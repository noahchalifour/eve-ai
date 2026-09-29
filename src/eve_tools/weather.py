"""Household weather from Open-Meteo: no key, no account, one GET."""
from __future__ import annotations

import httpx

from eve_tools.settings import get_tools_settings

_URL = "https://api.open-meteo.com/v1/forecast"


async def forecast(days: int = 3) -> dict:
    settings = get_tools_settings()
    if settings.weather_latitude is None or settings.weather_longitude is None:
        raise RuntimeError("weather location is not configured")
    imperial = settings.weather_units == "imperial"
    params = {
        "latitude": settings.weather_latitude,
        "longitude": settings.weather_longitude,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": max(1, min(7, int(days))),
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(_URL, params=params)
        response.raise_for_status()
        body = response.json()
    current, daily = body["current"], body["daily"]
    return {
        "units": "imperial" if imperial else "metric",
        "current": {
            "temperature": current["temperature_2m"],
            "apparent_temperature": current["apparent_temperature"],
            "humidity": current["relative_humidity_2m"],
            "wind_speed": current["wind_speed_10m"],
            "code": current["weather_code"],
            "is_day": bool(current["is_day"]),
        },
        "daily": [
            {
                "date": date,
                "high": daily["temperature_2m_max"][i],
                "low": daily["temperature_2m_min"][i],
                "code": daily["weather_code"][i],
                "precipitation_chance": daily["precipitation_probability_max"][i],
            }
            for i, date in enumerate(daily["time"])
        ],
    }
