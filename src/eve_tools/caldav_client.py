"""CalDAV client. One credential per family member, the same shape gmail.py
uses: caldav_credentials_json holds a JSON object keyed by member sub, each
value {"url": ..., "username": ..., "password": ...}.

The caldav library is synchronous, so every call runs in a thread via
asyncio.to_thread, exactly as gmail.py does, so one slow calendar server does
not block eve-tools' event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import caldav
import icalendar

from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)


def _credentials_for(member_sub: str) -> dict:
    all_creds = json.loads(get_tools_settings().caldav_credentials_json or "{}")
    return all_creds[member_sub]


def _calendars(member_sub: str) -> list:
    creds = _credentials_for(member_sub)
    client = caldav.DAVClient(
        url=creds["url"], username=creds["username"], password=creds["password"]
    )
    return client.principal().calendars()


def _as_utc_iso(value) -> str | None:
    """An icalendar dtstart is a datetime or a date. An all-day event has no
    time; midnight UTC is the only sane reading, and losing the event because
    it lacks a clock would be worse."""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    return datetime(value.year, value.month, value.day, tzinfo=UTC).isoformat()


def _to_dict(raw: str, member_sub: str) -> dict | None:
    # Everything below - parsing AND field extraction, including
    # `_as_utc_iso`'s `.year` access on whatever `.dt` turned out to be -
    # must run under this one guard, or a property shape a well-formed VEVENT
    # can still carry blanks the whole calendar instead of dropping one event
    # (fix round 1 item A2).
    try:
        component = next(
            part
            for part in icalendar.Calendar.from_ical(raw).walk()
            if part.name == "VEVENT"
        )
        uid = str(component.get("uid", ""))
        summary = str(component.get("summary", ""))
        location = str(component.get("location", ""))
        status = str(component.get("status", ""))
        start = _as_utc_iso(getattr(component.get("dtstart"), "dt", None))
        end = _as_utc_iso(getattr(component.get("dtend"), "dt", None))
        return {
            "uid": uid,
            # A content hash over exactly the fields Eve reports, not a
            # server etag and not the whole VCALENDAR body: another
            # attendee's PARTSTAT, a SEQUENCE/DTSTAMP bump, a VALARM edit or
            # a server reordering properties must not mint a new revision
            # when nothing Eve would actually say has changed (fix round 1
            # item C).
            "revision": hashlib.sha256(
                "|".join(
                    [uid, summary, location, str(start), str(end), status]
                ).encode("utf-8", "replace")
            ).hexdigest()[:16],
            "summary": summary,
            "location": location,
            "start": start,
            "end": end,
            "status": status,
        }
    except Exception:
        logger.warning(
            "skipping an unparseable calendar event for %s: %.80s",
            member_sub,
            raw,
            exc_info=True,
        )
        return None


async def list_events(
    member_sub: str, lookahead_minutes: int, horizon_days: int
) -> dict:
    """`lookahead_minutes` stays part of the tool contract; the search window
    itself is bounded by `horizon_days` so a source can detect a change to an
    event that has not yet entered the lookahead (fix round 1 item B - a
    plan defect in the original brief, not this implementation's)."""

    def _run() -> dict:
        # A total failure to reach the account at all - no credentials for
        # this member, a bad URL, a rejected login - must propagate rather
        # than degrade to an empty calendar (rereview fix, item 2). Before
        # this, `caldav_credentials_json` unset or holding a placeholder -
        # the literal state of a fresh deployment - made `_credentials_for`
        # raise `KeyError`, which this swallowed into `{"events": []}`: a
        # valid-looking empty result that primed the calendar source against
        # a connection that never actually succeeded. Letting this raise
        # means `invoke_tool` (`eve_tools/app.py`) turns it into the
        # `error: ...` string `tool_result` already recognises, and
        # `calendar.poll` raises instead of priming. Still logged here first,
        # since a raised exception crossing the `invoke_tool` boundary is
        # reported to the caller but not logged server-side on its own.
        try:
            calendars = _calendars(member_sub)
        except Exception:
            logger.warning("no reachable calendar for %s", member_sub, exc_info=True)
            raise
        start = datetime.now(UTC)
        end = start + timedelta(days=horizon_days)
        events = []
        partial = False
        for calendar in calendars:
            # `principal().calendars()` returns every collection the member
            # owns - task lists, birthday calendars, shared read-only
            # calendars. One 403, timeout or unsupported-report on any single
            # one must not blank the rest (fix round 1 item A1) - so this
            # failure is caught per-calendar, not propagated like the one
            # above. But it is still a partial result, and an unprimed
            # source priming against a partial calendar is the same hazard
            # priming against an empty one is (design invariant, item 18):
            # `partial` rides along in the response so `eve_ambient`'s
            # calendar source can raise `SourcePollError` and keep the
            # events it did get without letting them prime the source.
            try:
                found_events = calendar.search(
                    start=start, end=end, event=True, expand=True
                )
                for found in found_events:
                    parsed = _to_dict(found.data, member_sub)
                    if parsed and parsed["uid"]:
                        events.append(parsed)
            except Exception:
                logger.warning(
                    "calendar lookup failed for %s, skipping that calendar",
                    member_sub,
                    exc_info=True,
                )
                partial = True
                continue
        return {"events": events, "partial": partial}

    return await asyncio.to_thread(_run)
