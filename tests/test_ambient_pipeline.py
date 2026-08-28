from datetime import UTC, datetime

import pytest

from eve_ambient import pipeline
from eve_ambient.types import FilterVerdict, Signal

ROSTER = """
members:
  - sub: "sub-noah"
    name: "Noah"
    role: adult
    timezone: "America/Vancouver"
    permissions: [mail.read, finances, home.control, calendar.read]
  - sub: "sub-kid"
    name: "Kid"
    role: child
    timezone: "America/Vancouver"
    permissions: [home.control]
"""

MIDDAY = datetime(2026, 8, 23, 19, 0, tzinfo=UTC)   # 12:00 in Vancouver
NIGHT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)     # 02:00 in Vancouver


def _signal(source="finances", member_sub=None, key="k1"):
    return Signal(
        source=source, key=key, occurred_at=MIDDAY, member_sub=member_sub,
        summary="Budget over: Groceries.", payload={},
    )


@pytest.fixture(autouse=True)
def wiring(tmp_path, monkeypatch):
    """Replace the three I/O seams — store, filter, notify — and keep the
    real gates, because the gates are what this task is about."""
    path = tmp_path / "family.yaml"
    path.write_text(ROSTER)
    monkeypatch.setenv("EVE_FAMILY_FILE", str(path))
    monkeypatch.setenv("EVE_AMBIENT_DAILY_CAP", "2")
    monkeypatch.setenv("EVE_AMBIENT_QUIET_HOURS", "21:00-07:00")
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()

    state = {
        "fresh": True, "seen": [], "notices": [], "counts": {},
        "verdict": FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="w"),
        "delivered": [], "deliver_result": "thread-1", "deliver_error": None,
        "judge_error": None,
        # Simulates every existing notice row having aged out of whatever
        # cooldown window is passed to already_notified — i.e. "time has
        # passed beyond the cooldown" (fix round 2, item 1). A real signal
        # source can't fast-forward a Postgres clock in a unit test, so this
        # is the fake's stand-in for that.
        "notices_expired": False,
    }

    async def _is_fresh(source, key, cooldown_hours):
        state["cooldown_seen"] = cooldown_hours
        return state["fresh"]

    async def _mark_seen(source, key):
        state["seen"].append((source, key))

    async def _record_notice(member_sub, source, key, urgent, thread_id):
        state["notices"].append((member_sub, source, key, urgent, thread_id))

    async def _notices_since(member_sub, since):
        return state["counts"].get(member_sub, 0)

    async def _already_notified(member_sub, source, key, cooldown_hours):
        # Backed by the same notices the fake record_notice writes to, so a
        # retry within a test sees exactly what an earlier pass recorded —
        # the real idempotency contract (fix round 1, item 1). The pipeline
        # must thread the same cooldown it computed for is_fresh through to
        # this call (fix round 2, item 1); assert on it rather than silently
        # accepting whatever arrives.
        state["already_notified_cooldown_seen"] = cooldown_hours
        if state["notices_expired"]:
            return False
        return any(
            n[0] == member_sub and n[1] == source and n[2] == key
            for n in state["notices"]
        )

    async def _judge(signal):
        if state.get("judge_error"):
            raise state["judge_error"]
        return state["verdict"]

    async def _deliver(signal, member, verdict, notifier):
        if state["deliver_error"]:
            raise state["deliver_error"]
        state["delivered"].append(member.sub)
        return state["deliver_result"]

    monkeypatch.setattr(pipeline.store, "is_fresh", _is_fresh)
    monkeypatch.setattr(pipeline.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(pipeline.store, "record_notice", _record_notice)
    monkeypatch.setattr(pipeline.store, "notices_since", _notices_since)
    monkeypatch.setattr(pipeline.store, "already_notified", _already_notified)
    monkeypatch.setattr(pipeline, "judge", _judge)
    monkeypatch.setattr(pipeline, "deliver", _deliver)
    yield state
    get_settings.cache_clear()
    get_family.cache_clear()


async def test_a_signal_inside_its_cooldown_is_stale_and_costs_no_filter_call(wiring):
    wiring["fresh"] = False
    wiring["verdict"] = None  # judge would raise if called
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "stale"
    assert wiring["delivered"] == []


async def test_the_signals_own_cooldown_overrides_the_default(wiring):
    signal = Signal(
        source="finances", key="b1", occurred_at=MIDDAY, member_sub=None,
        summary="over", payload={}, cooldown_hours=720,
    )
    await pipeline.handle_signal(signal, now=MIDDAY)
    assert wiring["cooldown_seen"] == 720


async def test_the_default_cooldown_is_used_when_the_signal_has_none(wiring):
    await pipeline.handle_signal(_signal(), now=MIDDAY)
    assert wiring["cooldown_seen"] == 6


async def test_a_notify_verdict_delivers_and_records(wiring):
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "sent"
    assert wiring["delivered"] == ["sub-noah"]
    assert wiring["notices"][0][0] == "sub-noah"
    assert wiring["seen"] == [("finances", "k1")]


async def test_a_no_verdict_is_marked_seen_and_never_delivered(wiring):
    wiring["verdict"] = FilterVerdict(notify=False, why="routine")
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "filtered"
    assert wiring["delivered"] == []
    assert wiring["seen"] == [("finances", "k1")]


async def test_a_notify_verdict_with_an_empty_audience_is_filtered(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=[], why="who?")
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "filtered"


async def test_a_member_without_the_permission_is_dropped(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-kid"], why="w")
    assert await pipeline.handle_signal(_signal(source="finances"), now=MIDDAY) == "unpermitted"
    assert wiring["delivered"] == []


async def test_quiet_hours_suppress_a_normal_signal(wiring):
    assert await pipeline.handle_signal(_signal(), now=NIGHT) == "quiet"
    assert wiring["delivered"] == []
    # Dropped, not queued (fix round 1, item 6): a held signal is still
    # resolved, or the next poll would deliver yesterday's door-open at
    # breakfast.
    assert wiring["seen"] == [("finances", "k1")]


async def test_quiet_hours_do_not_suppress_an_urgent_signal(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="leak")
    assert await pipeline.handle_signal(_signal(source="home"), now=NIGHT) == "sent"
    assert wiring["delivered"] == ["sub-noah"]


async def test_the_cap_suppresses_once_it_is_reached(wiring):
    wiring["counts"]["sub-noah"] = 2
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "capped"
    assert wiring["delivered"] == []
    # Dropped, not queued (fix round 1, item 6).
    assert wiring["seen"] == [("finances", "k1")]


async def test_an_urgent_signal_bypasses_the_cap(wiring):
    wiring["counts"]["sub-noah"] = 99
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="fire")
    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "sent"


async def test_urgent_cannot_bypass_the_permission_gate(wiring):
    """`urgent` bypasses the cap and quiet hours, never the permission gate
    (fix round 1, item 6): sub-kid holds no `finances` permission, urgent or
    not, so the signal never reaches the per-member loop where the bypass
    would apply."""
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-kid"], urgent=True, why="fire")
    assert await pipeline.handle_signal(_signal(source="finances"), now=MIDDAY) == "unpermitted"
    assert wiring["delivered"] == []


async def test_an_urgent_bypass_is_logged_at_warning_level(wiring, caplog):
    """A 3am false alarm is only fixable if it is visible (fix round 1, item
    6): every urgent bypass of the cap/quiet-hours gate must log a warning
    naming the source, key, member and the filter's reasoning."""
    wiring["counts"]["sub-noah"] = 99
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="fire")
    with caplog.at_level("WARNING"):
        assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "sent"
    line = next(
        r.getMessage()
        for r in caplog.records
        if r.levelname == "WARNING" and "URGENT bypass" in r.getMessage()
    )
    assert "source=home" in line
    assert "sub-noah" in line
    assert "fire" in line


