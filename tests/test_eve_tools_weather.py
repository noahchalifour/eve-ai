import httpx
import pytest
import respx

from eve_tools import weather


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from eve_tools.settings import get_tools_settings

    monkeypatch.setenv("EVE_TOOLS_WEATHER_LATITUDE", "49.0")
    monkeypatch.setenv("EVE_TOOLS_WEATHER_LONGITUDE", "-123.0")
    get_tools_settings.cache_clear()
    yield
    get_tools_settings.cache_clear()


@respx.mock
async def test_weather_normalises_open_meteo():
    respx.get("https://api.open-meteo.com/v1/forecast").mock(return_value=httpx.Response(200, json={
        "current": {"temperature_2m": 12.4, "apparent_temperature": 10.1, "relative_humidity_2m": 80,
                    "wind_speed_10m": 14.0, "weather_code": 61, "is_day": 1},
        "daily": {"time": ["2026-09-28"], "temperature_2m_max": [14.2], "temperature_2m_min": [8.0],
                  "weather_code": [61], "precipitation_probability_max": [70]},
    }))
    out = await weather.forecast(days=1)
    assert out["current"]["temperature"] == 12.4
    assert out["daily"] == [{"date": "2026-09-28", "high": 14.2, "low": 8.0, "code": 61, "precipitation_chance": 70}]
    assert out["units"] == "metric"


async def test_weather_without_a_location_fails_loudly(monkeypatch):
    from eve_tools.settings import get_tools_settings

    monkeypatch.delenv("EVE_TOOLS_WEATHER_LATITUDE")
    get_tools_settings.cache_clear()
    with pytest.raises(RuntimeError, match="location"):
        await weather.forecast(days=1)


def test_home_weather_is_a_registered_tool():
    """The stylist has called `home.weather` since it shipped; nothing handled
    it, so it has always answered 404 (found while planning ENG-269)."""
    from eve_tools.app import _HANDLERS

    assert "home.weather" in _HANDLERS
