"""Latest health entries (recovery, sleep, activity), for a stat tile."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from eve.widgets.sources.base import ReadContext, SourceError, SourceType, call_tool, register, static_permission


class HealthParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["recovery", "sleep", "activity"]
    days: int = Field(default=7, ge=1, le=14)


async def _read(ctx: ReadContext, params: HealthParams) -> dict:
    body = await call_tool(
        f"health.get_{params.metric}", {"member_sub": ctx.member_sub, "days": params.days}
    )
    items = body.get(params.metric)
    if not isinstance(items, list):
        raise SourceError(f"health.get_{params.metric} had no list")
    items = [item for item in items if isinstance(item, dict)]
    # Newest first is the tool's contract; `latest` is the tile's headline.
    return {"latest": items[0] if items else {}, "items": items}


register(SourceType(
    name="health",
    params=HealthParams,
    fields=frozenset({"latest", "items"}),
    read=_read,
    ttl_seconds=900,
    item_fields={"items": frozenset()},
    permissions=static_permission("health"),
    description="Recent health entries, newest first. Params: metric (recovery|sleep|activity), days (1-14). "
    "Fields: latest (newest entry), items (list). Entry keys depend on the provider, e.g. date, score_0_100.",
))
