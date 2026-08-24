import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from eve_ambient.sources import calendar

_NOW = datetime.now(UTC)
_SOON = (_NOW + timedelta(minutes=30)).isoformat()
_LATER = (_NOW + timedelta(days=5)).isoformat()

EVENTS = {
    "events": [
        {
            "uid": "uid-1",
            "revision": "abc123",
            "summary": "Dentist",
            "location": "Main St",
            "start": _SOON,
            "end": _SOON,
        }
    ]
}


def _invoke_returning(payload):
    # tools_client.invoke already unwraps eve-tools' {"result": ...} envelope
    # and returns the inner object as a JSON string.
    return AsyncMock(return_value=json.dumps(payload))


async def test_an_upcoming_event_produces_a_start_signal(monkeypatch):
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    keys = [s.key for s in await calendar.poll("sub-noah")]
    assert f"uid-1:start:{_SOON}" in keys


async def test_an_upcoming_event_also_produces_a_revision_signal(monkeypatch):
    """A reschedule changes the revision, so a fresh revision key is how a
    moved or cancelled event reaches Eve before its start window."""
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    keys = [s.key for s in await calendar.poll("sub-noah")]
    assert "uid-1:rev:abc123" in keys


async def test_an_event_beyond_the_lookahead_gets_no_start_signal(monkeypatch):
    """The horizon is wider than the lookahead precisely so a change to a
    far-off event is seen before it becomes imminent - but `:start:` still
    only fires once the event is actually starting soon (fix round 1 item
    B)."""
    payload = {
        "events": [
            {
                "uid": "uid-2",
                "revision": "def456",
                "summary": "Reunion",
                "location": "Grandma's",
                "start": _LATER,
                "end": _LATER,
            }
        ]
    }
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(payload))
    signals = await calendar.poll("sub-noah")
    assert [s.key for s in signals] == ["uid-2:rev:def456"]


async def test_the_summary_carries_the_time_and_place(monkeypatch):
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    start_signal = next(
        s for s in await calendar.poll("sub-noah") if ":start:" in s.key
    )
    assert "Dentist" in start_signal.summary
    assert "Main St" in start_signal.summary


async def test_the_revision_summary_never_claims_a_change(monkeypatch):
    """The filter reads only the one-line summary, so a `:rev:` summary
    asserting an unestablished change is the defect this fix round exists to
    close (fix round 1 item C)."""
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    rev_signal = next(s for s in await calendar.poll("sub-noah") if ":rev:" in s.key)
    assert "changed" not in rev_signal.summary.lower()


async def test_a_cancelled_event_says_so_in_the_summary(monkeypatch):
    payload = {
        "events": [
            {
                "uid": "uid-3",
                "revision": "ghi789",
                "summary": "Standup",
                "location": "",
                "start": _SOON,
                "end": _SOON,
                "status": "CANCELLED",
            }
        ]
    }
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(payload))
    signals = await calendar.poll("sub-noah")
    assert all("CANCELLED" in s.summary for s in signals)


async def test_occurred_at_is_now_not_the_events_start_time(monkeypatch):
    """The filter prompt renders `occurred_at` as "Occurred at"; a future
    start time under that label would be a lie - the payload already carries
    `start` and the summary says it in words (fix round 1 item E)."""
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    before = datetime.now(UTC)
    signals = await calendar.poll("sub-noah")
    after = datetime.now(UTC)
    assert all(before <= s.occurred_at <= after for s in signals)


async def test_signals_are_scoped_to_the_member_whose_calendar_it_is(monkeypatch):
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    assert all(s.member_sub == "sub-noah" for s in await calendar.poll("sub-noah"))


async def test_the_lookahead_and_horizon_from_settings_are_passed_through(monkeypatch):
    invoke = _invoke_returning({"events": []})
    monkeypatch.setattr(calendar, "invoke", invoke)
    await calendar.poll("sub-noah")
    _tool, args = invoke.await_args.args
    assert args["lookahead_minutes"] == 90
    assert args["horizon_days"] == 14


async def test_an_event_without_a_uid_is_skipped(monkeypatch):
    monkeypatch.setattr(
        calendar, "invoke", _invoke_returning({"events": [{"summary": "Ghost"}]})
    )
    assert await calendar.poll("sub-noah") == []


async def test_an_event_without_a_start_is_skipped(monkeypatch):
    """A model should never be handed "…, starting None." (fix round 1 item
    E) - dropping the event beats emitting a signal with a fabricated
    dedup key."""
    payload = {"events": [{"uid": "uid-4", "revision": "jkl012", "summary": "Ghost"}]}
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(payload))
    assert await calendar.poll("sub-noah") == []


async def test_an_eve_tools_error_yields_no_signals(monkeypatch):
    monkeypatch.setattr(
        calendar, "invoke", AsyncMock(return_value="error: caldav unavailable")
    )
    assert await calendar.poll("sub-noah") == []
