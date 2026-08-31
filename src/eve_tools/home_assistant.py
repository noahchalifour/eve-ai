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


# ponytail: a flat trim, not a window the caller chooses. HA hands back 48
# hourly entries and the card lays out a handful of cells; `eve.ui.weather`
# trims again for presentation. Raise it if a range ever needs more.
_MAX_FORECAST_ENTRIES = 24


async def weather(entity_id: str | None = None) -> dict:
    """Current conditions plus the hourly AND daily forecast for one HA
    weather entity - everything the `weather` surface needs, in one relay call.

    Not `call_service`: `weather.get_forecasts` is a *response* service, and
    without `?return_response` HA answers 200 with an empty body and the
    forecast is silently lost. Adding that parameter to `call_service` would
    change every existing caller.

    Raises when the home has no weather entity at all. `eve_tools.app`'s
    `/invoke` turns that into `{"error": ...}` with a 200, which is the shape
    `eve.tools_client.invoke` already degrades to a returned string.
    """
    settings = get_tools_settings()
    base = settings.home_assistant_url
    headers = {"Authorization": f"Bearer {settings.home_assistant_token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        if entity_id is None:
            listing = await client.get(f"{base}/api/states", headers=headers)
            listing.raise_for_status()
            entity_id = next(
                (
                    entity["entity_id"]
                    for entity in listing.json()
                    if entity["entity_id"].startswith("weather.")
                ),
                None,
            )
            if entity_id is None:
                raise ValueError("no weather entity in Home Assistant")

        current = await client.get(f"{base}/api/states/{entity_id}", headers=headers)
        current.raise_for_status()
        state = current.json()

        forecasts: dict[str, list] = {}
        for kind in ("hourly", "daily"):
            response = await client.post(
                f"{base}/api/services/weather/get_forecasts",
                params={"return_response": ""},
                headers=headers,
                json={"entity_id": entity_id, "type": kind},
            )
            # A range the entity does not publish (plenty of HA weather
            # integrations have no hourly forecast) is an empty range, not a
            # failed call: the card still renders, and the client dispatches a
            # remote action for whichever range is absent.
            if response.status_code >= 400:
                forecasts[kind] = []
                continue
            body = response.json()
            entries = (
                body.get("service_response", {}).get(entity_id, {}).get("forecast", [])
            )
            forecasts[kind] = entries[:_MAX_FORECAST_ENTRIES]

    attributes = state.get("attributes", {})
    return {
        "entity_id": entity_id,
        "location": attributes.get("friendly_name", ""),
        "condition": state.get("state", ""),
        "temperature": attributes.get("temperature"),
        "hourly": forecasts["hourly"],
        "daily": forecasts["daily"],
    }
