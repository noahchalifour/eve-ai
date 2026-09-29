# tests/test_widgets_source_home.py
from __future__ import annotations

import json

import pytest

STATES = {
    "light.kitchen": {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
    "lock.front_door": {"entity_id": "lock.front_door", "state": "locked", "attributes": {"friendly_name": "Front door"}},
    "sensor.outside": {"entity_id": "sensor.outside", "state": "11.4",
                       "attributes": {"friendly_name": "Outside", "unit_of_measurement": "°C", "device_class": "temperature"}},
    "media_player.living_room": {"entity_id": "media_player.living_room", "state": "playing", "attributes": {
        "friendly_name": "Living room", "media_title": "Blue in Green", "media_artist": "Miles Davis", "volume_level": 0.4}},
}


@pytest.fixture
def ha(monkeypatch):
    from eve.widgets.sources import base

    async def fake_invoke(tool, arguments):
        assert tool == "home.get_state"
        return json.dumps(STATES[arguments["entity_id"]])

    monkeypatch.setattr(base, "invoke", fake_invoke)


async def test_entity_items_are_normalised_for_display(ha):
    from eve.widgets.sources import base, home

    params = home.EntityParams(entities=["light.kitchen", "lock.front_door", "sensor.outside"])
    out = await home._read_entities(base.ReadContext("sub-noah", {}), params)

    assert out["primary"] == {"entity_id": "light.kitchen", "name": "Kitchen", "domain": "light", "state": "on",
                              "state_label": "On", "on": True, "icon": "lightbulb", "value": "On"}
    assert out["items"][1]["icon"] == "lock" and out["items"][1]["state_label"] == "Locked"
    assert out["items"][2]["value"] == "11.4 °C" and out["items"][2]["icon"] == "thermometer"


async def test_one_bad_entity_does_not_blank_the_rest(monkeypatch):
    from eve.widgets.sources import base, home

    async def flaky(tool, arguments):
        if arguments["entity_id"] == "light.gone":
            return "error: 404"
        return json.dumps(STATES[arguments["entity_id"]])

    monkeypatch.setattr(base, "invoke", flaky)
    out = await home._read_entities(base.ReadContext("s", {}), home.EntityParams(entities=["light.gone", "light.kitchen"]))
    assert out["items"][0]["state_label"] == "Unavailable"
    assert out["items"][1]["name"] == "Kitchen"


def test_entity_ids_are_validated_and_become_targets():
    from pydantic import ValidationError

    from eve.widgets.sources import REGISTRY, home

    with pytest.raises(ValidationError):
        home.EntityParams(entities=["http://evil"])
    params = home.EntityParams(entities=["light.kitchen"])
    assert REGISTRY["home.entity"].targets(params) == {"light.kitchen"}
    assert REGISTRY["home.entity"].permissions(params) == {"home.control"}


async def test_media_player_reads_now_playing(ha):
    from eve.widgets.sources import base, home

    out = await home._read_media(base.ReadContext("s", {}), home.MediaParams(entity="media_player.living_room"))
    assert out["player"] == {"entity_id": "media_player.living_room", "name": "Living room", "state_label": "Playing",
                             "playing": True, "title": "Blue in Green", "artist": "Miles Davis", "volume": "40%",
                             "play_icon": "pause", "play_label": "Pause"}


def test_media_only_accepts_media_players():
    from pydantic import ValidationError

    from eve.widgets.sources import home

    with pytest.raises(ValidationError):
        home.MediaParams(entity="light.kitchen")


def test_every_emitted_icon_is_in_the_vocabulary():
    from eve.widgets import icons
    from eve.widgets.sources import home

    assert set(home._DOMAIN_ICONS.values()) | set(home._OFF_ICONS.values()) <= icons.ICON_NAMES
