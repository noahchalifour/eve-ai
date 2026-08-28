import pytest


def test_build_turns_reads_the_golden_file():
    from eve.eval.datasets import build_turns

    items = build_turns("tests/eval/turns.yaml")
    assert items, "the golden file must not be empty"
    first = items[0]
    assert first.shape == "turns"
    assert first.input["message"]
    assert first.expected["expects"]


def test_the_golden_file_has_exactly_one_canary():
    """A run where the canary passes means the judge is rubber-stamping. This
    is the only guard the harness has against itself."""
    from eve.eval.datasets import build_turns

    canaries = [i for i in build_turns("tests/eval/turns.yaml") if i.canary]
    assert len(canaries) == 1


def test_every_golden_file_member_resolves_in_the_real_roster():
    """replay_turn calls the real graph, which resolves `member` through
    get_family().get(identity) and raises UnknownMemberError on anything
    not in family.yaml. A placeholder sub (e.g. "sub-noah") here would crash
    `eve-eval run` on its very first item in every environment - this test
    exists so that class of bug fails loudly in the unit suite instead."""
    from eve.eval.datasets import build_turns
    from eve.family import get_family

    family = get_family()
    for item in build_turns("tests/eval/turns.yaml"):
        family.get(item.input["member"])  # raises UnknownMemberError if missing


def test_build_ambient_shapes_a_decision_row():
    from datetime import UTC, datetime

    from eve.eval.datasets import ambient_items_from_rows

    rows = [
        {
            "id": "d1",
            "source": "mail",
            "key": "k1",
            "signal": {
                "source": "mail", "key": "k1",
                "occurred_at": "2026-08-27T00:00:00+00:00",
                "member_sub": "sub-noah", "summary": "A package shipped.",
                "payload": {}, "cooldown_hours": None,
            },
            "verdict": {"notify": True, "audience": ["sub-noah"], "urgent": False, "why": "w"},
            "decided_at": datetime(2026, 8, 27, tzinfo=UTC),
            "replied": True,
            "notices": 1,
        }
    ]
    items = ambient_items_from_rows(rows)

    assert len(items) == 1
    assert items[0].shape == "ambient"
    assert items[0].expected["notify"] is True
    assert items[0].expected["replied"] is True


def test_build_ambient_excludes_a_row_whose_signal_will_not_rehydrate():
    """A malformed jsonb blob must be skipped, not crash the build."""
    from eve.eval.datasets import ambient_items_from_rows

    rows = [{"id": "d1", "source": "m", "key": "k", "signal": {},
             "verdict": {"notify": False}, "decided_at": None,
             "replied": False, "notices": 0}]
    assert ambient_items_from_rows(rows) == []


def test_no_production_module_imports_the_harness():
    """The harness imports Eve; Eve never imports the harness. Otherwise a
    bug in the eval package can fail a family member's turn."""
    import pathlib

    offenders = []
    for path in pathlib.Path("src").rglob("*.py"):
        if "eve/eval" in str(path).replace("\\", "/"):
            continue
        text = path.read_text()
        if "eve.eval" in text or "from eve import eval" in text:
            offenders.append(str(path))
    assert offenders == [], offenders
