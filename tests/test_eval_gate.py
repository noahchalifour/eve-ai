import pytest


def _run(scores, arm="with-rules"):
    return {"arm": arm, "scores": scores, "item_count": 10, "git_sha": "abc"}


def test_a_first_run_passes(monkeypatch):
    """Nothing to compare against is not a regression."""
    from eve.eval import store as store_mod

    assert store_mod.evaluate_gate([_run({"notify_agreement": 50.0})], None) == (0, [])


def test_a_ten_point_agreement_drop_fails(monkeypatch):
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate(
        [_run({"notify_agreement": 60.0})], _run({"notify_agreement": 75.0})
    )
    assert code == 1
    assert any("notify_agreement" in r for r in reasons)


def test_a_small_agreement_drop_passes():
    """Ten points is above the noise floor of a nondeterministic replay."""
    from eve.eval import store as store_mod

    code, _ = store_mod.evaluate_gate(
        [_run({"notify_agreement": 71.0})], _run({"notify_agreement": 75.0})
    )
    assert code == 0


def test_exactly_ten_point_drop_passes():
    """Boundary check: exactly 10 points should pass (drop > points is False when drop == points)."""
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate(
        [_run({"notify_agreement": 65.0})], _run({"notify_agreement": 75.0})
    )
    assert code == 0
    assert not any("notify_agreement" in r for r in reasons)


def test_just_over_ten_point_drop_fails():
    """Boundary check: 10.1 points should fail (exceeds the > 10 threshold)."""
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate(
        [_run({"notify_agreement": 64.9})], _run({"notify_agreement": 75.0})
    )
    assert code == 1
    assert any("notify_agreement" in r for r in reasons)


def test_any_audience_drop_fails():
    """A member receiving a notification they lack the permission for is the
    failure Phase 4's definition of done treats as unacceptable."""
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate(
        [_run({"audience_exact": 99.0})], _run({"audience_exact": 100.0})
    )
    assert code == 1
    assert any("audience_exact" in r for r in reasons)


def test_a_negative_rule_delta_fails():
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate([_run({"rule_delta": -3.0})], None)
    assert code == 1
    assert any("rule_delta" in r for r in reasons)


def test_a_passing_canary_fails_the_gate():
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate([_run({"canary_passed": True})], None)
    assert code == 1
    assert any("canary" in r for r in reasons)


def test_an_empty_dataset_is_skipped_not_passed():
    """Shape 1 is empty until decisions accumulate. Reporting green would say
    'measured and fine' when nothing was measured."""
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate(
        [{"arm": "with-rules", "scores": {}, "item_count": 0, "git_sha": "a"}], None
    )
    assert code == 0
    assert any("skipped" in r for r in reasons)


async def test_a_publish_failure_never_raises(monkeypatch):
    """The expensive work is already done. Losing it to a reporting outage is
    absurd - the same posture extract takes."""
    from eve.eval import publish as publish_mod

    class Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("langfuse unreachable")

    monkeypatch.setattr(publish_mod, "_client", Boom)

    assert await publish_mod.publish_run("d", "with-rules", [], {}, {}) is False


async def test_publish_reports_success(monkeypatch):
    from eve.eval import publish as publish_mod

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def create_dataset(self, **kw):
            return None

        def create_dataset_item(self, **kw):
            return None

        def create_score(self, **kw):
            return None

        def flush(self):
            return None

    monkeypatch.setattr(publish_mod, "_client", FakeClient)

    assert await publish_mod.publish_run("d", "with-rules", [], {}, {"x": 1.0}) is True
