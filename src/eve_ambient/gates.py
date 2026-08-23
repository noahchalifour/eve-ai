"""The gates between a signal and an interruption. Pure functions only: no
I/O, no clock reads, no database. The pipeline supplies `now` and the counts.

Every gate fails closed. An unmapped source notifies nobody; an unknown
subject is dropped; an unparseable quiet-hours window is treated as not
quiet, because the failure that silences Eve forever is worse than the one
that lets a notification through at a bad hour.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eve.family import UnknownMemberError, get_family
from eve.specialists.permissions import permission_denial
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

# The strings the roster actually grants. `home.control` rather than a
# read-only equivalent because no read-only home grant exists: whoever may
# operate the house is who gets told about it.
SOURCE_PERMISSION: dict[str, str] = {
    "calendar": "calendar.read",
    "mail": "mail.read",
    "finances": "finances",
    "home": "home.control",
}

# Sources whose content belongs to one member and may not be redistributed,
# whatever the filter decides (design section 5).
_OWNER_ONLY = {"mail"}


def scoped_audience(signal: Signal, audience: list[str]) -> list[str]:
    """An owner-only signal notifies its owner and nobody else — including
    when the filter named somebody else instead. The filter decides *whether*
    for these sources, never *who*."""
    if signal.source in _OWNER_ONLY and signal.member_sub:
        return [signal.member_sub]
    return list(audience)


def permitted(signal: Signal, subs: list[str]) -> list[str]:
    required = SOURCE_PERMISSION.get(signal.source)
    if required is None:
        logger.warning("no permission mapping for source %r; notifying nobody", signal.source)
        return []
    family = get_family()
    kept = []
    for sub in subs:
        try:
            member = family.get(sub)
        except UnknownMemberError:
            logger.warning("filter named an unknown subject %r", sub)
            continue
        if permission_denial(sorted(member.permissions), required) is None:
            kept.append(sub)
        else:
            logger.info("dropping %s: lacks %s for a %s signal", sub, required, signal.source)
    return kept


def parse_window(window: str) -> tuple[time, time]:
    start_text, end_text = window.split("-", 1)
    return time.fromisoformat(start_text.strip()), time.fromisoformat(end_text.strip())


def in_quiet_hours(when_local: datetime, window: str) -> bool:
    try:
        start, end = parse_window(window)
    except (AttributeError, ValueError):
        logger.warning("unparseable quiet-hours window %r; treating as never quiet", window)
        return False
    now = when_local.time()
    if start <= end:
        return start <= now < end
    # Wraps midnight: quiet from `start` to the end of the day, and from the
    # start of the day to `end`.
    return now >= start or now < end


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown timezone %r; falling back to UTC", timezone)
        return ZoneInfo("UTC")


def local_now(timezone: str, now_utc: datetime) -> datetime:
    return now_utc.astimezone(_zone(timezone))


def day_start_utc(timezone: str, now_utc: datetime) -> datetime:
    """Midnight of the member's current local day, expressed in UTC. This is
    the lower bound of the daily-cap count."""
    local = local_now(timezone, now_utc)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(now_utc.tzinfo or _zone("UTC"))
