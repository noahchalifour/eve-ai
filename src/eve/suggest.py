"""Reply suggestions: 2-4 things the MEMBER might say next.

One REFLEX-tier structured-output call after Eve's answer has streamed. Not
part of Eve's own turn - see ADR 0013 and the design doc section 2 for why
folding chips into the VOICE call was rejected.

Every failure degrades to no chips. A member must never lose a reply, and a
turn must never hang, because chip generation had a bad day.

This module owns BOTH chip flavours, because they differ only in the prompt
and in what they are given to read:

- `suggest` - continuations, after an exchange. The default path.
- `openers` - the first thing to say, on a thread with no exchange at all,
  requested by a client showing an empty chat. See ADR 0018.

They share `clean`, `_emit` and the budget/failure discipline deliberately. A
chip is rendered verbatim by a client either way, so a second copy of that
validation would be a second thing to keep in step.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from functools import lru_cache

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.constants import TAG_NOSTREAM
from opentelemetry import trace
from pydantic import BaseModel, Field, ValidationError

from eve.memory.extract import last_exchange
from eve.memory.types import MemoryBundle
from eve.models import Tier, get_model
from eve.settings import get_settings
from eve.state import LOOP_EXHAUSTED, EveState, is_ambient_text

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer("eve.suggest")

MAX_SUGGESTIONS = 4
# Rendered verbatim in a pill. Anything longer is a paragraph, and truncating
# mid-word would put words in the member's mouth.
MAX_CHARS = 80


class Suggestions(BaseModel):
    """The REFLEX model's structured output.

    `default_factory` matters: the prompt licenses an empty list for a
    finished conversation, and a required field would make that answer a
    validation failure indistinguishable from a broken response.
    """

    suggestions: list[str] = Field(
        default_factory=list,
        description="2-4 short first-person things the member might say next.",
    )


def clean(raw: object) -> list[str]:
    """Validate hard: chips are rendered verbatim by a client.

    Takes `object`, not `list[str]`, on purpose. `with_structured_output` is
    contracted to return a `Suggestions`, but a provider or langchain change
    that returns a bare dict or a string must produce no chips rather than an
    AttributeError escaping into the graph.
    """
    if not isinstance(raw, list):
        return []
    kept: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or len(text) > MAX_CHARS:
            continue
        kept.append(text)
        if len(kept) == MAX_SUGGESTIONS:
            break
    return kept


@lru_cache(maxsize=1)
def load_suggest_prompt() -> str:
    return (get_settings().prompt_file.parent / "suggest.md").read_text()


@lru_cache(maxsize=1)
def load_openers_prompt() -> str:
    return (get_settings().prompt_file.parent / "openers.md").read_text()


def _budget_seconds() -> float:
    return get_settings().suggest_budget_ms / 1000.0


def _render_memory(memory: MemoryBundle | None) -> str:
    """Profile and rules only. Household and episodic are what Eve needs to
    ANSWER; what shapes a plausible member utterance is who they are and how
    they like to be talked to. Keeping this narrow also keeps a REFLEX prompt
    short."""
    if not memory:
        return "(nothing recorded)"
    lines = [f"- {m.content}" for m in (*memory["profile"], *memory["rules"])]
    return "\n".join(lines) if lines else "(nothing recorded)"


def _render(member: dict, human: str, ai: str, memory: MemoryBundle | None) -> str:
    return (
        f"{load_suggest_prompt()}\n\n"
        f"## Who is talking to Eve\n"
        f"{member['name']} ({member['role']}), local time {member['local_time']}\n\n"
        f"## What Eve knows about them\n{_render_memory(memory)}\n\n"
        f"## The exchange\n{member['name']}: {human}\nEve: {ai}\n"
    )


def _render_openers(member: dict, memory: MemoryBundle | None) -> str:
    """The same header as `_render`, and deliberately NO exchange section.

    An empty "## The exchange" would be worse than omitting it: a REFLEX model
    reading `Noah:` followed by nothing tends to fill the blank in, which is
    exactly the continuation-shaped chip openers must not produce.
    """
    return (
        f"{load_openers_prompt()}\n\n"
        f"## Who is opening a chat with Eve\n"
        f"{member['name']} ({member['role']}), local time {member['local_time']}\n\n"
        f"## What Eve knows about them\n{_render_memory(memory)}\n"
    )


def _emit(chips: list[str], span: trace.Span) -> dict:
    """The ONE exit. Two delivery paths - the `custom` stream frame the
    Flutter client consumes, and the state channel stock SDK clients and
    `GET /threads/{id}/state` read - so they must be written in one place or
    they will eventually disagree.

    `get_stream_writer()` is called unconditionally. Inside a graph node it
    defaults to `_no_op_stream_writer` (langgraph/runtime.py:206) when there
    is no `custom` stream consumer, so this is inert there. Called outside a
    runnable context - which is exactly what a direct `await suggest(...)` in
    a test does - `get_stream_writer()` raises `RuntimeError`, which is
    caught below and logged quietly rather than as a warning.

    The two `RuntimeError`s are NOT the same failure and are caught
    separately: `get_stream_writer()` raising means there is no runnable
    context (expected, benign - e.g. a direct call in a test). The writer it
    returns raising - for any exception, including a second `RuntimeError`
    from something like a closed Aegra queue - means delivery itself broke,
    which is worth a warning.
    """
    span.set_attribute("eve.suggest.count", len(chips))
    try:
        writer = get_stream_writer()
    except RuntimeError:
        # No runnable context (e.g. a direct call in a test). Expected, not
        # a bug - the state channel is still written below.
        writer = None
        logger.debug("no runnable context to emit the suggestions frame")
    if writer is not None:
        try:
            writer({"suggestions": chips})
        except Exception:
            # Delivering chips must not be able to fail a turn. A writer that
            # raises (e.g. a closed Aegra queue) still leaves the state
            # channel written below.
            logger.warning("could not emit the suggestions frame", exc_info=True)
    return {"suggestions": chips}


async def _within_budget(
    build_prompt: Callable[[], str], span: trace.Span, metric: str
) -> Suggestions | None:
    """One bounded REFLEX call, or `None` for every way it can fail.

    Shared by `suggest` and `openers` so the two chip flavours cannot drift on
    the thing that actually matters here - that a bad day for chip generation
    is invisible to the member rather than a hung or failed turn. `metric`
    names the span attribute prefix so the two remain separable in Langfuse.

    Prompt construction is a callable rather than a string because it can
    raise too (a missing prompt file, a `member` dict with no `name`), and
    that failure belongs in the same `error` bucket as the call itself.
    """
    try:
        model = get_model(Tier.REFLEX).with_structured_output(Suggestions)
        prompt = build_prompt()
    except Exception:
        logger.warning("suggestions could not be prepared", exc_info=True)
        span.set_attribute(f"{metric}.outcome", "error")
        return None

    started = time.perf_counter()

    def _record(outcome: str) -> None:
        span.set_attribute(
            f"{metric}.latency_ms", round((time.perf_counter() - started) * 1000, 1)
        )
        span.set_attribute(f"{metric}.outcome", outcome)

    try:
        async with asyncio.timeout(_budget_seconds()):
            return await model.with_config(tags=[TAG_NOSTREAM]).ainvoke(
                [HumanMessage(prompt)]
            )
    except TimeoutError:
        # Bounded exposure, not a bug: the run does not stay open for a
        # slow REFLEX call. If this fires often, the budget is too tight
        # or the tier is too slow - the outcome attribute is how that
        # becomes visible rather than folklore.
        _record("budget")
        return None
    except (ValidationError, ValueError, OutputParserException) as exc:
        # The call succeeded; what came back cannot be used. Same
        # category, and the same three exception types, as
        # eve_ambient/filter.py's malformed branch.
        logger.warning("suggestions came back unusable: %s", exc)
        _record("malformed")
        return None
    except Exception:
        logger.warning("suggestions failed", exc_info=True)
        _record("error")
        return None


async def suggest(state: EveState, config: RunnableConfig) -> dict:
    """Chips for the turn that just finished. Never raises; every failure is
    an empty list."""
    with _tracer.start_as_current_span("eve.suggest") as span:
        # `.get(...) or ...`, not `state[...]`: a run whose input omits
        # `member` or `messages` must degrade to no chips, not raise out of
        # the node before the span has recorded anything. An empty `member`
        # then KeyErrors inside `_render`, which the `try` below already
        # converts to `_emit([], span)`.
        human, ai = last_exchange(state.get("messages") or [])
        member = state.get("member") or {}

        # Ordered cheapest-first, and all before the model is even
        # constructed: each of these saves a REFLEX call, and the ambient one
        # fires on every household signal.
        if not get_settings().suggest_enabled:
            span.set_attribute("eve.suggest.outcome", "disabled")
            return _emit([], span)
        if not human or is_ambient_text(human):
            # No human message: nothing to continue. Ambient-marked: not a
            # member speaking, and the reply goes to ntfy, not a chat
            # surface. Both are "no member utterance here", so one branch.
            span.set_attribute("eve.suggest.outcome", "skipped")
            return _emit([], span)
        if ai == LOOP_EXHAUSTED:
            span.set_attribute("eve.suggest.outcome", "skipped")
            return _emit([], span)

        result = await _within_budget(
            lambda: _render(member, human, ai, state.get("memory")), span, "eve.suggest"
        )
        if result is None:
            return _emit([], span)
        chips = clean(getattr(result, "suggestions", None))
        span.set_attribute("eve.suggest.outcome", "ok" if chips else "empty")
        return _emit(chips, span)


def openers_requested(config: RunnableConfig | None) -> bool:
    """Whether the client asked for opening chips instead of an answer.

    `config.configurable`, NOT run metadata, for the same reason
    `eve.ui.stream.capabilities` uses it: LangGraph indexes run metadata and
    rejects anything but a scalar there. Aegra merges request config into the
    graph config verbatim, so this arrives unchanged.

    Strictly `is True`, not truthiness: this flag turns a normal turn into one
    that never calls VOICE and never answers, so a client sending a stray
    non-empty string must not silently mute Eve. Fails CLOSED to a normal turn.
    """
    configurable = (config or {}).get("configurable") or {}
    return configurable.get("suggestions_only") is True


async def openers(state: EveState, config: RunnableConfig) -> dict:
    """Chips for a chat with nothing in it yet - the first thing to say.

    Reached only when a client asks for it (`_route_after_context` in
    `graph.py`), and only when the thread carries no member utterance. Never
    raises; every failure is an empty list, exactly like `suggest`.

    Unlike `suggest` this is the WHOLE turn: no VOICE call, no answer, no
    message appended to the thread. That is the point - an empty chat asking
    for openers must not put words in Eve's mouth, and the run's only visible
    effect is the `suggestions` frame. See ADR 0018.
    """
    with _tracer.start_as_current_span("eve.openers") as span:
        member = state.get("member") or {}

        # Same cheapest-first ordering as `suggest`, and the same switch: a
        # deployment that turned chips off must not have them reappear on a
        # different route.
        if not get_settings().suggest_enabled:
            span.set_attribute("eve.openers.outcome", "disabled")
            return _emit([], span)

        # Openers are for an EMPTY chat. A thread that already has an exchange
        # gets continuations from `suggest`, and answering here would offer a
        # member who is mid-conversation four ways to start one.
        human, _ = last_exchange(state.get("messages") or [])
        if human:
            span.set_attribute("eve.openers.outcome", "skipped")
            return _emit([], span)

        result = await _within_budget(
            lambda: _render_openers(member, state.get("memory")), span, "eve.openers"
        )
        if result is None:
            return _emit([], span)
        chips = clean(getattr(result, "suggestions", None))
        span.set_attribute("eve.openers.outcome", "ok" if chips else "empty")
        return _emit(chips, span)
