"""Scoring one replayed item.

Three of the five scorers are exact comparisons and cost nothing. The fourth
needs a model, and it runs on REFLEX - the metered Gemini route - because every
other tier is a subscription proxy sharing a max_budget of 20 per 30 days with
Noah's own work (eval design 6.1).

    ponytail: REFLEX-tier judge, a weak model on a narrow question. If the
    spot-check agreement in `eve-eval run`'s output falls below ~85%, move the
    tier below to DEEP and accept the budget cost.
"""

from __future__ import annotations

import logging

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, ValidationError

from eve.eval.types import DatasetItem
from eve.models import Tier, get_model

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """You are checking whether one assertion holds of one response.

Answer only about the assertion as written. Do not reward a response for being
helpful, polite, or well-phrased if the assertion does not mention it. Do not
penalise it for anything the assertion does not mention.

## Assertion
{assertion}

## Response
{response}

Does the assertion hold? Give one sentence of reasoning."""


class Judgement(BaseModel):
    passed: bool = False
    why: str = Field(default="", description="One sentence of reasoning.")


async def judge_assertion(assertion: str, response: str) -> Judgement:
    """A malformed structured-output response is a FAIL, not a crash - the same
    posture eve_ambient/filter.py takes for the same reason: retrying a
    response that will never come back different costs the same outage twice."""
    try:
        model = get_model(Tier.REFLEX).with_structured_output(Judgement)
        result = await model.ainvoke(
            [HumanMessage(_JUDGE_PROMPT.format(assertion=assertion, response=response))]
        )
    except (ValidationError, ValueError, OutputParserException) as exc:
        logger.warning("judge returned an unusable response: %s", exc)
        return Judgement(passed=False, why="judge response malformed")
    except Exception:
        logger.warning("judge call failed", exc_info=True)
        return Judgement(passed=False, why="judge unavailable")
    if not isinstance(result, Judgement):
        return Judgement(passed=False, why="judge response malformed")
    return result


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def score_ambient(items: list[DatasetItem], results: dict[str, dict]) -> dict:
    """Exact comparison against the recorded verdict, plus reply precision.

    `notify_agreement` is a CONSISTENCY scorer, not an accuracy one: it
    compares Eve to her own past self, which is what makes it useful for
    detecting a prompt edit or a model retier and useless for "is the filter
    good". The CLI labels it accordingly.
    """
    agree = exact_audience = comparable = 0
    replied = sent = 0
    for item in items:
        result = results.get(item.id) or {}
        if result.get("error"):
            # Excluded, not counted as disagreement: an unavailable model call
            # is not a behaviour change.
            continue
        comparable += 1
        if result.get("notify") == item.expected["notify"]:
            agree += 1
        if sorted(result.get("audience") or []) == sorted(item.expected["audience"]):
            exact_audience += 1
        if item.expected["notify"] and item.expected.get("notices"):
            sent += 1
            if item.expected.get("replied"):
                replied += 1

    scores = {
        "notify_agreement": _pct(agree, comparable),
        "audience_exact": _pct(exact_audience, comparable),
        "comparable_items": comparable,
    }
    # Omitted rather than reported as 0.0 when nothing is labelled: a 0% would
    # read as a regression in the gate rather than as an absence of data.
    if sent:
        scores["notify_precision"] = _pct(replied, sent)
    return scores


def score_turns(items: list[DatasetItem], judged: dict[str, list[Judgement]]) -> dict:
    """Fraction of assertions the judge marked satisfied, plus the canary.

    An item whose replay errored (an unknown member sub, a graph failure)
    carries an empty verdict list here, the same convention `_cmd_run` uses
    for it. An empty list contributes zero to both `total` and `passed`, so
    the item is excluded from `assertion_pass` rather than counted as a
    failure - mirroring how `score_ambient` excludes `{"error": True}`
    results from `comparable`. A canary with no verdicts is treated as not
    passed, the safe default: the gate only fails on `canary_passed is True`.
    """
    passed = total = 0
    canary_passed = False
    for item in items:
        verdicts = judged.get(item.id) or []
        if item.canary:
            # A canary passing means the judge is rubber-stamping. Kept out of
            # assertion_pass so it cannot mask a real regression.
            canary_passed = all(v.passed for v in verdicts) if verdicts else False
            continue
        total += len(verdicts)
        passed += sum(1 for v in verdicts if v.passed)
    return {
        "assertion_pass": _pct(passed, total),
        "assertions": total,
        "canary_passed": canary_passed,
    }


def rule_delta(with_rules: dict, without_rules: dict) -> float:
    """The number that justifies Phase 5a. Positive: self-authoring is
    working. Flat: it is costing prompt budget for nothing. Negative: the rule
    set has turned on itself."""
    return round(
        with_rules.get("assertion_pass", 0.0)
        - without_rules.get("assertion_pass", 0.0),
        1,
    )
