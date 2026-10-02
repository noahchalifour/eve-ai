"""`date_time`: time-zone and calendar arithmetic, locally (ENG-372).

One tool with a closed `operation` vocabulary rather than four tiny tools:
each would carry the same timezone plumbing, and the model picks an
operation as easily as a tool name.

Relative phrases ("tomorrow", "friday", "in 20 minutes", "6 weeks") are
resolved HERE against the member's own local clock, because that arithmetic
is exactly what a language model gets subtly wrong. The parser is
deliberately small and closed; anything it does not recognise is an error
the model can act on, never a guess.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_UNITS = {
    "minute": "minutes", "minutes": "minutes", "min": "minutes", "mins": "minutes",
    "hour": "hours", "hours": "hours", "hr": "hours", "hrs": "hours",
    "day": "days", "days": "days",
    "week": "weeks", "weeks": "weeks",
    "second": "seconds", "seconds": "seconds", "sec": "seconds", "secs": "seconds",
}
# Months and years are not fixed-length; "add 1 month" goes through
# _add_months so Jan 31 + 1 month lands on the last day of February.
_CALENDAR_UNITS = {"month": "months", "months": "months", "year": "years", "years": "years"}

# Friendly names for the zones people actually say. Anything else must be an
# IANA name (America/Vancouver).
_ZONE_ALIASES = {
    "utc": "UTC", "gmt": "UTC", "z": "UTC",
    "pacific": "America/Los_Angeles", "pt": "America/Los_Angeles", "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles",
    "mountain": "America/Denver", "mt": "America/Denver", "mst": "America/Denver", "mdt": "America/Denver",
    "central": "America/Chicago", "ct": "America/Chicago", "cst": "America/Chicago", "cdt": "America/Chicago",
    "eastern": "America/New_York", "et": "America/New_York", "est": "America/New_York", "edt": "America/New_York",
    "atlantic": "America/Halifax", "newfoundland": "America/St_Johns",
    "london": "Europe/London", "uk": "Europe/London", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "sydney": "Australia/Sydney",
    "montreal": "America/Toronto", "toronto": "America/Toronto", "vancouver": "America/Vancouver",
    "new york": "America/New_York", "los angeles": "America/Los_Angeles", "hawaii": "Pacific/Honolulu",
    "india": "Asia/Kolkata", "ist": "Asia/Kolkata", "beijing": "Asia/Shanghai", "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "singapore": "Asia/Singapore", "dubai": "Asia/Dubai",
}


class TimeError(ValueError):
    pass


def zone(name: str | None, default: str) -> ZoneInfo:
    raw = (name or default or "UTC").strip()
    key = _ZONE_ALIASES.get(raw.lower(), raw)
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TimeError(f"unknown timezone {raw!r}; use an IANA name like America/Toronto") from exc


def _clock(text: str) -> time | None:
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", text.strip(), re.IGNORECASE)
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        pm = meridiem.lower().startswith("p")
        hour = (hour % 12) + (12 if pm else 0)
    elif match.group(2) is None:
        return None  # a bare "3" is not a time
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _duration(text: str) -> tuple[timedelta, int] | None:
    """(fixed timedelta, months) for "20 minutes", "2 hours 30 minutes",
    "6 weeks", "1 month", "1h30m"."""
    text = text.strip().lower().replace(",", " ").replace(" and ", " ")
    compact = re.fullmatch(r"(?:(\d+)h)?\s*(?:(\d+)m)?", text)
    if compact and (compact.group(1) or compact.group(2)):
        return timedelta(hours=int(compact.group(1) or 0), minutes=int(compact.group(2) or 0)), 0
    parts = re.findall(r"(\d+(?:\.\d+)?|an?|one)\s*([a-z]+)", text)
    if not parts or re.sub(r"(\d+(?:\.\d+)?|an?|one)\s*([a-z]+)", "", text).strip():
        return None
    fixed = timedelta()
    months = 0
    for amount, unit in parts:
        value = 1.0 if amount in ("a", "an", "one") else float(amount)
        if unit in _UNITS:
            fixed += timedelta(**{_UNITS[unit]: value})
        elif unit in _CALENDAR_UNITS:
            if not value.is_integer():
                return None
            months += int(value) * (12 if _CALENDAR_UNITS[unit] == "years" else 1)
        else:
            return None
    return fixed, months


def _add_months(moment: datetime, months: int) -> datetime:
    if not months:
        return moment
    month_index = moment.month - 1 + months
    year, month = moment.year + month_index // 12, month_index % 12 + 1
    following = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (following - timedelta(days=1)).day
    return moment.replace(year=year, month=month, day=min(moment.day, last_day))


def shift(moment: datetime, fixed: timedelta, months: int, sign: int = 1) -> datetime:
    """`moment` moved by a duration, the way people mean it: months, weeks
    and days keep the wall-clock time ("in 2 days" at 9am is 9am, across a
    DST change), while hours and minutes are elapsed time ("in 3 hours" is
    three real hours). Python's aware arithmetic does wall-clock for both,
    hence this."""
    tz = moment.tzinfo
    whole_days = timedelta(days=fixed.days)
    rest = fixed - whole_days
    moment = _add_months(moment, sign * months)
    moment = (moment.replace(tzinfo=None) + sign * whole_days).replace(tzinfo=tz)
    utc = ZoneInfo("UTC")
    return (moment.astimezone(utc) + sign * rest).astimezone(tz)


def elapsed(first: datetime, second: datetime) -> timedelta:
    utc = ZoneInfo("UTC")
    return second.astimezone(utc) - first.astimezone(utc)


def parse_moment(text: str, tz: ZoneInfo, now: datetime) -> datetime:
    """An aware datetime in `tz` for `text`.

    Accepts ISO-8601 ("2026-12-25", "2026-12-25T15:00", with or without an
    offset), "now", "today"/"tomorrow"/"yesterday", a weekday ("friday",
    "next friday"), "in <duration>", "<duration> from <moment>", an optional
    trailing clock time ("tomorrow at 3pm", "friday 09:30"), a bare clock time
    ("3pm" - today, or tomorrow if already past), and "christmas"/"new year".
    """
    raw = (text or "").strip()
    if not raw:
        raise TimeError("no time given")
    local_now = now.astimezone(tz)
    lowered = raw.lower().strip().rstrip(".")

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed.astimezone(tz) if parsed.tzinfo else parsed.replace(tzinfo=tz)

    if lowered == "now":
        return local_now

    match = re.fullmatch(r"in\s+(.+)", lowered)
    if match:
        span = _duration(match.group(1))
        if span is None:
            raise TimeError(f"cannot read the duration in {raw!r}")
        return shift(local_now, span[0], span[1])

    match = re.fullmatch(r"(.+?)\s+(?:from|after)\s+(.+)", lowered)
    if match:
        span = _duration(match.group(1))
        if span is not None:
            base = parse_moment(match.group(2), tz, now)
            return shift(base, span[0], span[1])

    match = re.fullmatch(r"(.+?)\s+(?:before|ago)(?:\s+(.+))?", lowered)
    if match:
        span = _duration(match.group(1))
        if span is not None:
            base = parse_moment(match.group(2), tz, now) if match.group(2) else local_now
            return shift(base, span[0], span[1], -1)

    clock: time | None = None
    match = re.fullmatch(r"(.+?)\s+(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)", lowered)
    if match and _clock(match.group(2)) is not None and _day(match.group(1), local_now) is not None:
        clock = _clock(match.group(2))
        lowered = match.group(1).strip()

    day = _day(lowered, local_now)
    if day is not None:
        at = clock or time(0, 0)
        return datetime.combine(day, at, tzinfo=tz)

    bare = _clock(lowered.removeprefix("at ").strip())
    if bare is not None:
        candidate = datetime.combine(local_now.date(), bare, tzinfo=tz)
        if candidate <= local_now:
            candidate = datetime.combine(local_now.date() + timedelta(days=1), bare, tzinfo=tz)
        return candidate

    raise TimeError(
        f"cannot read {raw!r}; use ISO-8601 (2026-12-25T15:00), a weekday, "
        "today/tomorrow, 'in 20 minutes', or a clock time like 3pm"
    )


def _day(text: str, local_now: datetime) -> date | None:
    text = text.strip()
    today = local_now.date()
    named = {"today": today, "tonight": today, "tomorrow": today + timedelta(days=1),
             "yesterday": today - timedelta(days=1)}
    if text in named:
        return named[text]
    if text in ("christmas", "christmas day", "xmas"):
        target = date(today.year, 12, 25)
        return target if target >= today else date(today.year + 1, 12, 25)
    if text in ("new year", "new year's day", "new years day", "new years"):
        return date(today.year + 1, 1, 1)
    match = re.fullmatch(r"(?:(this|next|last)\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", text)
    if match:
        qualifier, name = match.groups()
        target = _DAYS.index(name)
        ahead = (target - today.weekday()) % 7
        if qualifier == "last":
            back = (today.weekday() - target) % 7 or 7
            return today - timedelta(days=back)
        if qualifier == "next" and ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead)
    return None


def _render(moment: datetime) -> str:
    return f"{moment.strftime('%A %Y-%m-%d %H:%M %Z')} ({moment.isoformat(timespec='minutes')})"


def _render_span(delta: timedelta) -> str:
    sign = "-" if delta.total_seconds() < 0 else ""
    seconds = abs(int(delta.total_seconds()))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes or not parts:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    weeks = f" (about {days / 7:.1f} weeks)" if days >= 14 else ""
    return sign + ", ".join(parts) + weeks


def run(operation: str, member_tz: str, now: datetime, *, time_text: str | None = None,
        timezone: str | None = None, to_timezone: str | None = None,
        start: str | None = None, end: str | None = None, duration: str | None = None) -> str:
    op = (operation or "").strip().lower()
    home = zone(None, member_tz)
    if op == "now":
        tz = zone(timezone, member_tz)
        return f"It is {_render(now.astimezone(tz))}."
    if op == "convert":
        if not time_text:
            raise TimeError("convert needs `time`")
        source = zone(timezone, member_tz)
        target = zone(to_timezone, member_tz)
        moment = parse_moment(time_text, source, now)
        return f"{_render(moment)} is {_render(moment.astimezone(target))}."
    if op == "diff":
        first = parse_moment(start or "now", home, now)
        second = parse_moment(end or "now", home, now)
        delta = elapsed(first, second)
        calendar_days = (second.date() - first.date()).days
        return (
            f"From {_render(first)} to {_render(second)}: {_render_span(delta)}; "
            f"{calendar_days} calendar day{'s' if abs(calendar_days) != 1 else ''}."
        )
    if op == "add":
        if not duration:
            raise TimeError("add needs `duration`, e.g. '6 weeks' or '-3 days'")
        base = parse_moment(start or "now", home, now)
        text = duration.strip()
        negative = text.startswith("-")
        span = _duration(text.lstrip("-+ "))
        if span is None:
            raise TimeError(f"cannot read the duration {duration!r}")
        fixed, months = span
        result = shift(base, fixed, months, -1 if negative else 1)
        return f"{_render(base)} {'minus' if negative else 'plus'} {text.lstrip('-+ ')} is {_render(result)}."
    raise TimeError("operation must be one of: now, convert, diff, add")


def _member(config: RunnableConfig) -> dict:
    return (config.get("configurable") or {}).get("member") or {}


@tool
def date_time(
    operation: str,
    config: RunnableConfig,
    time: str | None = None,
    timezone: str | None = None,
    to_timezone: str | None = None,
    start: str | None = None,
    end: str | None = None,
    duration: str | None = None,
) -> str:
    """Exact time-zone and date arithmetic, in the member's own timezone by
    default. Use instead of working dates out in your head.

    operation is one of:
      now      - current time; `timezone` optional ("Tokyo", "Europe/Paris")
      convert  - `time` in `timezone` (default: member's) to `to_timezone`,
                 e.g. time="3pm", timezone="Pacific"
      diff     - between `start` and `end` (each defaults to now),
                 e.g. end="christmas" for "how many days until Christmas"
      add      - `duration` added to `start` (default now), e.g.
                 start="friday", duration="6 weeks"; prefix "-" to subtract
    Times may be ISO-8601, "tomorrow at 3pm", "next friday", "in 20 minutes".
    """
    from datetime import UTC, datetime as _dt

    member_tz = _member(config).get("timezone") or "UTC"
    try:
        return run(operation, member_tz, _dt.now(UTC), time_text=time, timezone=timezone,
                   to_timezone=to_timezone, start=start, end=end, duration=duration)
    except TimeError as exc:
        return f"error: {exc}"
