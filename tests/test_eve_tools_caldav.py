"""The caldav library is synchronous and talks to a real server, so these
tests replace the calendar-lookup seam and exercise everything above it."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eve_tools import caldav_client


class FakeEvent:
    def __init__(self, data: str):
        self.data = data


def _ics(uid: str, summary: str, start: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nSUMMARY:{summary}\r\n"
        f"DTSTART:{start}\r\nDTEND:{start}\r\n"
        "LOCATION:Office\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )


@pytest.fixture
def one_calendar(monkeypatch):
    events = [FakeEvent(_ics("uid-1", "Dentist", "20260823T150000Z"))]
    calendar = SimpleNamespace(search=lambda **kwargs: events)
    monkeypatch.setattr(caldav_client, "_calendars", lambda sub: [calendar])
    return events


async def test_an_event_becomes_one_dict(one_calendar):
    result = await caldav_client.list_events("sub-noah", 90)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["uid"] == "uid-1"
    assert event["summary"] == "Dentist"
    assert event["location"] == "Office"
    assert event["start"] == "2026-08-23T15:00:00+00:00"


async def test_the_revision_changes_when_the_event_changes(one_calendar):
    first = (await caldav_client.list_events("sub-noah", 90))["events"][0]["revision"]
    one_calendar[0].data = _ics("uid-1", "Dentist MOVED", "20260823T170000Z")
    second = (await caldav_client.list_events("sub-noah", 90))["events"][0]["revision"]
    assert first != second


async def test_the_revision_is_stable_when_nothing_changes(one_calendar):
    first = (await caldav_client.list_events("sub-noah", 90))["events"][0]["revision"]
    second = (await caldav_client.list_events("sub-noah", 90))["events"][0]["revision"]
    assert first == second


async def test_an_unparseable_event_is_skipped_not_fatal(monkeypatch):
    """One malformed event on a shared calendar must not blind Eve to the
    rest of the day."""
    events = [FakeEvent("not a calendar at all"), FakeEvent(_ics("uid-2", "Soccer", "20260823T180000Z"))]
    monkeypatch.setattr(
        caldav_client, "_calendars", lambda sub: [SimpleNamespace(search=lambda **kw: events)]
    )
    result = await caldav_client.list_events("sub-noah", 90)
    assert [e["uid"] for e in result["events"]] == ["uid-2"]


async def test_a_member_without_credentials_gets_an_empty_list(monkeypatch):
    monkeypatch.setattr(
        caldav_client, "_credentials_for", lambda sub: (_ for _ in ()).throw(KeyError(sub))
    )
    assert await caldav_client.list_events("sub-nobody", 90) == {"events": []}


async def test_the_search_window_starts_now_and_spans_the_lookahead(monkeypatch):
    captured = {}

    def _search(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        caldav_client, "_calendars", lambda sub: [SimpleNamespace(search=_search)]
    )
    await caldav_client.list_events("sub-noah", 90)
    span = captured["end"] - captured["start"]
    assert 89 <= span.total_seconds() / 60 <= 91
    assert captured["event"] is True
