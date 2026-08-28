"""Rule-set hygiene: redundant, conflicting, or dormant.

Operates on Eve's own rows rather than on model behaviour, so it is cheap and
checkable. It does NOT judge whether a rule is good, and it is not the
reflection loop deferred in Phase 5a - it never authors anything, and Eve
authoring rules about her own authoring is out of scope for the program
(eval design 8.1).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from eve.memory.store import supersede
from eve.memory.types import Memory
from eve.models import Tier, get_model

logger = logging.getLogger(__name__)


class Contradictions(BaseModel):
    conflicts: list[str] = Field(default_factory=list)


_CONTRADICTION_PROMPT = """Below are standing instructions an assistant wrote
for herself. Report ONLY pairs that genuinely contradict - where following one
means failing the other. Two rules about different topics are not a conflict,
and neither are two rules that merely overlap.

Most rule sets contain NO contradictions. An empty list is the correct and
common answer.

{rules}"""


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return round(dot, 4)


def find_duplicates(
    pairs: list[tuple[Memory, list[float]]], threshold: float = 0.95
) -> list[tuple[Memory, Memory, float]]:
    """(keeper, loser, score) for each near-identical pair in one scope.

    Auto-applicable because a vector comparison can make this claim without a
    model, and the loser is superseded rather than deleted, so a wrong merge is
    recoverable from the superseded_by chain.
    """
    found = []
    for i, (left, left_vec) in enumerate(pairs):
        for right, right_vec in pairs[i + 1 :]:
            if left.scope_id != right.scope_id:
                continue
            score = _cosine(left_vec, right_vec)
            if score < threshold:
                continue
            keeper, loser = (
                (left, right) if left.salience >= right.salience else (right, left)
            )
            found.append((keeper, loser, score))
    return found


def find_dead(rules: list[Memory], days: int) -> list[Memory]:
    """Rules whose last_seen_at has not moved inside the window. Report only:
    a dormant rule may simply cover a rare situation."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return [rule for rule in rules if rule.last_seen_at < cutoff]


async def report_contradictions(rules: list[Memory]) -> list[str]:
    """Report only. Never applied - see the module docstring."""
    if len(rules) < 2:
        return []
    rendered = "\n".join(f"- {rule.content}" for rule in rules)
    model = get_model(Tier.REFLEX).with_structured_output(Contradictions)
    try:
        result = await model.ainvoke(
            [HumanMessage(_CONTRADICTION_PROMPT.format(rules=rendered))]
        )
    except Exception:
        logger.warning("contradiction check failed", exc_info=True)
        return []
    return list(getattr(result, "conflicts", []) or [])


async def apply_duplicates(pairs: list[tuple[Memory, Memory, float]]) -> int:
    """Supersede each loser by its keeper. Returns how many were retired."""
    applied = 0
    for keeper, loser, score in pairs:
        await supersede(loser.id, keeper.id, f"duplicate of {keeper.id} (cosine {score})")
        applied += 1
    return applied
