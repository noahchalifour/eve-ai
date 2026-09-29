# src/eve/widgets/presets.py
"""Built-in templates. A preset is just a recipe builder the model can pick
instead of authoring a template: consistent layouts for common widgets, and
the reference examples a custom template can copy from.

Adding a preset = one options model + one builder + one `_register` call.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from eve.widgets.sources.home import EntityParams, MediaParams

# Domains where the whole card toggles. Kept in step with
# `eve.widgets.actions.home.TOGGLE_RISK` by a test (Task A7).
TOGGLE_DOMAINS = frozenset({"light", "switch", "fan", "input_boolean", "lock", "cover"})


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    options: type[BaseModel]
    build: Callable[[BaseModel], dict]


PRESETS: dict[str, Preset] = {}


def _register(preset: Preset) -> None:
    PRESETS[preset.name] = preset


def _c(id: str, type: str, properties: dict | None = None, *children: dict) -> dict:
    return {"id": id, "type": type, "properties": properties or {}, "children": list(children)}


def _recipe(sources: dict, *template: dict) -> dict:
    return {"version": 2, "sources": sources, "template": list(template)}


class WeatherOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=5, ge=1, le=7)


def _weather(o: WeatherOptions) -> dict:
    return _recipe(
        {"weather": {"type": "weather", "days": o.days}},
        _c("root", "column", {},
           _c("now", "row", {},
              _c("now_icon", "icon", {"name": "$data.weather.current.icon"}),
              _c("now_temp", "text", {"text": "$data.weather.current.temperature"}),
              _c("now_cond", "text", {"text": "$data.weather.current.condition"})),
           _c("feels", "text", {"text": "$data.weather.current.feels_like"}),
           _c("days", "list", {"repeat": "$data.weather.days", "limit": o.days},
              _c("day", "row", {},
                 _c("day_name", "text", {"text": "$item.day"}),
                 _c("day_icon", "icon", {"name": "$item.icon"}),
                 _c("day_range", "text", {"text": "$item.range"})))),
    )


class EntityOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str

    def params(self) -> EntityParams:
        return EntityParams(entities=[self.entity])


def _entity(o: EntityOptions) -> dict:
    o.params()  # validates the id
    toggleable = o.entity.split(".", 1)[0] in TOGGLE_DOMAINS
    card_props = {"actionId": "home.toggle", "actionValue": o.entity} if toggleable else {}
    return _recipe(
        {"entity": {"type": "home.entity", "entities": [o.entity]}},
        _c("root", "card", card_props,
           _c("row", "row", {},
              _c("icon", "icon", {"name": "$data.entity.primary.icon"}),
              _c("name", "text", {"text": "$data.entity.primary.name"}),
              _c("state", "badge", {"label": "$data.entity.primary.value"}))),
    )


class GlanceOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: list[str] = Field(min_length=1, max_length=12)


def _glance(o: GlanceOptions) -> dict:
    EntityParams(entities=o.entities)
    return _recipe(
        {"home": {"type": "home.entity", "entities": o.entities}},
        _c("root", "list", {"repeat": "$data.home.items", "limit": len(o.entities)},
           _c("row", "row", {},
              _c("icon", "icon", {"name": "$item.icon"}),
              _c("name", "text", {"text": "$item.name"}),
              _c("value", "badge", {"label": "$item.value"}))),
    )


class MediaOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity: str


def _media(o: MediaOptions) -> dict:
    MediaParams(entity=o.entity)
    e = o.entity
    return _recipe(
        {"media": {"type": "home.media", "entity": e}},
        _c("root", "column", {},
           _c("title", "text", {"text": "$data.media.player.title"}),
           _c("artist", "text", {"text": "$data.media.player.artist"}),
           _c("transport", "row", {},
              _c("prev", "button", {"label": "Previous", "actionId": "home.media.previous", "actionValue": e}),
              _c("play", "button", {"label": "$data.media.player.play_label", "actionId": "home.media.play_pause", "actionValue": e}),
              _c("next", "button", {"label": "Next", "actionId": "home.media.next", "actionValue": e})),
           _c("volume", "row", {},
              _c("down", "button", {"label": "Quieter", "actionId": "home.media.volume_down", "actionValue": e}),
              _c("level", "text", {"text": "$data.media.player.volume"}),
              _c("up", "button", {"label": "Louder", "actionId": "home.media.volume_up", "actionValue": e}))),
    )


class ChartOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[dict]
    metric: dict


def _chart(o: ChartOptions) -> dict:
    return _recipe(
        {"series": {"type": "series", "sources": o.sources, "metric": o.metric}},
        _c("root", "column", {},
           _c("range", "segmentedSelection",
              {"options": ["7", "30", "90"], "selected": "$data.widget.days", "actionId": "widget.setRange"}),
           _c("chart", "chart", {"points": "$data.series.points"}),
           _c("note", "text", {"text": "$data.series.note"})),
    )


_register(Preset("weather", "Current conditions and a daily forecast. Options: days (1-7).", WeatherOptions, _weather))
_register(Preset("entity", "One Home Assistant entity; tapping the card toggles lights, switches, fans, locks and "
                 "covers (locks and covers ask first). Options: entity.", EntityOptions, _entity))
_register(Preset("glance", "Read-only state of several entities. Options: entities (1-12).", GlanceOptions, _glance))
_register(Preset("media", "Now playing plus transport and volume for a media player. Options: entity "
                 "(media_player.*).", MediaOptions, _media))
_register(Preset("chart", "A day-bucketed chart of recorded collections and/or health metrics with a 7/30/90 day "
                 "range. Options: sources, metric (see the series source).", ChartOptions, _chart))


def build(name: str, options: dict | None) -> dict | str:
    preset = PRESETS.get(name)
    if preset is None:
        return f"unknown preset {name!r}; presets: {sorted(PRESETS)}"
    try:
        return preset.build(preset.options.model_validate(options or {}))
    except (ValidationError, ValueError) as exc:
        return f"preset {name!r} options rejected: {exc}"
