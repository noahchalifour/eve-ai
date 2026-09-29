"""The member's upcoming events, soonest first."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from eve.settings import get_settings
from eve.widgets.sources.base import ReadContext, SourceType, call_tool, register


class CalendarParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    horizon_days: int = Field(default=7, ge=1, le=14)
    limit: int = Field(default=5, ge=1, le=10)


def _zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().household_timezone)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _when(start: object, zone: ZoneInfo) -> str:
    if not isinstance(start, str):
        return ""
    moment = datetime.fromisoformat(start).astimezone(zone)
    return moment.strftime("%a %-I:%M %p")


async def _read(ctx: ReadContext, params: CalendarParams) -> dict:
    body = await call_tool("calendar.list_events", {
        "member_sub": ctx.member_sub, "lookahead_minutes": 0, "horizon_days": params.horizon_days,
    })
    events = sorted(
        (event for event in body.get("events") or [] if isinstance(event, dict)),
        key=lambda event: event.get("start") or "",
    )
    zone = _zone()
    items = [
        {"summary": event.get("summary") or "Untitled", "when": _when(event.get("start"), zone),
         "location": event.get("location") or ""}
        for event in events[: params.limit]
    ]
    return {
        "next": items[0] if items else {"summary": "Nothing scheduled", "when": "", "location": ""},
        "items": items,
        "count": f"{len(events)} upcoming",
    }


register(SourceType(
    name="calendar",
    params=CalendarParams,
    fields=frozenset({"next", "items", "count"}),
    read=_read,
    ttl_seconds=300,
    item_fields={"items": frozenset({"summary", "when", "location"})},
    description="The member's upcoming events. Params: horizon_days (1-14), limit (1-10). Fields: next, items "
    "(each {summary, when, location}), count.",
))
