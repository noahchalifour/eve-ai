"""Stylist specialist: what to wear today, from the clothes the member owns.

The first specialist whose subject is a set of objects rather than a service
API. The objects are photographs in an Immich album, catalogued into
`eve_wardrobe_item` by `eve.wardrobe.catalog` - so every tool here reads text.
No image enters this loop as pixels, even now that `photo_of` exists: it only
mints an id the phone can later resolve (design doc, "Eve cannot show you a
photograph" and "How Eve perceives a wardrobe").

Permission is checked twice, the pattern `mail.py` established: the coarse
`wardrobe` grant at the Eve -> stylist edge, and the fine `calendar.read`
grant inside `list_events`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.images import store as image_store
from eve.images.immich import from_immich
from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.specialists.permissions import permission_denial
from eve.tools_client import invoke
from eve.wardrobe import catalog
from eve.wardrobe import store as wardrobe_store

logger = logging.getLogger(__name__)

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
    try:
        return await catalog.render_wardrobe(_member(config)["sub"])
    except Exception as exc:
        # The binding contract: a tool returns a string and never raises -
        # a database failure here must not cost the whole specialist turn.
        logger.warning("read_wardrobe failed for %s", _member(config)["sub"], exc_info=exc)
        return f"error: the wardrobe catalogue is unavailable ({exc.__class__.__name__})"


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
    try:
        result = await catalog.sync(_member(config)["sub"], limit=SYNC_LIMIT)
    except Exception as exc:
        logger.warning(
            "sync_wardrobe failed for %s", _member(config)["sub"], exc_info=exc
        )
        return f"error: the wardrobe sync could not run ({exc.__class__.__name__})"
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


@tool
async def photo_of(garment: str, config: RunnableConfig) -> str:
    """Get a photo of one garment from the wardrobe, to show the member.

    `garment` is the name exactly as `read_wardrobe` lists it. Returns
    `[image <id>] <name>`; put that `[image <id>]` in your answer so Eve can
    show it. Call it only for garments you are recommending.
    """
    member = _member(config)
    wanted = garment.strip().lower()
    items = await wardrobe_store.list_items(member["sub"])
    match = next((i for i in items if i["name"].strip().lower() == wanted), None)
    if match is None:
        return f"There is no garment called {garment!r} in the wardrobe."
    thread_id = (config.get("configurable") or {}).get("thread_id")
    image_id = await from_immich(member["sub"], match["asset_id"], thread_id=thread_id)
    if image_id is None:
        return f"The photo of {match['name']} could not be fetched right now."
    return f"[image {image_store.short_id(image_id)}] {match['name']}"


ask_stylist = build_specialist(
    name="stylist",
    tools=[read_wardrobe, todays_weather, list_events, sync_wardrobe, photo_of],
    system_prompt=SYSTEM_PROMPT,
    permission="wardrobe",
    model_factory=lambda _tier: _model_for_test(),
    accepts_images=True,
)
