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


@app.get("/api/states")
async def list_states() -> list:
    """HA returns every entity, not just the lights, and each one carries an
    `attributes` blob - both of which `home_assistant.list_entities` has to
    filter and trim, so the stub has to produce them."""
    return [
        {
            "entity_id": entity_id,
            "state": state,
            "attributes": {"friendly_name": entity_id.split(".")[1].replace("_", " ")},
        }
        for entity_id, state in {**_states, "sensor.outside_temp": "11.4"}.items()
    ]


@app.get("/api/states/{entity_id}")
async def get_state(entity_id: str) -> dict:
    return {"entity_id": entity_id, "state": _states.get(entity_id, "unknown")}


@app.post("/api/services/{domain}/{service}")
async def call_service(domain: str, service: str, body: dict) -> list:
    _states[body["entity_id"]] = "on" if service == "turn_on" else "off"
    return []
