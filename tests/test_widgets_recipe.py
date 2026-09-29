# tests/test_widgets_recipe.py  (replace the file's v2-relevant tests with these)
from __future__ import annotations

V2 = {
    "version": 2,
    "sources": {"kitchen": {"type": "home.entity", "entities": ["light.kitchen"]}},
    "template": [{"id": "root", "type": "card", "properties": {"title": "$data.kitchen.primary.name"}, "children": []}],
}


def ok(action, target):
    return True


def test_a_v2_recipe_validates():
    from eve.widgets import recipe

    assert recipe.validate(V2, action_accepts=ok) is None


def test_top_level_smuggling_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate({**V2, "url": "http://x"}, action_accepts=ok) is not None


def test_reserved_and_malformed_aliases_are_rejected():
    from eve.widgets import recipe

    for alias in ("widget", "Bad-Alias", "1x"):
        bad = {**V2, "sources": {alias: V2["sources"]["kitchen"]}}
        assert recipe.validate(bad, action_accepts=ok) is not None


def test_too_many_sources_is_rejected():
    from eve.widgets import recipe

    many = {f"s{i}": {"type": "weather"} for i in range(5)}
    assert "4" in recipe.validate({**V2, "sources": many}, action_accepts=ok)


def test_v1_recipes_upgrade_to_the_chart_preset():
    from eve.widgets import recipe

    v1 = {"sources": [{"type": "records", "collection": "alpha"}], "metric": {"op": "count"}}
    upgraded = recipe.upgrade(v1)
    assert upgraded["version"] == 2
    assert upgraded["sources"]["series"]["type"] == "series"


def test_permissions_targets_and_ttl_come_from_the_sources():
    from eve.widgets import recipe

    both = {**V2, "sources": {**V2["sources"], "sky": {"type": "weather"}}}
    assert recipe.required_permissions(both) == ["home.control"]
    assert recipe.declared_targets(both) == {"light.kitchen"}
    assert recipe.ttl_seconds(both) == 10


def test_filters_rules_are_unchanged():
    from eve.widgets import recipe

    assert recipe.validate_filters({"days": 7}) is None
    assert recipe.validate_filters({"days": 0}) == "filters"
