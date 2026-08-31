"""A minimal stand-in for Home Assistant's REST API, for integration tests
that exercise the real HTTP boundary to eve-tools without touching the real
home lab instance.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI()
# More than one light so a "how many lights are on" request has a real
# answer to count, and enough of them that answering needs several rounds of
# get_state - the shape that surfaced EVE-15.
_states = {
    "light.kitchen": "off",
    "light.living_room": "on",
    "light.bedroom": "on",
    "light.porch": "off",
    "light.garage": "on",
    "light.office": "off",
}

# A weather entity with the attribute blob HA actually returns for one:
# `state` is the condition slug, `temperature` lives in `attributes`.
_WEATHER_ENTITY = "weather.home"
_WEATHER_STATE = "partlycloudy"
_WEATHER_ATTRIBUTES = {
    "friendly_name": "Home",
    "temperature": 21.4,
    "temperature_unit": "°C",
    "humidity": 62,
}

_FORECASTS = {
    "hourly": [
        {
            "datetime": f"2026-08-31T{hour:02d}:00:00+00:00",
            "condition": "partlycloudy" if hour % 2 else "sunny",
            "temperature": 18 + hour % 7,
        }
        for hour in range(12)
    ],
    "daily": [
        {
            "datetime": f"2026-09-0{day}T12:00:00+00:00",
            "condition": "rainy" if day % 2 else "cloudy",
            "temperature": 20 + day,
            "templow": 12 + day,
        }
        for day in range(1, 8)
    ],
}


@app.get("/api/states")
async def list_states() -> list:
    """HA returns every entity, not just the lights, and each one carries an
    `attributes` blob - both of which `home_assistant.list_entities` has to
    filter and trim, so the stub has to produce them."""
    entities = [
        {
            "entity_id": entity_id,
            "state": state,
            "attributes": {"friendly_name": entity_id.split(".")[1].replace("_", " ")},
        }
        for entity_id, state in {**_states, "sensor.outside_temp": "11.4"}.items()
    ]
    entities.append(
        {
            "entity_id": _WEATHER_ENTITY,
            "state": _WEATHER_STATE,
            "attributes": dict(_WEATHER_ATTRIBUTES),
        }
    )
    return entities


@app.get("/api/states/{entity_id}")
async def get_state(entity_id: str) -> dict:
    if entity_id == _WEATHER_ENTITY:
        return {
            "entity_id": entity_id,
            "state": _WEATHER_STATE,
            "attributes": dict(_WEATHER_ATTRIBUTES),
        }
    return {"entity_id": entity_id, "state": _states.get(entity_id, "unknown")}


# Declared BEFORE the generic service route below: FastAPI matches in
# declaration order, and `call_service` would otherwise swallow this as a
# state change and answer `[]`.
@app.post("/api/services/weather/get_forecasts")
async def get_forecasts(body: dict, return_response: str | None = None) -> dict:
    """HA drops the forecast entirely unless `?return_response` is present -
    the exact failure this stub has to be able to reproduce."""
    if return_response is None:
        return {"changed_states": []}
    forecast = _FORECASTS.get(body.get("type"), [])
    return {
        "changed_states": [],
        "service_response": {body["entity_id"]: {"forecast": forecast}},
    }


@app.post("/api/services/{domain}/{service}")
async def call_service(domain: str, service: str, body: dict) -> list:
    _states[body["entity_id"]] = "on" if service == "turn_on" else "off"
    return []
