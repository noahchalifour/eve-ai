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


def _replace_dynamic_tools(
    _old: list[DynamicToolSpec], new: list[DynamicToolSpec]
) -> list[DynamicToolSpec]:
    """Last-write-wins. A reducer is what gives a channel a default: without
    one LangGraph uses `LastValue`, which holds no value at all until
    something writes it, so on a fresh thread the key is simply absent from
    state and every tool taking `Annotated[EveState, InjectedState]` fails
    pydantic validation of the injected state before its body ever runs.
    Not `operator.add`: `search_skills` already merges against the existing
    list and caps it, then returns the whole new list."""
    return new


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
    dynamic_tools: Annotated[list[DynamicToolSpec], _replace_dynamic_tools]
