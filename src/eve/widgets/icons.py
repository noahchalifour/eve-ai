"""Icon names a widget may emit. Mirrors the client's closed glyph map
(`DynamicSurfaceCatalog.icons` in open-assistant's app_ui); an unknown name
renders the client's `unknown` glyph, never an error. Keep both lists in step
by hand when adding a glyph."""
from __future__ import annotations

ICON_NAMES = frozenset({
    "info", "alert", "check",
    "sun", "moon", "cloud", "cloud-sun", "rain", "snow", "storm", "fog", "wind",
    "thermometer", "droplets",
    "lightbulb", "lightbulb-off", "power", "lock", "unlock", "fan", "plug", "home",
    "music", "play", "pause", "skip-forward", "skip-back", "volume-down", "volume-up",
    "calendar", "heart",
})


def weather_condition(code: int) -> str:
    """WMO weather code to a short phrase (Open-Meteo uses WMO codes)."""
    if code == 0:
        return "Clear"
    if code in (1, 2):
        return "Partly cloudy"
    if code == 3:
        return "Overcast"
    if code in (45, 48):
        return "Fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "Rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "Snow"
    if 95 <= code <= 99:
        return "Thunderstorm"
    return "Unknown"


def weather_icon(code: int, is_day: bool = True) -> str:
    return {
        "Clear": "sun" if is_day else "moon",
        "Partly cloudy": "cloud-sun" if is_day else "cloud",
        "Overcast": "cloud",
        "Fog": "fog",
        "Rain": "rain",
        "Snow": "snow",
        "Thunderstorm": "storm",
    }.get(weather_condition(code), "info")
