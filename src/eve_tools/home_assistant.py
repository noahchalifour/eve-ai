"""Home Assistant REST client. No SDK: the REST API is three calls behind a
long-lived token, and a dependency buys nothing over httpx for that.
"""

from __future__ import annotations

import httpx

from eve_tools.settings import get_tools_settings


# ponytail: a flat cap, not pagination. A house with more than this many
# entities in one domain has bigger problems than a truncated list, and the
# alternative is a cursor the model has to be taught to follow. Raise it, or
# add paging, if a real domain ever overflows.
_MAX_ENTITIES = 200


async def list_entities(domain: str | None = None) -> dict:
    """Every entity's current state, or just one domain's.

    Without this the home specialist could only ever `get_state` an entity_id
    it already knew, so "how many lights are on" was unanswerable - it had to
    guess ids blind (EVE-15's sibling bug). HA's `/api/states` returns the
    full attribute blob for every entity, hundreds of them and mostly noise to
    a model, so this trims to the three fields a specialist reasons about.
    """
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{settings.home_assistant_url}/api/states",
            headers={"Authorization": f"Bearer {settings.home_assistant_token}"},
        )
        response.raise_for_status()
        entities = [
            {
                "entity_id": entity["entity_id"],
                "state": entity["state"],
                "name": entity.get("attributes", {}).get("friendly_name", ""),
            }
            for entity in response.json()
            if domain is None or entity["entity_id"].startswith(f"{domain}.")
        ]
    return {
        "entities": entities[:_MAX_ENTITIES],
        "total": len(entities),
        "truncated": len(entities) > _MAX_ENTITIES,
    }


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
