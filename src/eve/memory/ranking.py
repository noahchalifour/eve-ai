"""Pure ranking maths. No I/O, no settings lookups - everything is an
argument, so every one of these is trivially testable and none of them can
surprise you at 3am."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from eve.memory.types import Memory


def recency_decay(age_days: float, half_life_days: float) -> float:
    """Exponential decay, computed at read time rather than cached.

    A `decayed_score` column refreshed nightly would be a cache of an
    expression cheaper to evaluate than to maintain, and it would be wrong
    for as long as the pod was down.
    """
    if half_life_days <= 0:
        return 1.0
    # ln(2) makes this an actual half-life: value is exactly 0.5 at
    # age_days == half_life_days. exp(-age/half_life) alone (the brief's
    # original formula) is a mean-lifetime/e-folding decay - it reaches
    # 1/e (~0.368), not 0.5, at that point. See task-45-report.md.
    return math.exp(-math.log(2) * max(age_days, 0.0) / half_life_days)


def fuse(*rankings: Sequence[str], k: int = 60) -> list[str]:
    """Reciprocal-rank fusion over ranked id lists.

    RRF rather than score normalisation: `ts_rank` and cosine similarity are
    on incomparable scales, and any attempt to map them onto each other is a
    tuning parameter nobody will ever revisit. Rank position is comparable by
    construction. k=60 is the value from the original TREC work; it flattens
    the difference between ranks 1 and 2 enough that agreement between arms
    matters more than either arm's confidence.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item_id in enumerate(ranking):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + position + 1)
    return sorted(scores, key=lambda i: (-scores[i], i))


def estimate_tokens(text: str) -> int:
    """Four characters per token.

    ponytail: a knob whose job is to stop the prompt growing without bound
    does not earn a tokeniser dependency. Being 15% wrong about a 1200-token
    budget changes nothing.
    """
    return len(text) // 4


def fit_budget(items: Iterable[Memory], budget: int) -> list[Memory]:
    """Take ranked items until the token budget is spent.

    Always returns at least one item when given one: an over-long single
    memory still beats an empty section, which the model reads as "she knows
    nothing about this" rather than "this did not fit."
    """
    kept: list[Memory] = []
    spent = 0
    for item in items:
        cost = estimate_tokens(item.content)
        if kept and spent + cost > budget:
            break
        kept.append(item)
        spent += cost
    return kept
