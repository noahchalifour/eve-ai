"""Stylist specialist: what to wear today, from the clothes the member owns.

The first specialist whose subject is a set of objects rather than a service
API. The objects are photographs in an Immich album, catalogued into
`eve_wardrobe_item` by `eve.wardrobe.catalog` - so every tool here reads text
and no image ever enters this loop (design doc, "Eve cannot show you a
photograph" and "How Eve perceives a wardrobe").

Permission is checked twice, the pattern `mail.py` established: the coarse
`wardrobe` grant at the Eve -> stylist edge, and the fine `calendar.read`
grant inside `list_events`.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.specialists.permissions import permission_denial
from eve.tools_client import invoke
from eve.wardrobe import catalog

SYSTEM_PROMPT = Path("prompts/stylist.md").read_text()

# ponytail: a flat cap on one conversational sync, so "I added some clothes"
# cannot spend a whole turn describing a hundred photographs. The CLI is the
# unbounded caller; this one reports what it left behind.
SYNC_LIMIT = 10

# The rest of today, roughly. The calendar handler takes minutes-ahead and a
# day horizon; a stylist cares about what is left of this day, not a fortnight.
_LOOKAHEAD_MINUTES = 960
_HORIZON_DAYS = 1


if "_model_for_test" not in globals():

    def _model_for_test():
        """Indirection so unit tests can substitute a fake model, via
        importlib.reload, without a live LiteLLM call at import time."""
        return get_model(Tier.MECHANICAL)


def _member(config: RunnableConfig) -> dict:
    return config["configurable"]["member"]


@tool
async def read_wardrobe(config: RunnableConfig) -> str:
    """Read the member's whole wardrobe catalogue, grouped by category. Call
    this before recommending anything: it is the only list of clothes they
    actually own, and it changes between requests."""
    return await catalog.render_wardrobe(_member(config)["sub"])


@tool
async def todays_weather(config: RunnableConfig) -> str:
    """Today's forecast for the household."""
    return await invoke("home.weather", {})


@tool
async def list_events(config: RunnableConfig) -> str:
    """What is on the member's calendar for the rest of today. Requires the
    calendar.read permission."""
    member = _member(config)
    denial = permission_denial(member.get("permissions", []), "calendar.read")
    if denial:
        return denial
    return await invoke(
        "calendar.list_events",
        {
            "member_sub": member["sub"],
            "lookahead_minutes": _LOOKAHEAD_MINUTES,
            "horizon_days": _HORIZON_DAYS,
        },
    )


@tool
async def sync_wardrobe(config: RunnableConfig) -> str:
    """Catalogue any new photos the member has added to their Immich wardrobe
    album. Use when they say they have added or removed clothes, or when the
    catalogue reports itself stale."""
    result = await catalog.sync(_member(config)["sub"], limit=SYNC_LIMIT)
    if result["error"]:
        return result["error"]
    parts = [f"Catalogued {result['catalogued']} new garment(s)"]
    if result["removed"]:
        parts.append(f"removed {result['removed']} no longer in the album")
    if result["failed"]:
        parts.append(f"{result['failed']} photo(s) could not be read")
    if result["remaining"]:
        parts.append(
            f"{result['remaining']} photo(s) still uncatalogued - sync again to finish"
        )
    return ", ".join(parts) + "."


ask_stylist = build_specialist(
    name="stylist",
    tools=[read_wardrobe, todays_weather, list_events, sync_wardrobe],
    system_prompt=SYSTEM_PROMPT,
    permission="wardrobe",
    model_factory=lambda _tier: _model_for_test(),
)
