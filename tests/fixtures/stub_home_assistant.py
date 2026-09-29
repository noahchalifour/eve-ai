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
    # A lock and a media player so `home.entity`/`home.media` (ENG-269) and
    # their actions have non-light domains to exercise end to end: locks use
    # locked/unlocked instead of on/off and have no toggle service, and a
    # media player carries attributes (title/artist/volume) no light has.
    "lock.front_door": "locked",
    "media_player.living_room": "playing",
}

# Per-entity attributes, keyed the same as `_states`. Lights only ever needed
# `friendly_name`, which the old code derived uniformly from the entity id -
# that no longer holds once an entity carries attributes state alone can't
# express (media_title, media_artist, volume_level), so those live here and
# `friendly_name` is still filled in below for every entity that doesn't set
# its own.
_attributes: dict[str, dict] = {
    "lock.front_door": {"friendly_name": "Front door"},
    "media_player.living_room": {
        "friendly_name": "Living room",
        "media_title": "Blue in Green",
        "media_artist": "Miles Davis",
        "volume_level": 0.4,
    },
}


def _entity_attributes(entity_id: str) -> dict:
    """`friendly_name` alone by default (the old light-only behaviour),
    overridden/extended per entity where the domain needs more."""
    default = {"friendly_name": entity_id.split(".")[1].replace("_", " ")}
    return {**default, **_attributes.get(entity_id, {})}


@app.get("/api/states")
async def list_states() -> list:
    """HA returns every entity, not just the lights, and each one carries an
    `attributes` blob - both of which `home_assistant.list_entities` has to
    filter and trim, so the stub has to produce them."""
    return [
        {
            "entity_id": entity_id,
            "state": state,
            "attributes": _entity_attributes(entity_id),
        }
        for entity_id, state in {**_states, "sensor.outside_temp": "11.4"}.items()
    ]


@app.get("/api/states/{entity_id}")
async def get_state(entity_id: str) -> dict:
    return {
        "entity_id": entity_id,
        "state": _states.get(entity_id, "unknown"),
        "attributes": _entity_attributes(entity_id) if entity_id in _states else {},
    }


@app.post("/api/services/{domain}/{service}")
async def call_service(domain: str, service: str, body: dict) -> list:
    entity_id = body["entity_id"]
    if domain == "lock":
        # No lock.toggle in real HA; the action layer reads state first and
        # picks lock or unlock (see eve.widgets.actions.home._toggle).
        _states[entity_id] = "locked" if service == "lock" else "unlocked"
    elif domain == "media_player":
        if service == "media_play_pause":
            _states[entity_id] = "paused" if _states.get(entity_id) == "playing" else "playing"
        elif service in ("media_next_track", "media_previous_track"):
            # No track list to advance through in a stub: accepted no-ops,
            # matching the plan's "state flips only" instruction.
            pass
        elif service in ("volume_up", "volume_down"):
            attrs = _attributes.setdefault(entity_id, {})
            volume = attrs.get("volume_level", 0.5)
            step = 0.1 if service == "volume_up" else -0.1
            attrs["volume_level"] = round(min(1.0, max(0.0, volume + step)), 2)
    else:
        _states[entity_id] = "on" if service == "turn_on" else "off"
    return []
