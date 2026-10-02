"""The closed cadence vocabulary.

Four shapes, validated in Python rather than by columns, for the reason the
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

_KEYS = ("every_hours", "daily_at", "weekly_at", "once_at")

# A one-shot cadence (ENG-372 reminders) may be at most this far ahead.
MAX_ONCE_AHEAD_DAYS = 366


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
            "a cadence has exactly one of every_hours, daily_at, weekly_at, or once_at"
        )

    if "once_at" in cadence:
        # Shape only. Whether the moment is still in the future is the
        # caller's question, because it depends on "now" and this module is
        # pure.
        if _parse_moment(cadence["once_at"]) is None:
            return "once_at must be an ISO-8601 timestamp with an offset, like \"2026-10-02T15:00:00-07:00\""
        return None

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


def _parse_moment(raw: object) -> datetime | None:
    """An aware datetime for a once_at value, or None. A naive value is
    refused rather than guessed at: the tools resolve local time to an
    offset before storing, so a naive one here means a caller skipped that."""
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def is_one_shot(cadence: dict) -> bool:
    return isinstance(cadence, dict) and "once_at" in cadence


def validate_at(cadence: dict, now: datetime) -> str | None:
    """`validate`, plus the one rule that needs a clock: a one-shot must be
    in the future and within MAX_ONCE_AHEAD_DAYS. A past moment would fire
    on the very next tick, which is never what was asked for."""
    error = validate(cadence)
    if error is not None or not is_one_shot(cadence):
        return error
    moment = _parse_moment(cadence["once_at"])
    if moment <= now:
        return "once_at is in the past"
    if moment - now > timedelta(days=MAX_ONCE_AHEAD_DAYS):
        return f"once_at may be at most {MAX_ONCE_AHEAD_DAYS} days ahead"
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

    if "once_at" in cadence:
        # A one-shot fires at its moment and never again. "Strictly after"
        # does not hold once it has fired, and callers retire the row
        # (`expired`) rather than rescheduling it, so the moment itself is
        # returned either way.
        return _parse_moment(cadence["once_at"]).astimezone(ZoneInfo("UTC"))

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
    if "once_at" in cadence:
        return f"once, at {cadence['once_at']}"
    if "every_hours" in cadence:
        hours = int(cadence["every_hours"])
        return "every hour" if hours == 1 else f"every {hours} hours"
    if "daily_at" in cadence:
        return f"every day at {cadence['daily_at']}"
    weekly = cadence["weekly_at"]
    return f"every {str(weekly['day']).capitalize()} at {weekly['time']}"
