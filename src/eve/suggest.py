"""Reply suggestions: 2-4 things the MEMBER might say next.

One REFLEX-tier structured-output call after Eve's answer has streamed. Not
part of Eve's own turn - see ADR 0013 and the design doc section 2 for why
folding chips into the VOICE call was rejected.

Every failure degrades to no chips. A member must never lose a reply, and a
turn must never hang, because chip generation had a bad day.
"""

from __future__ import annotations

import asyncio
import logging
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
from eve.state import EveState

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


def _emit(chips: list[str], span) -> dict:
    """The ONE exit. Two delivery paths - the `custom` stream frame the
    Flutter client consumes, and the state channel stock SDK clients and
    `GET /threads/{id}/state` read - so they must be written in one place or
    they will eventually disagree.

    `get_stream_writer()` is called unconditionally: it defaults to
    `_no_op_stream_writer` (langgraph/runtime.py:206), so this is inert under
    `ainvoke` with no `custom` stream mode.
    """
    span.set_attribute("eve.suggest.count", len(chips))
    try:
        get_stream_writer()({"suggestions": chips})
    except Exception:
        # Delivering chips must not be able to fail a turn. A writer that
        # raises (no runtime context, a future langgraph change) still leaves
        # the state channel written below.
        logger.warning("could not emit the suggestions frame", exc_info=True)
    return {"suggestions": chips}


async def suggest(state: EveState, config: RunnableConfig) -> dict:
    """Chips for the turn that just finished. Never raises; every failure is
    an empty list."""
    with _tracer.start_as_current_span("eve.suggest") as span:
        human, ai = last_exchange(state["messages"])
        member = state["member"]

        try:
            model = get_model(Tier.REFLEX).with_structured_output(Suggestions)
            prompt = _render(member, human, ai, state.get("memory"))
        except Exception:
            logger.warning("suggestions could not be prepared", exc_info=True)
            span.set_attribute("eve.suggest.outcome", "error")
            return _emit([], span)

        try:
            async with asyncio.timeout(_budget_seconds()):
                result = await model.with_config(tags=[TAG_NOSTREAM]).ainvoke(
                    [HumanMessage(prompt)]
                )
        except TimeoutError:
            # Bounded exposure, not a bug: the run does not stay open for a
            # slow REFLEX call. If this fires often, the budget is too tight
            # or the tier is too slow - `eve.suggest.outcome` is how that
            # becomes visible rather than folklore.
            span.set_attribute("eve.suggest.outcome", "budget")
            return _emit([], span)
        except (ValidationError, ValueError, OutputParserException) as exc:
            # The call succeeded; what came back cannot be used. Same
            # category, and the same three exception types, as
            # eve_ambient/filter.py's malformed branch.
            logger.warning("suggestions came back unusable: %s", exc)
            span.set_attribute("eve.suggest.outcome", "malformed")
            return _emit([], span)
        except Exception:
            logger.warning("suggestions failed", exc_info=True)
            span.set_attribute("eve.suggest.outcome", "error")
            return _emit([], span)

        chips = clean(getattr(result, "suggestions", None))
        span.set_attribute("eve.suggest.outcome", "ok" if chips else "empty")
        return _emit(chips, span)
