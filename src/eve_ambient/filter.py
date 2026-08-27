"""The REFLEX-tier relevance gate: is this worth interrupting anyone over?

Structured output through the same mechanism memory/extract.py uses. Three
outcomes, not two, and they must not collapse into one another:

- a genuine verdict — including a genuine "not worth interrupting anyone
  over" — resolves the signal and is marked seen;
- the call itself failing (connection, timeout, HTTP error: anything
  transient) means the filter could not decide at all, so `judge` raises
  `FilterError` and the caller leaves the signal unseen so the next poll
  retries it, exactly as for a `notify.DeliveryError` (fix round 1, item 2);
- the call succeeding but returning something that does not fit
  `FilterVerdict` — a malformed structured-output response — is a
  deterministic dead end, not a retry candidate: retrying costs the same
  outage-shaped bug (a model-provider outage never resolving) except now for
  a request that will never come back different. `judge` resolves this case
  itself, as `FilterVerdict(notify=False, why="filter response malformed")`,
  rather than raising (fix round 2, item 2).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from eve.family import get_family
from eve.memory.store import load_always_on
from eve.models import Tier, get_model
from eve.settings import get_settings
from eve_ambient.types import FilterVerdict, Signal

logger = logging.getLogger(__name__)

_PAYLOAD_CHARS = 800
_MALFORMED = "filter response malformed"


class FilterError(Exception):
    """The REFLEX call itself could not be completed — a couldn't-decide,
    not a decided-no, and not a malformed-response dead end either.
    Distinguishable from both so the pipeline can treat only this one like a
    `notify.DeliveryError`: leave the signal unseen and let the next poll
    retry. A malformed response does not raise this — see `judge`."""


@lru_cache(maxsize=1)
def load_filter_prompt() -> str:
    return (get_settings().prompt_file.parent / "ambient_filter.md").read_text()


async def _household_context() -> str:
    """Household memory only. Profile memory is deliberately not read: the
    audience is not known yet, and the compose turn does full recall anyway
    (design section 5)."""
    try:
        # Four values since Phase 5a. Rules are deliberately not requested:
        # the filter decides whether to interrupt, and Eve's notes on how to
        # phrase things do not bear on that.
        _profile, household, _digest, _rules = await load_always_on("", None)
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
    except Exception as exc:
        logger.warning("ambient filter failed for %s", signal.key, exc_info=True)
        raise FilterError(f"the filter could not judge {signal.key}: {exc}") from exc

    try:
        verdict = await model.ainvoke([HumanMessage(prompt)])
    except (ValidationError, ValueError, OutputParserException) as exc:
        # The call succeeded; what came back does not fit FilterVerdict. A
        # deterministic dead end, not a retry candidate (fix round 2, item
        # 2; ValueError and OutputParserException added in the final round):
        # raising FilterError here would defer this signal forever, since a
        # malformed response does not spontaneously become a well-formed one.
        # `with_structured_output`'s parser raises `ValueError` ("tool
        # arguments must be specified as a dict") and `OutputParserException`
        # (unknown tool type) for exactly this shape of failure — a response
        # that arrived and cannot be used, the same category as a
        # ValidationError, not an infrastructure failure.
        logger.warning(
            "ambient filter returned an unusable response for %s: %s",
            signal.key, exc,
        )
        return FilterVerdict(notify=False, why=_MALFORMED)
    except Exception as exc:
        logger.warning("ambient filter failed for %s", signal.key, exc_info=True)
        raise FilterError(f"the filter could not judge {signal.key}: {exc}") from exc

    if not isinstance(verdict, FilterVerdict):
        # Belt-and-braces: `with_structured_output` is contracted to return
        # a FilterVerdict, but a provider/langchain change that silently
        # returns some other shape (a dict, say) must be handled explicitly
        # rather than left to raise an AttributeError, unhandled, straight
        # past the pipeline's `except FilterError` (fix round 2, item 2, the
        # related gap the reviewer noted).
        logger.warning(
            "ambient filter returned %s instead of a FilterVerdict for %s: %r",
            type(verdict).__name__, signal.key, verdict,
        )
        return FilterVerdict(notify=False, why=_MALFORMED)

    logger.info(
        "ambient filter verdict source=%s key=%s notify=%s urgent=%s why=%s",
        signal.source, signal.key, verdict.notify, verdict.urgent, verdict.why,
    )
    return verdict
