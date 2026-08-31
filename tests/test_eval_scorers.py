import pytest

from eve.eval.types import DatasetItem


def _ambient(item_id, notify, audience=(), replied=False, notices=0):
    return DatasetItem(
        id=item_id, shape="ambient", input={"signal": {}},
        expected={"notify": notify, "audience": list(audience),
                  "urgent": False, "replied": replied, "notices": notices},
    )


def test_notify_agreement_is_exact():
    from eve.eval.scorers import score_ambient

    items = [_ambient("a", True), _ambient("b", False)]
    results = {"a": {"notify": True, "audience": [], "urgent": False, "error": False},
               "b": {"notify": True, "audience": [], "urgent": False, "error": False}}
    scores = score_ambient(items, results)

    assert scores["notify_agreement"] == 50.0


def test_notify_precision_counts_only_sent_notifications():
    """Silence is not a usable negative label: 'your 3pm moved to 4pm' is a
    perfect notification nobody needs to answer. Precision is over the
    notify=true items that actually produced a notice."""
    from eve.eval.scorers import score_ambient

    items = [
        _ambient("a", True, replied=True, notices=1),
        _ambient("b", True, replied=False, notices=1),
        _ambient("c", False),
    ]
    results = {
        i.id: {"notify": i.expected["notify"], "audience": [], "urgent": False,
               "error": False}
        for i in items
    }
    scores = score_ambient(items, results)

    assert scores["notify_precision"] == 50.0


def test_precision_is_absent_with_no_labelled_notifications():
    """Reporting 0% when nothing is labelled would read as a regression."""
    from eve.eval.scorers import score_ambient

    items = [_ambient("c", False)]
    results = {"c": {"notify": False, "audience": [], "urgent": False, "error": False}}

    assert "notify_precision" not in score_ambient(items, results)


def test_audience_exact_requires_a_member_for_member_match():
    from eve.eval.scorers import score_ambient

    items = [_ambient("a", True, audience=["sub-noah"])]
    results = {"a": {"notify": True, "audience": ["sub-noah", "sub-kid"],
                     "urgent": False, "error": False}}

    assert score_ambient(items, results)["audience_exact"] == 0.0


def test_errored_items_are_excluded_not_counted_as_disagreement():
    from eve.eval.scorers import score_ambient

    items = [_ambient("a", True), _ambient("b", True)]
    results = {"a": {"notify": True, "audience": [], "urgent": False, "error": False},
               "b": {"error": True}}

    assert score_ambient(items, results)["notify_agreement"] == 100.0


def _turn(item_id, canary=False):
    return DatasetItem(
        id=item_id, shape="turns", input={}, expected={"expects": []}, canary=canary,
    )


def test_score_turns_excludes_an_errored_items_empty_verdict_list():
    """`_cmd_run` records `[]` for an item whose replay errored (the same
    convention as `replay_ambient`'s `{"error": True}`). An empty list must
    contribute nothing to assertion_pass rather than read as zero assertions
    passed out of zero, or worse, skew the percentage."""
    from eve.eval.scorers import Judgement, score_turns

    items = [_turn("a"), _turn("b")]
    judged = {
        "a": [Judgement(passed=True, why="ok"), Judgement(passed=True, why="ok")],
        "b": [],  # replay of "b" errored
    }
    scores = score_turns(items, judged)

    assert scores["assertion_pass"] == 100.0
    assert scores["assertions"] == 2


def test_score_turns_treats_an_errored_canary_as_not_passed():
    from eve.eval.scorers import score_turns

    items = [_turn("canary", canary=True)]
    scores = score_turns(items, {"canary": []})

    assert scores["canary_passed"] is False


async def test_judge_returns_a_boolean_and_a_reason(monkeypatch):
    from eve.eval import scorers as scorers_mod
    from eve.eval.scorers import Judgement

    class FakeModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        async def ainvoke(self, messages):
            return Judgement(passed=True, why="It leads with $42.")

    monkeypatch.setattr(scorers_mod, "get_model", lambda tier: FakeModel())
    out = await scorers_mod.judge_assertion("Leads with a number.", "You have $42.")

    assert out.passed is True and out.why


async def test_a_malformed_judge_response_is_a_fail_not_a_crash(monkeypatch):
    """Mirrors filter.py: a response that arrived and cannot be used is a
    deterministic dead end, not a retry candidate."""
    from pydantic import ValidationError

    from eve.eval import scorers as scorers_mod

    class FakeModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        async def ainvoke(self, messages):
            raise ValidationError.from_exception_data("Judgement", [])

    monkeypatch.setattr(scorers_mod, "get_model", lambda tier: FakeModel())
    out = await scorers_mod.judge_assertion("x", "y")

    assert out.passed is False


def test_rule_delta_is_the_difference():
    from eve.eval.scorers import rule_delta

    assert rule_delta({"assertion_pass": 80.0}, {"assertion_pass": 65.0}) == 15.0
    assert rule_delta({"assertion_pass": 60.0}, {"assertion_pass": 70.0}) == -10.0


def test_the_judge_uses_the_deep_tier_with_function_calling(monkeypatch):
    """Moved off REFLEX (metered Gemini, 15 req/min free-tier quota) after
    the 2026-08-31 real run rate-limited 4 of 9 spot-checked judge calls.
    method="function_calling": DEEP's default structured-output method
    (json_schema, via the Responses API) returned unparsed text through this
    LiteLLM proxy - see docs/architecture.md's Eval harness section."""
    from eve.eval import scorers as scorers_mod
    from eve.models import Tier

    tiers = []
    methods = []

    class FakeModel:
        def with_structured_output(self, schema, **kwargs):
            methods.append(kwargs.get("method"))
            return self

        async def ainvoke(self, messages):
            from eve.eval.scorers import Judgement

            return Judgement(passed=True, why="ok")

    def factory(tier):
        tiers.append(tier)
        return FakeModel()

    monkeypatch.setattr(scorers_mod, "get_model", factory)
    import asyncio

    asyncio.run(scorers_mod.judge_assertion("x", "y"))
    assert tiers == [Tier.DEEP]
    assert methods == ["function_calling"]
