"""The `weather` surface: the one catalog type V1 ships.

Everything the client renders is assembled HERE, server-side, out of Home
Assistant's own forecast. The model chooses *whether* to show a card, never
what is in it. A model asked to hand-write thirteen-component JSON produces
`component-schema` rejections and invented temperatures; a model that calls
one no-argument tool cannot do either.

The client contracts this file is shaped by (flutter-open-assistant):
`dynamic_surface_renderer.dart:53` branches on `catalogId == 'weather'` and
resolves `location`/`condition`/`temperature` off the component whose `type`
is `weather`, requiring two strings and a number or it draws the
whole-surface fallback. `weather_surface.dart` draws the Hourly / 7-day
control ITSELF, so no `segmentedSelection` component belongs here.
`_ForecastCell` reads exactly `label`, `temperature`, `condition`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from eve.ui import protocol

CATALOG_ID = "weather"
RANGES = ("hourly", "daily")

# ponytail: the widget lays cells out in a `Wrap`, and the surface has a
# 48KiB ceiling. Twelve hours and seven days is a card; forty-eight hours is
# a spreadsheet nobody reads on a phone.
_CELLS = {"hourly": 6, "daily": 7}

# Home Assistant's closed condition vocabulary. Anything outside it is
# de-slugged rather than dropped - a new HA condition should read a little
# plain, not blank out the card.
_CONDITIONS = {
    "clear-night": "Clear",
    "cloudy": "Cloudy",
    "exceptional": "Exceptional",
    "fog": "Fog",
    "hail": "Hail",
    "lightning": "Lightning",
    "lightning-rainy": "Thunderstorms",
    "partlycloudy": "Partly cloudy",
    "pouring": "Heavy rain",
    "rainy": "Rain",
    "snowy": "Snow",
    "snowy-rainy": "Sleet",
    "sunny": "Sunny",
    "windy": "Windy",
    "windy-variant": "Windy",
}


def new_surface_id() -> str:
    """Unique per card, not per thread. The client addresses a surface by this
    id and the action envelope carries it back, so nothing server-side has to
    remember it between turns."""
    return f"wx-{uuid.uuid4().hex[:8]}"


def condition_label(slug: object) -> str:
    if not isinstance(slug, str) or not slug:
        return "Unknown"
    if slug in _CONDITIONS:
        return _CONDITIONS[slug]
    return slug.replace("-", " ").replace("_", " ").capitalize()


def decode_forecast(raw: str) -> dict | None:
    """The `home.weather` payload, or None.

    `eve.tools_client.invoke` answers with a JSON string on success and a
    human-readable `error: ...` string on EVERY failure, so a parse failure is
    the failure signal - there is no exception to catch. The temperature check
    belongs here rather than in the renderer's lap: a non-numeric temperature
    makes the client draw its whole-surface fallback, and this is the last
    place Eve can still choose prose instead.
    """
    try:
        forecast = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(forecast, dict):
        return None
    temperature = forecast.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        return None
    return forecast


def forecast_cells(entries: object, kind: str, timezone: str) -> list[dict]:
    """`{label, temperature, condition}` per cell - exactly the three keys
    `_ForecastCell` reads, and nothing else.

    An entry missing a parseable timestamp or a numeric temperature is
    DROPPED, never defaulted: a card showing `0°` for an hour HA said nothing
    about is worse than a card with one fewer cell.
    """
    zone = ZoneInfo(timezone)
    limit = _CELLS.get(kind, _CELLS["daily"])
    cells: list[dict] = []
    for entry in (entries if isinstance(entries, list) else [])[:limit]:
        if not isinstance(entry, dict):
            continue
        moment = _local_moment(entry.get("datetime"), zone)
        temperature = entry.get("temperature")
        if moment is None or isinstance(temperature, bool):
            continue
        if not isinstance(temperature, (int, float)):
            continue
        cells.append(
            {
                "label": _label(moment, kind),
                "temperature": round(temperature),
                "condition": condition_label(entry.get("condition")),
            }
        )
    return cells


def build_create(surface_id: str, forecast: dict, timezone: str) -> dict:
    """The `create` operation for one weather card.

    `daily` is deliberately ABSENT from `data`. `_forecastFor('daily')`
    returns null for a missing key, so `_select('daily')` dispatches a
    `weather.rangeChanged` action instead of switching locally - that omission
    is what makes the round trip in `eve.ui.actions` reachable at all. Adding
    `"daily": null` would work identically (null is not a list) but reads like
    an oversight; leaving the key out states the intent.
    """
    return {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": surface_id,
            "catalogId": CATALOG_ID,
            "catalogVersion": protocol.CATALOG_VERSION,
            "components": [
                {
                    "id": "weather",
                    "type": "weather",
                    "properties": {
                        "location": "$data.location",
                        "condition": "$data.condition",
                        "temperature": "$data.temperature",
                    },
                    "children": [],
                }
            ],
            "data": {
                "location": _location(forecast),
                "condition": condition_label(forecast.get("condition")),
                "temperature": round(forecast["temperature"]),
                "selectedRange": "hourly",
                "hourly": forecast_cells(forecast.get("hourly"), "hourly", timezone),
            },
            # Never seeded server-side: `localState` is the client's own
            # presentation memory, restored from its cache on reopen. A value
            # here would fight `_mergeCachedLocalState` for it.
            "localState": {},
        },
    }


def build_range_patch(
    surface_id: str, value: str, forecast: dict, timezone: str
) -> dict | None:
    """The `patch` answering one `weather.rangeChanged` tap, or None when the
    home publishes nothing for that range.

    None matters: emitting no frame is how the client learns the action
    failed (its contract keeps the last valid data, marks the surface `error`
    and offers a retry). A patch carrying an empty list would instead look
    like success and render an empty card.
    """
    cells = forecast_cells(forecast.get(value), value, timezone)
    if not cells:
        return None
    return {
        "protocol": protocol.PROTOCOL,
        "op": "patch",
        "surfaceId": surface_id,
        "patch": {"dataPatch": {"selectedRange": value, value: cells}},
    }


def summary(forecast: dict) -> str:
    """What the MODEL sees as the tool result: one short sentence, so Eve can
    add a line of her own without reading a payload back to the member."""
    return (
        f"Weather card shown: {_location(forecast)}, "
        f"{condition_label(forecast.get('condition'))}, "
        f"{round(forecast['temperature'])} degrees. "
        "Say one short sentence about it; do not list the forecast."
    )


def _location(forecast: dict) -> str:
    location = forecast.get("location")
    return location if isinstance(location, str) and location else "Home"


def _local_moment(value: object, zone: ZoneInfo) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(zone)
    except ValueError:
        return None


def _label(moment: datetime, kind: str) -> str:
    if kind != "hourly":
        return moment.strftime("%a")
    # Built by hand rather than with `%-I %p`: the dash-modifier is a
    # platform extension (glibc/BSD) that is not portable, and this runs in a
    # container.
    hour = moment.hour % 12 or 12
    return f"{hour} {'AM' if moment.hour < 12 else 'PM'}"