async def test_a_veto_is_recorded_as_seen_but_not_as_a_notice(wiring):
    wiring["deliver_result"] = None
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "vetoed"
    assert wiring["notices"] == []
    assert wiring["seen"] == [("finances", "k1")]


async def test_a_delivery_failure_leaves_the_signal_unseen(wiring):
    from eve_ambient.notify import DeliveryError

    wiring["deliver_error"] = DeliveryError("aegra down")
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "deferred"
    assert wiring["seen"] == []


async def test_a_record_notice_failure_after_a_successful_delivery_still_marks_seen(
    wiring, monkeypatch, caplog
):
    """(fix round 4, item 5) `deliver` already returned a thread id - the
    push already happened - when `record_notice` raises. Before this fix
    that exception escaped `handle_signal` entirely, leaving the signal
    unseen; the next poll would find no notice row and re-deliver, producing
    a second 3am push for the sake of a lost cap-counter row. The signal
    must still resolve as sent and still be marked seen."""

    async def _record_notice(member_sub, source, key, urgent, thread_id):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(pipeline.store, "record_notice", _record_notice)

    with caplog.at_level("ERROR"):
        assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "sent"

    assert wiring["delivered"] == ["sub-noah"]
    assert wiring["seen"] == [("finances", "k1")]
    assert any(r.levelname == "ERROR" for r in caplog.records)


