from __future__ import annotations

import json

FORECAST = {
    "units": "metric",
    "current": {"temperature": 12.4, "apparent_temperature": 10.1, "humidity": 80,
                "wind_speed": 14.0, "code": 61, "is_day": True},
    "daily": [
        {"date": "2026-09-28", "high": 14.2, "low": 8.0, "code": 61, "precipitation_chance": 70},
        {"date": "2026-09-29", "high": 16.0, "low": 9.0, "code": 1, "precipitation_chance": 10},
    ],
}


async def test_weather_formats_for_display(monkeypatch):
    from eve.widgets.sources import base, weather

    async def fake_invoke(tool, arguments):
        assert tool == "home.weather" and arguments == {"days": 2}
        return json.dumps(FORECAST)

    monkeypatch.setattr(base, "invoke", fake_invoke)
    out = await weather._read(base.ReadContext("sub-noah", {}), weather.WeatherParams(days=2))

    assert out["current"] == {
        "temperature": "12°", "feels_like": "Feels like 10°", "condition": "Rain",
        "icon": "rain", "humidity": "80%", "wind": "14 km/h",
    }
    assert out["days"][1] == {"day": "Tue", "high": "16°", "low": "9°", "range": "16° / 9°",
                              "condition": "Partly cloudy", "icon": "cloud-sun", "precipitation": "10%"}
    assert out["today"] == out["days"][0]
