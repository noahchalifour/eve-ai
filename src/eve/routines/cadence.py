"""The closed cadence vocabulary.

Three shapes, validated in Python rather than by columns, for the reason the
widget recipe is jsonb: the set of legal shapes grows and a migration per
shape is the cost that choice exists to avoid.

Pure functions only. This module imports nothing from `eve`, which is what
lets the tools, the store, and the ambient source all depend on it without a
cycle, and what makes every rule here testable without a database.

Closed on purpose. A cron expression is more expressive and strictly worse
here: a model-authored `* * * * *` is a paid VOICE turn every minute, and
neither a mobile editor nor a plain-English rendering can be built over an
open grammar.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIN_EVERY_HOURS = 1
MAX_EVERY_HOURS = 168

_DAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_KEYS = ("every_hours", "daily_at", "weekly_at")


def _parse_time(raw: object) -> time | None:
    if not isinstance(raw, str):
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError:
        return None


def validate(cadence: object) -> str | None:
    """None when the cadence is legal; otherwise a diagnostic a model can act
    on. Every rejection names what was wrong and what is legal, because the
    caller is a language model retrying from the message."""
    if not isinstance(cadence, dict):
        return "a cadence must be an object"

    present = [key for key in _KEYS if key in cadence]
    if len(present) != 1 or len(cadence) != 1:
        return (
            "a cadence has exactly one of every_hours, daily_at, or weekly_at"
        )

    if "every_hours" in cadence:
        hours = cadence["every_hours"]
        # bool is an int subclass; True must not read as 1.
        if not isinstance(hours, int) or isinstance(hours, bool):
            return "every_hours must be a whole number of hours"
        if hours < MIN_EVERY_HOURS or hours > MAX_EVERY_HOURS:
            return (
                f"every_hours must be between {MIN_EVERY_HOURS} and "
                f"{MAX_EVERY_HOURS}; nothing may run more often than hourly"
            )
        return None

    if "daily_at" in cadence:
        if _parse_time(cadence["daily_at"]) is None:
            return "daily_at must be a 24-hour time like \"08:00\""
        return None

    weekly = cadence["weekly_at"]
    if not isinstance(weekly, dict):
        return "weekly_at must be an object with day and time"
    day = weekly.get("day")
    if not isinstance(day, str) or day.lower() not in _DAYS:
        return f"weekly_at.day must be one of: {', '.join(_DAYS)}"
    if _parse_time(weekly.get("time")) is None:
        return "weekly_at.time must be a 24-hour time like \"19:00\""
    return None


def _zone(timezone: str) -> ZoneInfo:
    """An unknown timezone degrades to UTC rather than raising, the same
    posture `eve_ambient.gates._zone` takes: a bad timezone string must not
    be able to stop a routine from ever firing again."""
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _at_local(local_day: datetime, at: time) -> datetime:
    return local_day.replace(
        hour=at.hour, minute=at.minute, second=0, microsecond=0
    )


def next_after(cadence: dict, timezone: str, after: datetime) -> datetime:
    """The next firing time strictly after `after`, in UTC.

    Computed in local time and converted, not the other way round, which is
    what makes 08:00 stay 08:00 across a DST transition. The stored
    `timezone` column exists for exactly this: a member who moves keeps their
    existing routines firing at the local times they chose.
    """
    zone = _zone(timezone)

    if "every_hours" in cadence:
        return after + timedelta(hours=int(cadence["every_hours"]))

    if "daily_at" in cadence:
        at = _parse_time(cadence["daily_at"])
        local = after.astimezone(zone)
        candidate = _at_local(local, at)
        if candidate <= local:
            candidate = _at_local(local + timedelta(days=1), at)
        return candidate.astimezone(ZoneInfo("UTC"))

    weekly = cadence["weekly_at"]
    at = _parse_time(weekly["time"])
    target = _DAYS.index(str(weekly["day"]).lower())
    local = after.astimezone(zone)
    ahead = (target - local.weekday()) % 7
    candidate = _at_local(local + timedelta(days=ahead), at)
    if candidate <= local:
        candidate = _at_local(local + timedelta(days=ahead + 7), at)
    return candidate.astimezone(ZoneInfo("UTC"))


def describe(cadence: dict) -> str:
    """English, for Eve's spoken confirmation and the compose prompt. The
    client renders its own prose from the structured object rather than
    consuming this, so the two never have to agree on wording."""
    if "every_hours" in cadence:
        hours = int(cadence["every_hours"])
        return "every hour" if hours == 1 else f"every {hours} hours"
    if "daily_at" in cadence:
        return f"every day at {cadence['daily_at']}"
    weekly = cadence["weekly_at"]
    return f"every {str(weekly['day']).capitalize()} at {weekly['time']}"
