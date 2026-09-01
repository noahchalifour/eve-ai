"""The sync, and the one text rendering of a wardrobe.

`sync` is a batch job. It is the only place in the deployment that moves image
bytes, and it does so between eve-tools and `vision.describe` and nowhere
else - by the time anything here returns, the wardrobe is text.

`render_wardrobe` is the other half: the whole catalogue as one string for a
prompt. It lives here rather than in the stylist so it can be tested without
a model, and so the stylist's tool stays a two-line wrapper.
"""

from __future__ import annotations

import json
import logging

from eve.family import get_family
from eve.tools_client import invoke
from eve.wardrobe import store, vision

logger = logging.getLogger(__name__)

NO_ALBUM = (
    "No wardrobe album is configured for this member. Add a `wardrobe_album` "
    "to their entry in family.yaml with the id of their Immich album."
)
EMPTY = (
    "The wardrobe catalogue is empty. Photograph the clothes into the Immich "
    "album and run `eve-wardrobe sync` (or ask me to sync it)."
)

# ponytail: the whole wardrobe in one string, roughly 6k tokens at 200
# garments. Enumeration is the entire reason a catalogue beat a similarity
# search, so filtering here would defeat the point - but past ~300 items this
# stops being sensible and the tool grows a `category` argument.
_CATEGORY_ORDER = ("full", "outerwear", "top", "bottom", "footwear", "accessory")


def album_for(member_sub: str) -> str | None:
    """The member's Immich album id, from the roster. `None` when they have
    no wardrobe configured - not an error, just nothing to sync."""
    return get_family().get(member_sub).wardrobe_album


async def _album_asset_list(album_id: str) -> tuple[list[dict], str | None]:
    """`(assets, error)`. `invoke` returns a JSON string, or a string starting
    with `error:` - it never raises, which is why nothing here does either."""
    raw = await invoke("immich.album_assets", {"album_id": album_id})
    if raw.startswith("error:"):
        return [], raw
    try:
        return json.loads(raw).get("assets", []), None
    except (ValueError, AttributeError):
        return [], "error: Immich returned something that was not an album"


async def sync(
    member_sub: str, *, force: bool = False, limit: int | None = None
) -> dict:
    """Bring the catalogue in line with the album.

    `limit` bounds how many photographs one call will describe, so the
    conversational `sync_wardrobe` tool cannot spend a whole turn on a
    hundred-photo first run; `remaining` tells the caller what it left.
    """
    result = {"catalogued": 0, "removed": 0, "failed": 0, "remaining": 0, "error": None}

    album_id = album_for(member_sub)
    if not album_id:
        result["error"] = NO_ALBUM
        return result

    assets, error = await _album_asset_list(album_id)
    if error:
        result["error"] = error
        return result

    known = await store.catalogued_asset_ids(member_sub)
    in_album = {asset["id"] for asset in assets}

    departed = sorted(known - in_album)
    if departed:
        await store.delete_assets(member_sub, departed)
        result["removed"] = len(departed)

    todo = [a for a in assets if force or a["id"] not in known]
    if limit is not None and len(todo) > limit:
        result["remaining"] = len(todo) - limit
        todo = todo[:limit]

    for asset in todo:
        try:
            raw = await invoke("immich.asset_image", {"asset_id": asset["id"]})
            if raw.startswith("error:"):
                raise RuntimeError(raw)
            payload = json.loads(raw)
            items = await vision.describe(
                payload["base64"], payload.get("content_type", "image/jpeg")
            )
        except Exception:
            # One unreadable photograph must not cost the other ninety-nine.
            logger.warning(
                "could not catalogue asset %s for %s", asset["id"], member_sub,
                exc_info=True,
            )
            result["failed"] += 1
            continue
        await store.insert_items(
            member_sub, asset["id"], [vision.to_row(item) for item in items]
        )
        result["catalogued"] += len(items)

    return result


def _render_item(item: dict) -> str:
    attrs = item.get("attrs") or {}
    parts = [
        attrs.get("fabric"),
        attrs.get("pattern"),
        f"warmth {attrs['warmth']}" if attrs.get("warmth") else None,
        f"formality {attrs['formality']}" if attrs.get("formality") else None,
        attrs.get("season"),
        attrs.get("notes"),
    ]
    detail = ", ".join(p for p in parts if p)
    return f"- {item['name']}" + (f" ({detail})" if detail else "")


async def _staleness_note(member_sub: str, album_id: str) -> str:
    """One extra API call, no vision, no measurable latency - and the
    alternative is a stylist confidently dressing someone out of a wardrobe
    missing the coat they bought last week.

    Degrades to silence: if Immich cannot be reached, the catalogue we have is
    still worth answering from, and a failure to CHECK for staleness is not
    itself worth reporting to a member asking what to wear.
    """
    assets, error = await _album_asset_list(album_id)
    if error:
        return ""
    uncatalogued = len({a["id"] for a in assets} - await store.catalogued_asset_ids(member_sub))
    if not uncatalogued:
        return ""
    return (
        f"\n\nNote: {uncatalogued} photo(s) in the album have not been "
        "catalogued yet, so this list may be incomplete."
    )


async def render_wardrobe(member_sub: str) -> str:
    """The whole catalogue as one string, grouped by category."""
    album_id = album_for(member_sub)
    if not album_id:
        return NO_ALBUM

    items = await store.list_items(member_sub)
    if not items:
        return EMPTY

    by_category: dict[str, list[dict]] = {}
    for item in items:
        by_category.setdefault(item["category"], []).append(item)

    ordered = [c for c in _CATEGORY_ORDER if c in by_category]
    ordered += sorted(c for c in by_category if c not in _CATEGORY_ORDER)

    sections = [
        f"## {category}\n" + "\n".join(_render_item(i) for i in by_category[category])
        for category in ordered
    ]
    return "\n\n".join(sections) + await _staleness_note(member_sub, album_id)
