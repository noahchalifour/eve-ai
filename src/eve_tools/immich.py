"""Immich REST client. No SDK: two GETs behind a long-lived API key, and a
dependency buys nothing over httpx for that - the same reasoning
`home_assistant.py` gives for Home Assistant.

Read-only by construction. The wardrobe catalogue is downstream of the album;
nothing here writes to Immich, so a key scoped to reading is enough.
"""

from __future__ import annotations

import base64

import httpx

from eve_tools.settings import get_tools_settings

_MAX_ASSETS = 500
_transport_for_test: httpx.MockTransport | None = None


def _client(timeout: float) -> httpx.AsyncClient:
    settings = get_tools_settings()
    return httpx.AsyncClient(
        timeout=timeout,
        base_url=settings.immich_url,
        headers={"x-api-key": settings.immich_api_key},
        transport=_transport_for_test,
    )


async def album_assets(album_id: str) -> dict:
    """Every asset in one album: id and original filename, nothing else."""
    async with _client(15.0) as client:
        response = await client.get(f"/api/albums/{album_id}")
        response.raise_for_status()
        album = response.json()
    assets = album.get("assets") or []
    return {
        "assets": [
            {"id": asset["id"], "filename": asset.get("originalFileName", "")}
            for asset in assets[:_MAX_ASSETS]
        ]
    }


async def asset_image(asset_id: str) -> dict:
    """One asset's preview, base64-encoded."""
    async with _client(30.0) as client:
        response = await client.get(
            f"/api/assets/{asset_id}/thumbnail", params={"size": "preview"}
        )
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "image/jpeg")
    return {
        "asset_id": asset_id,
        "content_type": content_type,
        "base64": base64.b64encode(content).decode(),
    }
