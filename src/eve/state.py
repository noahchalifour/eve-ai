"""Graph state. `permissions` is resolved in Phase 1 and consumed in Phase 3;
carrying it now means the tools loop does not reshape state when it lands."""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

from eve.memory.types import MemoryBundle
from eve.settings import get_settings
from eve.skills.types import DynamicToolSpec

# Phase 4 prefixes an ambient signal's composed human message with this so the
# model knows the member did not say it (eve_ambient/notify.py). Phase 5a
# reuses it as the authoring guard: a turn that cannot be attributed to a
# member speaking authors no rule and no procedure (design doc section 6.2).
#
# One owner for the literal, deliberately. A guard that matches a string
# another module builds by hand is a guard that silently stops matching.
AMBIENT_MARKER_PREFIX = "[ambient signal — not spoken by"


def ambient_marker(name: str) -> str:
    return f"{AMBIENT_MARKER_PREFIX} {name}]"


def is_ambient_text(text: str) -> bool:
    """True when this message was composed by the ambient pipeline rather than
    typed by a family member. Fails CLOSED for the ambiguous case: anything
    carrying the marker is treated as untrusted input."""
    return text.lstrip().startswith(AMBIENT_MARKER_PREFIX)


def may_author(human: str) -> bool:
    """True if a turn whose last human message is `human` may author a rule or
    a procedure.

    One predicate, two callers - eve.memory.extract's passive rule pass and
    eve.skills.authoring's deliberate write_skill tool. Two copies of this
    boolean would be two guards to keep in step, and the ambient pipeline
    drives the same graph both paths hang off (eve_ambient/notify.py embeds
    raw signal payload into a marked human message), so a guard that covers
    only one path leaves the other reachable by attacker-controlled text.

    Fails CLOSED: an ambient-marked turn authors nothing, regardless of the
    setting (design doc section 6.2).
    """
    return get_settings().self_authoring_enabled and not is_ambient_text(human)


def _last_write_wins(_old: list, new: list) -> list:
    """Last-write-wins, shared by `dynamic_tools` and `suggestions`.

    A reducer is what gives a channel a default: without one LangGraph uses
    `LastValue`, which holds no value at all until something writes it, so on
    a fresh thread the key is simply absent from state and every tool taking
    `Annotated[EveState, InjectedState]` fails pydantic validation of the
    injected state before its body ever runs.

    Not `operator.add`, for both channels and for different reasons.
    `search_skills` already merges against the existing list and caps it, then
    returns the whole new list. `suggestions` describes ONE turn: appending
    would accumulate every turn's chips and a client would render
    continuations of a conversation that has moved on.
    """
    return new


# Owned here rather than in graph.py because `suggest` must recognise this
# reply to skip chip generation for it, and graph.py imports `suggest` - the
# same one-owner-for-a-shared-literal reason as AMBIENT_MARKER_PREFIX above.
LOOP_EXHAUSTED = (
    "I wasn't able to finish that - I kept going back and forth with my tools "
    "without getting anywhere. Could you try asking me a different way?"
)


class MemberContext(TypedDict):
    sub: str
    name: str
    role: str
    timezone: str
    permissions: list[str]
    local_time: str


class EveState(TypedDict):
    messages: Annotated[list, add_messages]
    member: MemberContext
    system_prompt: str
    # Written by `recall`, rendered into the system prompt by `eve` after
    # recall completes, and read by `extract`. Phase 3's tools loop reads it too.
    # `| None` because InjectedState's pydantic schema validates this field
    # strictly (unlike a plain TypedDict at runtime): a specialist tool invoked
    # before `recall` populates it - or in a test - must be able to pass `None`.
    memory: MemoryBundle | None
    # Specs only - see eve.skills.types.DynamicToolSpec. Materialized into
    # real callables fresh on every model call (eve.skills.materialize,
    # Task 11), never stored as one.
    dynamic_tools: Annotated[list[DynamicToolSpec], _last_write_wins]
    # Written by `suggest` (eve/suggest.py) after the answer has streamed;
    # read by any client on `stream_mode="values"`/`"updates"` or from
    # `GET /threads/{id}/state`. The same list also goes out on the `custom`
    # stream channel, which is what the Flutter client actually consumes -
    # see the design doc section 6.
    #
    # ALWAYS written, including as `[]`: a turn that skips chip generation
    # must clear the previous turn's chips rather than leave them standing.
    suggestions: Annotated[list[str], _last_write_wins]
