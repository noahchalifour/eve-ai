"""The caldav library is synchronous and talks to a real server, so these
tests replace the calendar-lookup seam and exercise everything above it."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from eve_tools import caldav_client


class FakeEvent:
    def __init__(self, data: str):
        self.data = data


def _ics(uid: str, summary: str, start: str, extra: str = "") -> str:
    return (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nSUMMARY:{summary}\r\n"
        f"DTSTART:{start}\r\nDTEND:{start}\r\n"
        f"LOCATION:Office\r\n{extra}END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


@pytest.fixture
def one_calendar(monkeypatch):
    events = [FakeEvent(_ics("uid-1", "Dentist", "20260823T150000Z"))]
    calendar = SimpleNamespace(search=lambda **kwargs: events)
    monkeypatch.setattr(caldav_client, "_calendars", lambda sub: [calendar])
    return events


async def _revision(sub: str = "sub-noah") -> str:
    events = (await caldav_client.list_events(sub, 90, 14))["events"]
    return events[0]["revision"]


async def test_an_event_becomes_one_dict(one_calendar):
    result = await caldav_client.list_events("sub-noah", 90, 14)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["uid"] == "uid-1"
    assert event["summary"] == "Dentist"
    assert event["location"] == "Office"
    assert event["start"] == "2026-08-23T15:00:00+00:00"


async def test_the_revision_changes_when_the_event_changes(one_calendar):
    first = await _revision()
    one_calendar[0].data = _ics("uid-1", "Dentist MOVED", "20260823T170000Z")
    second = await _revision()
    assert first != second


async def test_the_revision_is_stable_when_nothing_changes(one_calendar):
    first = await _revision()
    second = await _revision()
    assert first == second


async def test_the_revision_ignores_fields_eve_does_not_report(one_calendar):
    """DTSTAMP and ATTENDEE/PARTSTAT live in the VEVENT body but neither
    reaches Eve, so neither may move the revision - otherwise a `:rev:`
    signal whose summary is byte-identical to the last one is exactly the
    notification that teaches a family to ignore Eve."""
    first = await _revision()
    one_calendar[0].data = _ics(
        "uid-1",
        "Dentist",
        "20260823T150000Z",
        extra=(
            "DTSTAMP:20260823T120000Z\r\n"
            "ATTENDEE;PARTSTAT=ACCEPTED:mailto:someone@example.com\r\n"
        ),
    )
    second = await _revision()
    assert first == second


async def test_the_revision_moves_when_the_summary_changes(one_calendar):
    first = await _revision()
    one_calendar[0].data = _ics("uid-1", "Dentist RESCHEDULED", "20260823T150000Z")
    second = await _revision()
    assert first != second


async def test_an_unparseable_event_is_skipped_not_fatal(monkeypatch):
    """One malformed event on a shared calendar must not blind Eve to the
    rest of the day."""
    events = [
        FakeEvent("not a calendar at all"),
        FakeEvent(_ics("uid-2", "Soccer", "20260823T180000Z")),
    ]
    monkeypatch.setattr(
        caldav_client,
        "_calendars",
        lambda sub: [SimpleNamespace(search=lambda **kw: events)],
    )
    result = await caldav_client.list_events("sub-noah", 90, 14)
    assert [e["uid"] for e in result["events"]] == ["uid-2"]


async def test_one_failing_calendar_does_not_blind_the_others(monkeypatch):
    """`principal().calendars()` returns every collection the member owns -
    task lists, birthday calendars, shared read-only calendars. A 403,
    timeout or unsupported-report on any single one must not blank the
    member's whole calendar."""

    def _broken_search(**kwargs):
        raise TimeoutError("boom")

    broken = SimpleNamespace(search=_broken_search)
    healthy = SimpleNamespace(
        search=lambda **kw: [FakeEvent(_ics("uid-3", "Standup", "20260823T090000Z"))]
    )
    monkeypatch.setattr(caldav_client, "_calendars", lambda sub: [broken, healthy])
    result = await caldav_client.list_events("sub-noah", 90, 14)
    assert [e["uid"] for e in result["events"]] == ["uid-3"]


async def test_a_property_that_breaks_marshalling_does_not_blind_its_siblings(
    monkeypatch,
):
    """A VEVENT that parses cleanly can still carry a property shape that
    breaks the marshalling - here, a dtstart whose `.dt` has no `.year`.
    That must drop the one event, not the batch (fix round 1 item A2: the
    dict construction has to run inside the same guard as parsing)."""
    real_as_utc_iso = caldav_client._as_utc_iso

    def _flaky_as_utc_iso(value):
        if getattr(value, "year", None) == 1900:
            raise AttributeError("boom")
        return real_as_utc_iso(value)

    monkeypatch.setattr(caldav_client, "_as_utc_iso", _flaky_as_utc_iso)

    events = [
        FakeEvent(_ics("uid-bad", "Bad", "19000101T100000Z")),
        FakeEvent(_ics("uid-5", "Good", "20260823T110000Z")),
    ]
    monkeypatch.setattr(
        caldav_client,
        "_calendars",
        lambda sub: [SimpleNamespace(search=lambda **kw: events)],
    )
    result = await caldav_client.list_events("sub-noah", 90, 14)
    assert [e["uid"] for e in result["events"]] == ["uid-5"]


async def test_a_member_without_credentials_gets_an_empty_list(monkeypatch):
    monkeypatch.setattr(
        caldav_client,
        "_credentials_for",
        lambda sub: (_ for _ in ()).throw(KeyError(sub)),
    )
    assert await caldav_client.list_events("sub-nobody", 90, 14) == {"events": []}


async def test_the_search_window_starts_now_and_spans_the_horizon(monkeypatch):
    captured = {}

    def _search(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        caldav_client, "_calendars", lambda sub: [SimpleNamespace(search=_search)]
    )
    await caldav_client.list_events("sub-noah", 90, 14)
    span_days = captured["end"] - captured["start"]
    assert 13.99 <= span_days.total_seconds() / 86400 <= 14.01
    assert captured["event"] is True


def test_as_utc_iso_of_none_is_none():
    assert caldav_client._as_utc_iso(None) is None


def test_as_utc_iso_of_a_naive_datetime_is_read_as_utc():
    """No member timezone exists anywhere in this design; a floating DTSTART
    is read as UTC. Pinned here because a regression - reading it in the
    local server timezone, say - would shift the event and silently change
    the `:start:` dedup key."""
    naive = datetime(2026, 8, 23, 15, 0, 0)
    assert caldav_client._as_utc_iso(naive) == "2026-08-23T15:00:00+00:00"


def test_as_utc_iso_of_an_all_day_date_is_midnight_utc():
    """An all-day event has no clock; midnight UTC on that calendar day is
    the pinned reading, even though it means the event enters a 90-minute
    lookahead the evening before somewhere west of UTC. Defensible, but
    nothing pinned it before - a regression would have been invisible."""
    assert caldav_client._as_utc_iso(date(2026, 8, 23)) == "2026-08-23T00:00:00+00:00"
