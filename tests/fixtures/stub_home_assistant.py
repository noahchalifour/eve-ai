"""A minimal stand-in for Home Assistant's REST API, for integration tests
that exercise the real HTTP boundary to eve-tools without touching the real
home lab instance.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI()
_states = {"light.kitchen": "off"}


@app.get("/api/states/{entity_id}")
async def get_state(entity_id: str) -> dict:
    return {"entity_id": entity_id, "state": _states.get(entity_id, "unknown")}


@app.post("/api/services/{domain}/{service}")
async def call_service(domain: str, service: str, body: dict) -> list:
    _states[body["entity_id"]] = "on" if service == "turn_on" else "off"
    return []
