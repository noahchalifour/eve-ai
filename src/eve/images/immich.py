"""Immich photographs into `eve_image`, as a one-day cache (spec 2.4, 3.4).

Returns the image id or None - never a broken id. A specialist still returns
a string; it cites what this returns as `[image <short-id>]`, and Eve places
that id in a surface. The Immich API key stays in eve-tools.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging

from starlette.concurrency import run_in_threadpool

from eve.images import store
from eve.images.process import normalise
from eve.tools_client import invoke

logger = logging.getLogger(__name__)


async def from_immich(member_sub: str, asset_id: str, *, thread_id: str | None) -> str | None:
    raw = await invoke("immich.asset_image", {"asset_id": asset_id}, timeout=30.0)
    if raw.startswith("error:"):
        logger.warning("immich preview for %s unavailable: %s", asset_id, raw)
        return None
    try:
        data = base64.b64decode(json.loads(raw)["base64"], validate=True)
        image = await run_in_threadpool(normalise, data)
    except (ValueError, KeyError, TypeError, binascii.Error):
        logger.warning("immich preview for %s was not a usable image", asset_id)
        return None
    row = await store.put(
        member_sub, image, origin="immich", thread_id=thread_id, source_ref=asset_id
    )
    return row.id
