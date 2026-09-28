"""Household weather, formatted for display. Formatting lives here, not in
eve-tools: the stylist's model wants raw numbers, a tile wants `12°`."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from eve.widgets.icons import weather_condition, weather_icon
from eve.widgets.sources.base import ReadContext, SourceType, call_tool, register


class WeatherParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=5, ge=1, le=7)


def _deg(value: float) -> str:
    return f"{round(value)}°"


async def _read(ctx: ReadContext, params: WeatherParams) -> dict:
    body = await call_tool("home.weather", {"days": params.days})
    wind_unit = "mph" if body.get("units") == "imperial" else "km/h"
    now = body["current"]
    days = [
        {
            "day": date.fromisoformat(entry["date"]).strftime("%a"),
            "high": _deg(entry["high"]),
            "low": _deg(entry["low"]),
            "range": f"{_deg(entry['high'])} / {_deg(entry['low'])}",
            "condition": weather_condition(entry["code"]),
            "icon": weather_icon(entry["code"]),
            "precipitation": f"{entry['precipitation_chance']}%",
        }
        for entry in body["daily"]
    ]
    return {
        "current": {
            "temperature": _deg(now["temperature"]),
            "feels_like": f"Feels like {_deg(now['apparent_temperature'])}",
            "condition": weather_condition(now["code"]),
            "icon": weather_icon(now["code"], now["is_day"]),
            "humidity": f"{now['humidity']}%",
            "wind": f"{round(now['wind_speed'])} {wind_unit}",
        },
        "today": days[0] if days else {},
        "days": days,
    }


_DAY_KEYS = frozenset({"day", "high", "low", "range", "condition", "icon", "precipitation"})

register(SourceType(
    name="weather",
    params=WeatherParams,
    fields=frozenset({"current", "today", "days"}),
    read=_read,
    ttl_seconds=900,
    item_fields={"days": _DAY_KEYS},
    description="Household weather. Params: days (1-7). Fields: current {temperature, feels_like, condition, icon, "
    "humidity, wind}; today and each of days {day, high, low, range, condition, icon, precipitation}.",
))
