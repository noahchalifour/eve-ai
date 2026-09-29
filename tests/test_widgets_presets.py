# tests/test_widgets_presets.py
from __future__ import annotations

import pytest

from tests.test_widgets_template import accepts  # the same fake action check


@pytest.mark.parametrize("name,options", [
    ("weather", {"days": 3}),
    ("entity", {"entity": "light.kitchen"}),
    ("entity", {"entity": "sensor.outside"}),
    ("glance", {"entities": ["light.kitchen", "sensor.outside"]}),
    ("media", {"entity": "media_player.living_room"}),
    ("chart", {"sources": [{"type": "records", "collection": "alpha"}], "metric": {"op": "count"}}),
])
def test_every_preset_builds_a_valid_recipe(name, options):
    from eve.widgets import presets, recipe

    built = presets.build(name, options)
    assert isinstance(built, dict), built
    assert recipe.validate(built, action_accepts=lambda a, t: True) is None


def test_entity_preset_makes_the_card_the_tap_target_only_when_toggleable():
    from eve.widgets import presets

    light = presets.build("entity", {"entity": "light.kitchen"})
    sensor = presets.build("entity", {"entity": "sensor.outside"})
    assert light["template"][0]["properties"]["actionId"] == "home.toggle"
    assert "actionId" not in sensor["template"][0]["properties"]


def test_unknown_preset_lists_the_real_ones():
    from eve.widgets import presets

    assert "weather" in presets.build("nope", {})


def test_bad_options_are_explained():
    from eve.widgets import presets

    assert "days" in presets.build("weather", {"days": 99})
