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


def _to_dict(raw: str) -> dict | None:
    try:
        component = next(
            part
            for part in icalendar.Calendar.from_ical(raw).walk()
            if part.name == "VEVENT"
        )
    except Exception:
        logger.warning("skipping an unparseable calendar event")
        return None
    return {
        "uid": str(component.get("uid", "")),
        # A content hash, not a server etag: no extra request, and it works
        # on servers that omit etags from search results.
        "revision": hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16],
        "summary": str(component.get("summary", "")),
        "location": str(component.get("location", "")),
        "start": _as_utc_iso(getattr(component.get("dtstart"), "dt", None)),
        "end": _as_utc_iso(getattr(component.get("dtend"), "dt", None)),
    }


async def list_events(member_sub: str, lookahead_minutes: int) -> dict:
    def _run() -> dict:
        try:
            calendars = _calendars(member_sub)
        except Exception:
            logger.warning("no reachable calendar for %s", member_sub, exc_info=True)
            return {"events": []}
        start = datetime.now(UTC)
        end = start + timedelta(minutes=lookahead_minutes)
        events = []
        for calendar in calendars:
            for found in calendar.search(start=start, end=end, event=True, expand=True):
                parsed = _to_dict(found.data)
                if parsed and parsed["uid"]:
                    events.append(parsed)
        return {"events": events}

    return await asyncio.to_thread(_run)
