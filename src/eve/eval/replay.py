"""Running one dataset item through the real code path.

Real, not reconstructed: shape 1 calls eve_ambient.filter.judge and shape 2
invokes a compiled Eve graph. The one substitution is `extract`, replaced by a
no-op - an eval run that writes memory corrupts the behaviour it is measuring.
"""

from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage

from eve import context
from eve.eval.types import DatasetItem
from eve.graph import build_graph
from eve.models import get_model
from eve_ambient.filter import FilterError, judge
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

# Indirection so tests can substitute a fake without patching eve.models
# globally, which would also affect the judge in scorers.py.
_model_factory = get_model


def _signal_from(blob: dict) -> Signal:
    return Signal(
        source=blob["source"],
        key=blob["key"],
        occurred_at=datetime.fromisoformat(blob["occurred_at"]),
        member_sub=blob.get("member_sub"),
        summary=blob["summary"],
        payload=blob.get("payload") or {},
        cooldown_hours=blob.get("cooldown_hours"),
    )


async def replay_ambient(item: DatasetItem) -> dict:
    """Re-judge a recorded signal. A FilterError is reported, not raised: one
    unavailable model call must not abort a two-hundred-item run."""
    try:
        verdict = await judge(_signal_from(item.input["signal"]))
    except FilterError:
        logger.warning("replay: the filter could not judge %s", item.id)
        return {"error": True}
    except Exception:
        logger.warning("replay: %s failed", item.id, exc_info=True)
        return {"error": True}
    return {
        "notify": verdict.notify,
        "audience": list(verdict.audience),
        "urgent": verdict.urgent,
        "why": verdict.why,
        "error": False,
    }


async def _no_extract(state: dict, config) -> dict:
    """The one substitution. See the module docstring."""
    return {}


async def _no_suggest(state, config):
    """Eval replays score Eve's answer, not her chips. Same reason
    `_no_extract` exists: a replay must not pay for work nothing scores."""
    return {"suggestions": []}


async def replay_turn(item: DatasetItem, *, suppress_rules: bool) -> dict:
    """Invoke the real graph for one member message and return the final text.

    `suppress_rules` is threaded through build_system_prompt via a patched
    module attribute rather than a graph parameter: the graph builds its prompt
    internally and adding an arm parameter to EveState would make every tool
    taking InjectedState fail validation wherever the key is absent - the same
    failure mode eve/state.py's _last_write_wins exists to prevent.

    Mirrors replay_ambient's posture: any failure (an unknown `member` sub, a
    model outage, ...) is reported as `{"error": True}` rather than raised, so
    one bad item cannot abort a run of two hundred.
    """
    original = context.build_system_prompt

    def patched(persona, member, memory=None, **kwargs):
        return original(
            persona, member, memory, suppress_rules=suppress_rules
        )

    context.build_system_prompt = patched
    try:
        app = build_graph(
            model_factory=_model_factory,
            extract_fn=_no_extract,
            suggest_fn=_no_suggest,
        ).compile()
        result = await app.ainvoke(
            {"messages": [HumanMessage(item.input["message"])]},
            {
                "configurable": {
                    "langgraph_auth_user": {"identity": item.input["member"]},
                    "thread_id": f"eval-{item.id}",
                }
            },
        )
    except Exception:
        logger.warning("replay: %s failed", item.id, exc_info=True)
        return {"text": "", "error": True}
    finally:
        context.build_system_prompt = original

    for message in reversed(result["messages"]):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return {"text": str(message.content), "error": False}
    return {"text": "", "error": False}


def voice_call_estimate(items: list[DatasetItem], arms: int) -> int:
    """One VOICE call per turn item per arm. Printed before a run starts so
    nobody discovers the cost afterwards."""
    return len([i for i in items if i.shape in ("turns", "")]) * arms
