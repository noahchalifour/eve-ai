from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from eve_ambient import filter as ambient_filter
from eve_ambient.types import FilterVerdict, Signal

SIGNAL = Signal(
    source="home",
    key="binary_sensor.garage:open",
    occurred_at=datetime(2026, 8, 23, 2, 0, tzinfo=UTC),
    member_sub=None,
    summary="The garage door has been open for 40 minutes.",
    payload={"entity_id": "binary_sensor.garage", "state": "open"},
)


class FakeStructuredModel:
    def __init__(self, verdict=None, error=None):
        self.verdict, self.error, self.prompt = verdict, error, None

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages, *args, **kwargs):
        self.prompt = messages[0].content
        if self.error:
            raise self.error
        return self.verdict


@pytest.fixture
def no_household_memory(monkeypatch):
    async def _load_always_on(sub, thread):
        return [], [], None

    monkeypatch.setattr(ambient_filter, "load_always_on", _load_always_on)


async def test_the_verdict_is_returned_as_given(monkeypatch, no_household_memory):
    verdict = FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="ok")
    monkeypatch.setattr(
        ambient_filter, "get_model", lambda tier: FakeStructuredModel(verdict=verdict)
    )
    assert await ambient_filter.judge(SIGNAL) == verdict


async def test_a_failing_model_call_raises_rather_than_deciding_no(
    monkeypatch, no_household_memory
):
    """A REFLEX outage is a couldn't-decide, not a decided-no: collapsing the
    two would mark every signal in the outage window seen and drop it
    forever, the same mistake as treating notify.DeliveryError as a veto
    (fix round 1, item 2). The caller — the pipeline — is the one that
    leaves the signal unseen so the next poll retries."""
    monkeypatch.setattr(
        ambient_filter,
        "get_model",
        lambda tier: FakeStructuredModel(error=RuntimeError("litellm down")),
    )
    with pytest.raises(ambient_filter.FilterError):
        await ambient_filter.judge(SIGNAL)


async def test_a_malformed_response_resolves_to_not_notify_rather_than_deferring(
    monkeypatch, no_household_memory
):
    """A response that arrives but does not fit FilterVerdict is a
    deterministic dead end, not a retry candidate (fix round 2, item 2):
    raising FilterError here would defer this signal forever, since a
    malformed response does not spontaneously become a well-formed one on
    the next poll. It must resolve — as a genuine not-notify verdict — so
    the signal is marked seen exactly once."""
    try:
        FilterVerdict.model_validate({"audience": 123})
    except ValidationError as exc:
        malformed = exc
    else:
        raise AssertionError("expected model_validate to raise ValidationError")

    monkeypatch.setattr(
        ambient_filter, "get_model", lambda tier: FakeStructuredModel(error=malformed)
    )
    verdict = await ambient_filter.judge(SIGNAL)
    assert verdict.notify is False
    assert "malformed" in verdict.why


async def test_a_non_filterverdict_response_resolves_to_not_notify(
    monkeypatch, no_household_memory
):
    """Belt-and-braces (fix round 2, item 2, the related gap): if
    `with_structured_output` ever returns something other than a
    FilterVerdict — a dict, say — accessing `.notify` on it would raise an
    AttributeError past the pipeline's `except FilterError` and into the
    poll loop. It must be handled explicitly instead."""
    monkeypatch.setattr(
        ambient_filter,
        "get_model",
        lambda tier: FakeStructuredModel(verdict={"notify": True}),
    )
    verdict = await ambient_filter.judge(SIGNAL)
    assert verdict.notify is False
    assert "malformed" in verdict.why


async def test_the_prompt_carries_the_summary_the_roster_and_the_time(
    monkeypatch, no_household_memory
):
    model = FakeStructuredModel(verdict=FilterVerdict())
    monkeypatch.setattr(ambient_filter, "get_model", lambda tier: model)
    await ambient_filter.judge(SIGNAL)
    assert SIGNAL.summary in model.prompt
    assert "Noah" in model.prompt
    assert "2026-08-23" in model.prompt


async def test_household_memory_reaches_the_prompt(monkeypatch):
    """Without it the filter re-tells the family things they already know."""
    from eve.memory.types import Memory

    fact = Memory(
        id="1", layer="household", scope_kind="household", scope_id="",
        kind="fact", subject=None, content="The garage door sticks in humidity.",
        confidence=0.9, salience=0.8,
        created_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
    )

    async def _load_always_on(sub, thread):
        return [], [fact], None

    monkeypatch.setattr(ambient_filter, "load_always_on", _load_always_on)
    model = FakeStructuredModel(verdict=FilterVerdict())
    monkeypatch.setattr(ambient_filter, "get_model", lambda tier: model)
    await ambient_filter.judge(SIGNAL)
    assert "sticks in humidity" in model.prompt


async def test_a_memory_read_failure_still_produces_a_verdict(monkeypatch):
    """Postgres being unreachable should cost the filter its context, not its
    ability to answer."""
    async def _boom(sub, thread):
        raise RuntimeError("no database")

    monkeypatch.setattr(ambient_filter, "load_always_on", _boom)
    verdict = FilterVerdict(notify=True, audience=["sub-noah"], why="fine")
    monkeypatch.setattr(
        ambient_filter, "get_model", lambda tier: FakeStructuredModel(verdict=verdict)
    )
    assert (await ambient_filter.judge(SIGNAL)).notify is True


async def test_the_reflex_tier_is_the_one_used(monkeypatch, no_household_memory):
    """Ambient filtering runs on every household signal; it must never spend
    the conversational tier's quota (models.py's REFLEX comment)."""
    from eve.models import Tier

    seen = {}

    def _get_model(tier):
        seen["tier"] = tier
        return FakeStructuredModel(verdict=FilterVerdict())

    monkeypatch.setattr(ambient_filter, "get_model", _get_model)
    await ambient_filter.judge(SIGNAL)
    assert seen["tier"] is Tier.REFLEX
