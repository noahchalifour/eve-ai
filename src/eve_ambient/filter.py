"""The REFLEX-tier relevance gate: is this worth interrupting anyone over?

Structured output through the same mechanism memory/extract.py uses. A
genuine verdict — "not worth interrupting anyone over" — and an infrastructure
failure are not the same thing, and must not collapse into one: the pipeline
marks a genuine verdict seen, but a failure means the filter could not decide
at all, so `judge` raises `FilterError` instead. The caller must leave the
signal unseen so the next poll retries it, exactly as it does for a
`notify.DeliveryError` (fix round 1, item 2 — a model-provider outage must
cost every signal in its window a retry, not the signal itself).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from langchain_core.messages import HumanMessage

from eve.family import get_family
from eve.memory.store import load_always_on
from eve.models import Tier, get_model
from eve.settings import get_settings
from eve_ambient.types import FilterVerdict, Signal

logger = logging.getLogger(__name__)

_PAYLOAD_CHARS = 800


class FilterError(Exception):
    """The REFLEX call could not be completed — a couldn't-decide, not a
    decided-no. Distinguishable from a genuine `FilterVerdict(notify=False)`
    so the pipeline can treat it like a `notify.DeliveryError`: leave the
    signal unseen and let the next poll retry."""


@lru_cache(maxsize=1)
def load_filter_prompt() -> str:
    return (get_settings().prompt_file.parent / "ambient_filter.md").read_text()


async def _household_context() -> str:
    """Household memory only. Profile memory is deliberately not read: the
    audience is not known yet, and the compose turn does full recall anyway
    (design section 5)."""
    try:
        _profile, household, _digest = await load_always_on("", None)
    except Exception:
        logger.warning("household memory unavailable to the filter", exc_info=True)
        return "(household memory unavailable)"
    if not household:
        return "(nothing recorded)"
    return "\n".join(f"- {memory.content}" for memory in household)


def _roster_block() -> str:
    return "\n".join(
        f"- {member.name} ({member.role}, {member.timezone}), sub={member.sub}"
        for member in get_family().members()
    )


def _render(signal: Signal, household: str) -> str:
    return (
        f"{load_filter_prompt()}\n\n"
        f"## The family\n{_roster_block()}\n\n"
        f"## Household memory\n{household}\n\n"
        f"## The signal\n"
        f"Source: {signal.source}\n"
        f"Occurred at: {signal.occurred_at.isoformat()}\n"
        f"Belongs to: {signal.member_sub or 'the household'}\n"
        f"Summary: {signal.summary}\n"
        f"Detail: {json.dumps(signal.payload, default=str)[:_PAYLOAD_CHARS]}\n"
    )


async def judge(signal: Signal) -> FilterVerdict:
    try:
        prompt = _render(signal, await _household_context())
        model = get_model(Tier.REFLEX).with_structured_output(FilterVerdict)
        verdict = await model.ainvoke([HumanMessage(prompt)])
    except Exception as exc:
        logger.warning("ambient filter failed for %s", signal.key, exc_info=True)
        raise FilterError(f"the filter could not judge {signal.key}: {exc}") from exc
    logger.info(
        "ambient filter verdict source=%s key=%s notify=%s urgent=%s why=%s",
        signal.source, signal.key, verdict.notify, verdict.urgent, verdict.why,
    )
    return verdict
