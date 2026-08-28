from datetime import UTC, datetime, timedelta

import pytest

from eve.memory.types import Memory


def _rule(rid, content, salience=0.5, days_old=0, embedding=None):
    now = datetime.now(UTC) - timedelta(days=days_old)
    memory = Memory(
        id=rid, layer="rule", scope_kind="member", scope_id="sub-noah",
        kind="preference", subject=None, content=content, confidence=0.8,
        salience=salience, created_at=now, last_seen_at=now,
    )
    return memory, (embedding or [1.0] + [0.0] * 1535)


def test_find_duplicates_pairs_near_identical_rules():
    from eve.eval.hygiene import find_duplicates

    a, va = _rule("a", "Lead with the number.", salience=0.9)
    b, vb = _rule("b", "Give the number first.", salience=0.3)
    pairs = find_duplicates([(a, va), (b, vb)], threshold=0.95)

    assert len(pairs) == 1
    keeper, loser, _score = pairs[0]
    assert keeper.id == "a" and loser.id == "b"


def test_find_duplicates_ignores_dissimilar_rules():
    from eve.eval.hygiene import find_duplicates

    a, va = _rule("a", "Lead with the number.")
    b, vb = _rule("b", "Never text at dinner.", embedding=[0.0] * 1535 + [1.0])

    assert find_duplicates([(a, va), (b, vb)], threshold=0.95) == []


def test_find_dead_uses_the_dormancy_window():
    from eve.eval.hygiene import find_dead

    fresh, _ = _rule("a", "Recent.", days_old=1)
    stale, _ = _rule("b", "Dormant.", days_old=200)

    assert [r.id for r in find_dead([fresh, stale], days=90)] == ["b"]


async def test_apply_duplicates_supersedes_the_loser(monkeypatch):
    from eve.eval import hygiene as hygiene_mod

    calls = []

    async def supersede(old, new, why):
        calls.append((old, new, why))

    monkeypatch.setattr(hygiene_mod, "supersede", supersede)
    keeper, _ = _rule("a", "Lead with the number.", salience=0.9)
    loser, _ = _rule("b", "Give the number first.", salience=0.3)

    assert await hygiene_mod.apply_duplicates([(keeper, loser, 0.99)]) == 1
    assert calls == [("b", "a", "duplicate of a (cosine 0.99)")]


async def test_contradictions_are_never_auto_applied(monkeypatch):
    """Resolving a conflict means choosing what the family wants."""
    from eve.eval import hygiene as hygiene_mod

    async def supersede(old, new, why):
        raise AssertionError("a contradiction must never be auto-resolved")

    monkeypatch.setattr(hygiene_mod, "supersede", supersede)

    class FakeModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            from eve.eval.hygiene import Contradictions

            return Contradictions(conflicts=["'Be brief' vs 'Explain fully'"])

    monkeypatch.setattr(hygiene_mod, "get_model", lambda tier: FakeModel())
    a, _ = _rule("a", "Be brief.")
    b, _ = _rule("b", "Explain fully.")

    found = await hygiene_mod.report_contradictions([a, b])
    assert found == ["'Be brief' vs 'Explain fully'"]