async def test_a_mail_signal_only_reaches_its_owner(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-kid"], why="w")
    result = await pipeline.handle_signal(
        _signal(source="mail", member_sub="sub-noah"), now=MIDDAY
    )
    assert result == "sent"
    assert wiring["delivered"] == ["sub-noah"]


async def test_every_signal_leaves_one_resolution_line(wiring, caplog):
    """Design section 9: the trace only starts at the compose turn, so the
    verdict, the reasoning and the gate that stopped it live in this log line
    or nowhere."""
    wiring["verdict"] = FilterVerdict(notify=False, why="routine and expected")
    with caplog.at_level("INFO"):
        await pipeline.handle_signal(_signal(), now=MIDDAY)
    line = next(
        r.getMessage() for r in caplog.records if "ambient resolved" in r.getMessage()
    )
    assert "outcome=filtered" in line
    assert "key=k1" in line
    assert "routine and expected" in line


async def test_a_stale_signal_also_leaves_one_resolution_line(wiring, caplog):
    """The cooldown is both the most frequently taken path and, before this
    fix, the one that left no trace: "why didn't Eve tell me about X" was
    unanswerable for exactly the case where the suppression was deliberate
    (fix round 1, item 4)."""
    wiring["fresh"] = False
    with caplog.at_level("INFO"):
        assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "stale"
    line = next(
        r.getMessage() for r in caplog.records if "ambient resolved" in r.getMessage()
    )
    assert "outcome=stale" in line
    assert "key=k1" in line


async def test_two_members_each_get_their_own_notice(wiring):
    wiring["verdict"] = FilterVerdict(
        notify=True, audience=["sub-noah", "sub-kid"], why="w"
    )
    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "sent"
    assert sorted(wiring["delivered"]) == ["sub-kid", "sub-noah"]
    # Not just delivered but each recorded as their own notice against their
    # own daily cap (fix round 1, item 6) — this would still pass if
    # record_notice were called once instead of twice.
    assert sorted(n[0] for n in wiring["notices"]) == ["sub-kid", "sub-noah"]


async def test_a_partial_defer_leaves_the_signal_unseen(wiring, monkeypatch):
    """Two members, one delivery raises: the whole signal is deferred, not
    just the failing member — `mark_seen` must not run so the next poll
    retries the entire audience (fix round 1, item 1, first half)."""
    from eve_ambient.notify import DeliveryError

    wiring["verdict"] = FilterVerdict(
        notify=True, audience=["sub-noah", "sub-kid"], why="w"
    )

    async def _deliver(signal, member, verdict, notifier):
        if member.sub == "sub-kid":
            raise DeliveryError("aegra down")
        wiring["delivered"].append(member.sub)
        return "thread-1"

    monkeypatch.setattr(pipeline, "deliver", _deliver)

    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "deferred"
    assert wiring["seen"] == []
    assert wiring["delivered"] == ["sub-noah"]


async def test_a_retry_after_a_partial_defer_only_reaches_the_missed_member(
    wiring, monkeypatch
):
    """The other half of item 1: a retry must not re-deliver, re-push, or
    re-spend the daily cap for the member who already has the message — only
    the member the first pass missed should see a new delivery attempt."""
    from eve_ambient.notify import DeliveryError

    wiring["verdict"] = FilterVerdict(
        notify=True, audience=["sub-noah", "sub-kid"], why="w"
    )
    attempts = {"sub-kid": 0}

    async def _deliver(signal, member, verdict, notifier):
        if member.sub == "sub-kid":
            attempts["sub-kid"] += 1
            if attempts["sub-kid"] == 1:
                raise DeliveryError("aegra down")
        wiring["delivered"].append(member.sub)
        return "thread-1"

    monkeypatch.setattr(pipeline, "deliver", _deliver)

    # First pass: sub-noah is delivered and recorded; sub-kid fails.
    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "deferred"
    assert wiring["delivered"] == ["sub-noah"]
    assert wiring["seen"] == []

    # Second pass over the same signal: sub-noah already has a notice row
    # and must not be re-delivered to; only sub-kid gets a new attempt.
    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "sent"
    assert wiring["delivered"] == ["sub-noah", "sub-kid"]
    assert wiring["seen"] == [("home", "k1")]


async def test_a_filter_infrastructure_failure_defers_rather_than_dropping(
    wiring, monkeypatch
):
    """A REFLEX outage is a couldn't-decide, not a decided-no (fix round 1,
    item 2): the signal must be left unseen, exactly like a
    notify.DeliveryError, so the next poll retries it instead of the outage
    silently and permanently discarding every signal in its window."""
    from eve_ambient.filter import FilterError

    async def _judge(signal):
        raise FilterError("litellm down")

    monkeypatch.setattr(pipeline, "judge", _judge)

    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "deferred"
    assert wiring["seen"] == []
    assert wiring["delivered"] == []


async def test_already_notified_is_bounded_by_the_same_cooldown_as_is_fresh(wiring):
    """fix round 2, item 1: the idempotency check must be bounded by the
    signal's own cooldown, the same one `is_fresh` uses — not open-ended —
    or every recurrence of a key like home.py's `door:open` -> `door:closed`
    -> `door:open` would be silently dropped forever. This asserts the
    pipeline actually threads that value through rather than a hardcoded or
    missing one."""
    signal = Signal(
        source="finances", key="b1", occurred_at=MIDDAY, member_sub=None,
        summary="over", payload={}, cooldown_hours=720,
    )
    await pipeline.handle_signal(signal, now=MIDDAY)
    assert wiring["already_notified_cooldown_seen"] == 720


async def test_a_recurrence_after_the_cooldown_expires_is_delivered_again(wiring):
    """The case none of the fix-round-1 tests covered (fix round 2, item 1):
    a member who was notified in an earlier cooldown window must be
    delivered to again once that window has passed, rather than the notice
    row suppressing them forever. `notices_expired` stands in for "enough
    wall-clock time has passed that the bounded lookup no longer matches"."""
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "sent"
    assert wiring["delivered"] == ["sub-noah"]

    wiring["notices_expired"] = True
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "sent"
    assert wiring["delivered"] == ["sub-noah", "sub-noah"]


async def test_a_defer_that_outlives_the_cooldown_notifies_again_on_retry(wiring):
    """The accepted trade-off from fix round 2, item 1: if a defer persists
    longer than the cooldown, the eventual retry notifies the member again
    rather than silently treating them as already handled — correct at that
    distance in time, and bounded rather than the unbounded bug this
    replaces."""
    from eve_ambient.notify import DeliveryError

    wiring["deliver_error"] = DeliveryError("aegra down")
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "deferred"
    assert wiring["delivered"] == []

    # The cooldown elapses while the outage is ongoing; on retry the outage
    # has since cleared.
    wiring["deliver_error"] = None
    wiring["notices_expired"] = True
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "sent"
    assert wiring["delivered"] == ["sub-noah"]


async def test_an_audience_fully_already_notified_resolves_as_known_not_filtered(
    wiring,
):
    """fix round 2, item 3: once the idempotence skip can fire for every
    member in the audience, the `"filtered"` fallthrough becomes ambiguous
    between "the filter said no" and "everyone already knew." They get
    distinct outcomes so the resolution line still means one thing."""
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "sent"
    assert wiring["delivered"] == ["sub-noah"]

    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "known"
    assert wiring["delivered"] == ["sub-noah"]
    # Still resolved — marked seen so this pass does not spin forever.
    assert wiring["seen"] == [("finances", "k1"), ("finances", "k1")]


async def test_a_malformed_filter_response_resolves_filtered_not_deferred(
    wiring, monkeypatch
):
    """Final round, item 2: the previous version of this test hand-set
    `wiring["verdict"]` to a not-notify verdict and so exercised the same
    "the filter said no" path every other filtered test already covers — it
    would have passed unchanged against code that never classified a
    malformed response at all. This drives a real `ValidationError` through
    the actual `filter.judge` (not the fixture's fake), so the
    classification added in fix round 2 is exercised end to end: without it,
    `judge` would raise `FilterError` for the ValidationError same as any
    other exception, and the pipeline would resolve "deferred", not
    "filtered"."""
    from pydantic import ValidationError

    from eve_ambient import filter as ambient_filter

    try:
        FilterVerdict.model_validate({"audience": 123})
    except ValidationError as exc:
        malformed = exc
    else:
        raise AssertionError("expected model_validate to raise ValidationError")

    class _FakeModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages, *args, **kwargs):
            raise malformed

    async def _no_memory(sub, thread):
        return [], [], None, []

    monkeypatch.setattr(ambient_filter, "load_always_on", _no_memory)
    monkeypatch.setattr(ambient_filter, "get_model", lambda tier: _FakeModel())
    # Route the pipeline through the real judge() for this one test, instead
    # of the fixture's fake, so the classification itself is under test.
    monkeypatch.setattr(pipeline, "judge", ambient_filter.judge)

    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "filtered"
    assert wiring["seen"] == [("finances", "k1")]
    assert wiring["delivered"] == []
