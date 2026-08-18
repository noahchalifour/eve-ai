import math
from datetime import UTC, datetime, timedelta

from eve.memory.ranking import (
    estimate_tokens,
    fit_budget,
    fuse,
    recency_decay,
)
from eve.memory.types import Memory


def _mem(mid: str, content: str = "x" * 40) -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id=mid,
        layer="episodic",
        scope_kind="member",
        scope_id="sub-noah",
        kind="event",
        subject=None,
        content=content,
        confidence=0.7,
        salience=0.5,
        created_at=now,
        last_seen_at=now,
    )


def test_decay_is_one_half_at_the_half_life():
    assert math.isclose(recency_decay(90.0, 90.0), 0.5, rel_tol=1e-6)


def test_decay_is_one_for_something_recorded_now():
    assert recency_decay(0.0, 90.0) == 1.0


def test_decay_never_reaches_zero():
    """A fact from two years ago is faint, not gone. Clamping to zero would
    make old memories unrecallable even when nothing else matches."""
    assert recency_decay(730.0, 90.0) > 0.0


def test_fusion_prefers_what_both_arms_agree_on():
    """The whole point of hybrid recall: an item both arms found should beat
    an item only one arm found, even when that one arm ranked it first."""
    lexical = ["a", "b"]
    vector = ["c", "b"]
    assert fuse(lexical, vector)[0] == "b"


def test_fusion_keeps_items_only_one_arm_found():
    """Each arm covers the other's blind spot; dropping singletons would
    throw away exactly the coverage hybrid recall exists to buy."""
    assert set(fuse(["a"], ["b"])) == {"a", "b"}


def test_fusion_of_one_arm_preserves_its_order():
    assert fuse(["a", "b", "c"]) == ["a", "b", "c"]


def test_token_estimate_is_four_characters():
    assert estimate_tokens("x" * 40) == 10


def test_budget_drops_from_the_end():
    """Items arrive ranked, so the tail is the least relevant. Dropping the
    head would silently discard the best match whenever the budget bites."""
    items = [_mem("a"), _mem("b"), _mem("c")]  # 10 tokens each
    assert [m.id for m in fit_budget(items, 25)] == ["a", "b"]


def test_budget_never_returns_nothing_when_it_has_something():
    """A single item longer than the whole budget still beats an empty
    memory section, which reads to the model as 'she knows nothing'."""
    assert len(fit_budget([_mem("a", "x" * 10_000)], 10)) == 1
