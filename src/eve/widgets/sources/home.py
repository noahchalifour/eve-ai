# src/eve/widgets/sources/home.py
"""Home Assistant entity state, normalised for tiles.

Reads one `home.get_state` per entity, concurrently, so one missing entity
degrades to an `Unavailable` row instead of failing the widget. Every entity
id a widget reads is also a TARGET: the only things a widget's actions may
touch (see `eve.widgets.actions`).
"""
from __future__ import annotations

import asyncio
import logging
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from eve.widgets.sources.base import ReadContext, SourceError, SourceType, call_tool, register, static_permission

logger = logging.getLogger(__name__)

_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_ON_STATES = frozenset({"on", "open", "unlocked", "playing", "home", "heat", "cool"})

_DOMAIN_ICONS = {
    "light": "lightbulb", "switch": "power", "input_boolean": "power", "fan": "fan",
    "lock": "lock", "cover": "home", "media_player": "music", "climate": "thermometer",
    "binary_sensor": "info", "sensor": "info", "plug": "plug",
}
_OFF_ICONS = {"light": "lightbulb-off"}
_DEVICE_CLASS_ICONS = {"temperature": "thermometer", "humidity": "droplets"}


def _check_entity(value: str) -> str:
    if not _ENTITY_ID.match(value):
        raise ValueError(f"{value!r} is not a Home Assistant entity id")
    return value


class EntityParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[str] = Field(min_length=1, max_length=12)

    @field_validator("entities")
    @classmethod
    def _ids(cls, value: list[str]) -> list[str]:
        return [_check_entity(entity) for entity in value]


class MediaParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str

    @field_validator("entity")
    @classmethod
    def _player(cls, value: str) -> str:
        _check_entity(value)
        if not value.startswith("media_player."):
            raise ValueError("entity must be a media_player")
        return value


def _label(state: str) -> str:
    return state.replace("_", " ").capitalize() if state else "Unknown"


def normalise_entity(raw: dict) -> dict:
    entity_id = raw.get("entity_id", "")
    domain = entity_id.split(".", 1)[0]
    state = str(raw.get("state", "unknown"))
    attributes = raw.get("attributes") or {}
    on = state in _ON_STATES
    unit = attributes.get("unit_of_measurement")
    icon = _DEVICE_CLASS_ICONS.get(attributes.get("device_class"), _DOMAIN_ICONS.get(domain, "info"))
    if not on and domain in _OFF_ICONS:
        icon = _OFF_ICONS[domain]
    if domain == "lock" and state == "unlocked":
        icon = "unlock"
    label = _label(state)
    return {
        "entity_id": entity_id,
        "name": attributes.get("friendly_name") or entity_id,
        "domain": domain,
        "state": state,
        "state_label": label,
        "on": on,
        "icon": icon,
        "value": f"{state} {unit}" if unit else label,
    }


def _unavailable(entity_id: str) -> dict:
    return normalise_entity({"entity_id": entity_id, "state": "unavailable"}) | {"name": entity_id}


async def _one(entity_id: str) -> dict:
    try:
        return normalise_entity(await call_tool("home.get_state", {"entity_id": entity_id}))
    except SourceError:
        logger.warning("home.get_state failed for %s", entity_id)
        return _unavailable(entity_id)


async def _read_entities(ctx: ReadContext, params: EntityParams) -> dict:
    items = list(await asyncio.gather(*(_one(entity) for entity in params.entities)))
    if all(item["state"] == "unavailable" for item in items):
        # Every read failed: that is an outage, not a house full of broken
        # devices, and the card should say so via `sources.errors`.
        raise SourceError("no entity could be read")
    return {"primary": items[0], "items": items}


async def _read_media(ctx: ReadContext, params: MediaParams) -> dict:
    raw = await call_tool("home.get_state", {"entity_id": params.entity})
    base = normalise_entity(raw)
    attributes = raw.get("attributes") or {}
    playing = base["state"] == "playing"
    volume = attributes.get("volume_level")
    return {"player": {
        "entity_id": base["entity_id"],
        "name": base["name"],
        "state_label": base["state_label"],
        "playing": playing,
        "title": attributes.get("media_title") or "Nothing playing",
        "artist": attributes.get("media_artist") or "",
        "volume": f"{round(volume * 100)}%" if isinstance(volume, (int, float)) else "",
        "play_icon": "pause" if playing else "play",
        "play_label": "Pause" if playing else "Play",
    }}


_ITEM_KEYS = frozenset({"entity_id", "name", "domain", "state", "state_label", "on", "icon", "value"})

register(SourceType(
    name="home.entity",
    params=EntityParams,
    fields=frozenset({"primary", "items"}),
    read=_read_entities,
    ttl_seconds=10,
    item_fields={"items": _ITEM_KEYS},
    permissions=static_permission("home.control"),
    targets=lambda params: frozenset(params.entities),
    description="Home Assistant entity state. Params: entities (1-12 entity ids). Fields: primary (first entity), "
    "items (all). Each has entity_id, name, domain, state, state_label, on, icon, value.",
))

register(SourceType(
    name="home.media",
    params=MediaParams,
    fields=frozenset({"player"}),
    read=_read_media,
    ttl_seconds=10,
    permissions=static_permission("home.control"),
    targets=lambda params: frozenset({params.entity}),
    description="A Home Assistant media player. Params: entity (media_player.*). Field: player {entity_id, name, "
    "state_label, playing, title, artist, volume, play_icon, play_label}.",
))
