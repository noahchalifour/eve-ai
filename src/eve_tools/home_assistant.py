"""Home Assistant REST client. No SDK: the REST API is two calls behind a
long-lived token, and a dependency buys nothing over httpx for that.
"""

from __future__ import annotations

import httpx

from eve_tools.settings import get_tools_settings


async def get_state(entity_id: str) -> dict:
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.home_assistant_url}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {settings.home_assistant_token}"},
        )
        response.raise_for_status()
        return response.json()


async def call_service(domain: str, service: str, entity_id: str, data: dict) -> dict:
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{settings.home_assistant_url}/api/services/{domain}/{service}",
            headers={"Authorization": f"Bearer {settings.home_assistant_token}"},
            json={"entity_id": entity_id, **data},
        )
        response.raise_for_status()
        return {"called": True, "response": response.json()}
