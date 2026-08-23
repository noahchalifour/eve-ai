import json
from unittest.mock import AsyncMock

from eve_ambient.sources import calendar

EVENTS = {
    "events": [
        {
            "uid": "uid-1",
            "revision": "abc123",
            "summary": "Dentist",
            "location": "Main St",
            "start": "2026-08-23T15:00:00+00:00",
            "end": "2026-08-23T16:00:00+00:00",
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
    assert "uid-1:start:2026-08-23T15:00:00+00:00" in keys


async def test_an_upcoming_event_also_produces_a_revision_signal(monkeypatch):
    """A reschedule changes the revision, so a fresh revision key is how a
    moved or cancelled event reaches Eve before its start window."""
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    keys = [s.key for s in await calendar.poll("sub-noah")]
    assert "uid-1:rev:abc123" in keys


async def test_the_summary_carries_the_time_and_place(monkeypatch):
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    start_signal = next(
        s for s in await calendar.poll("sub-noah") if ":start:" in s.key
    )
    assert "Dentist" in start_signal.summary
    assert "Main St" in start_signal.summary


async def test_signals_are_scoped_to_the_member_whose_calendar_it_is(monkeypatch):
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    assert all(s.member_sub == "sub-noah" for s in await calendar.poll("sub-noah"))


async def test_the_lookahead_from_settings_is_passed_through(monkeypatch):
    invoke = _invoke_returning({"events": []})
    monkeypatch.setattr(calendar, "invoke", invoke)
    await calendar.poll("sub-noah")
    _tool, args = invoke.await_args.args
    assert args["lookahead_minutes"] == 90


async def test_an_event_without_a_uid_is_skipped(monkeypatch):
    monkeypatch.setattr(
        calendar, "invoke", _invoke_returning({"events": [{"summary": "Ghost"}]})
    )
    assert await calendar.poll("sub-noah") == []


async def test_an_eve_tools_error_yields_no_signals(monkeypatch):
    monkeypatch.setattr(
        calendar, "invoke", AsyncMock(return_value="error: caldav unavailable")
    )
    assert await calendar.poll("sub-noah") == []
