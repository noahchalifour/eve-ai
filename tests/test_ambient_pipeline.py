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

    async def _judge(signal):
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


async def test_quiet_hours_do_not_suppress_an_urgent_signal(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="leak")
    assert await pipeline.handle_signal(_signal(source="home"), now=NIGHT) == "sent"
    assert wiring["delivered"] == ["sub-noah"]


async def test_the_cap_suppresses_once_it_is_reached(wiring):
    wiring["counts"]["sub-noah"] = 2
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "capped"


async def test_an_urgent_signal_bypasses_the_cap(wiring):
    wiring["counts"]["sub-noah"] = 99
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="fire")
    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "sent"


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


async def test_two_members_each_get_their_own_notice(wiring):
    wiring["verdict"] = FilterVerdict(
        notify=True, audience=["sub-noah", "sub-kid"], why="w"
    )
    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "sent"
    assert sorted(wiring["delivered"]) == ["sub-kid", "sub-noah"]
