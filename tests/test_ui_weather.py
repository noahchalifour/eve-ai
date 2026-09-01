"""The `weather` surface, built from Home Assistant's forecast rather than
from anything a model said."""

from __future__ import annotations

import json

from eve.ui import protocol, weather

TORONTO = "America/Toronto"

FORECAST = {
    "entity_id": "weather.home",
    "location": "Home",
    "condition": "partlycloudy",
    "temperature": 21.4,
    "hourly": [
        {"datetime": "2026-08-31T18:00:00+00:00", "condition": "sunny", "temperature": 22.6},
        {"datetime": "2026-08-31T19:00:00+00:00", "condition": "rainy", "temperature": 19.1},
    ],
    "daily": [
        {"datetime": "2026-09-05T12:00:00+00:00", "condition": "pouring", "temperature": 17.0},
    ],
}


def test_a_created_surface_passes_the_protocol_validator():
    operation = weather.build_create("wx-1", FORECAST, TORONTO)
    assert protocol.validate_operation(operation) is None


def test_the_surface_declares_the_weather_catalog_and_binds_its_three_properties():
    """`dynamic_surface_renderer.dart` branches on catalogId == 'weather',
    then resolves exactly these three properties off the component whose type
    is 'weather'."""
    surface = weather.build_create("wx-1", FORECAST, TORONTO)["surface"]

    assert surface["catalogId"] == "weather"
    assert surface["catalogVersion"] == protocol.CATALOG_VERSION
    assert surface["localState"] == {}
    component = surface["components"][0]
    assert component["type"] == "weather"
    assert component["properties"] == {
        "location": "$data.location",
        "condition": "$data.condition",
        "temperature": "$data.temperature",
    }


def test_location_and_condition_resolve_to_strings_and_temperature_to_a_number():
    """Any other type and the renderer draws the "This content can't be
    shown" fallback instead of the card."""
    data = weather.build_create("wx-1", FORECAST, TORONTO)["surface"]["data"]

    assert isinstance(data["location"], str) and data["location"]
    assert isinstance(data["condition"], str) and data["condition"]
    assert isinstance(data["temperature"], (int, float))
    assert not isinstance(data["temperature"], bool)


def test_the_create_carries_hourly_and_deliberately_omits_daily():
    """This omission IS the action round trip. `_forecastFor('daily')` returns
    null for an absent key, and `_select` dispatches a `weather.rangeChanged`
    action rather than switching locally."""
    data = weather.build_create("wx-1", FORECAST, TORONTO)["surface"]["data"]

    assert data["selectedRange"] == "hourly"
    assert isinstance(data["hourly"], list) and data["hourly"]
    assert "daily" not in data


def test_a_forecast_cell_carries_exactly_the_three_keys_the_widget_reads():
    cells = weather.forecast_cells(FORECAST["hourly"], "hourly", TORONTO)

    assert set(cells[0]) == {"label", "temperature", "condition"}
    assert isinstance(cells[0]["temperature"], int)
    assert cells[0]["condition"] == "Sunny"


def test_hourly_labels_are_local_clock_hours_and_daily_labels_are_weekdays():
    """18:00Z on 2026-08-31 is 14:00 in Toronto (EDT), and 2026-09-05 is a
    Saturday. A UTC label would be wrong for every member not at UTC+0."""
    hourly = weather.forecast_cells(FORECAST["hourly"], "hourly", TORONTO)
    daily = weather.forecast_cells(FORECAST["daily"], "daily", TORONTO)

    assert hourly[0]["label"] == "2 PM"
    assert daily[0]["label"] == "Sat"


def test_a_forecast_entry_without_a_usable_temperature_is_dropped_not_faked():
    entries = [
        {"datetime": "2026-08-31T18:00:00+00:00", "condition": "sunny"},
        {"datetime": "not-a-date", "condition": "sunny", "temperature": 20},
        {"datetime": "2026-08-31T19:00:00+00:00", "condition": "rainy", "temperature": 19},
    ]
    cells = weather.forecast_cells(entries, "hourly", TORONTO)

    assert len(cells) == 1
    assert cells[0]["temperature"] == 19


def test_condition_slugs_become_readable_labels():
    assert weather.condition_label("partlycloudy") == "Partly cloudy"
    assert weather.condition_label("lightning-rainy") == "Thunderstorms"
    assert weather.condition_label("clear-night") == "Clear"
    assert weather.condition_label("brand-new-slug") == "Brand new slug"
    assert weather.condition_label(None) == "Unknown"


def test_a_range_patch_sets_both_the_range_and_its_forecast():
    operation = weather.build_range_patch("wx-1", "daily", FORECAST, TORONTO)

    assert protocol.validate_operation(operation) is None
    assert operation["op"] == "patch"
    assert operation["surfaceId"] == "wx-1"
    assert operation["patch"]["dataPatch"]["selectedRange"] == "daily"
    assert operation["patch"]["dataPatch"]["daily"][0]["label"] == "Sat"


def test_a_range_the_home_publishes_nothing_for_produces_no_patch():
    """No patch means no `custom` frame, which is exactly the failure the
    client's own contract describes: the surface keeps its last valid data and
    offers a retry. A patch full of nothing would instead look like success
    and render an empty card."""
    assert weather.build_range_patch("wx-1", "daily", {"daily": []}, TORONTO) is None


def test_decode_forecast_treats_an_error_string_as_a_failure():
    """`eve.tools_client.invoke` answers with a JSON string on success and a
    human-readable `error: ...` on every failure - so the parse failure IS the
    failure signal, and there is no exception to catch."""
    assert weather.decode_forecast("error: eve-tools unavailable (ConnectError)") is None
    assert weather.decode_forecast("null") is None


def test_decode_forecast_rejects_a_payload_with_no_usable_temperature():
    """A non-numeric temperature would render the whole-surface fallback on
    the client. Catch it here, where Eve can still answer in prose."""
    assert weather.decode_forecast(json.dumps({"temperature": None})) is None
    assert weather.decode_forecast(json.dumps({"temperature": True})) is None
    assert weather.decode_forecast(json.dumps({"temperature": 20})) == {"temperature": 20}


def test_surface_ids_are_unique_per_card():
    assert weather.new_surface_id() != weather.new_surface_id()


def test_summary_gives_the_model_a_short_sentence_not_the_payload():
    text = weather.summary(FORECAST)

    assert "Home" in text
    assert "Partly cloudy" in text
    assert len(text) < 160
