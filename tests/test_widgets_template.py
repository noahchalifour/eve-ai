# tests/test_widgets_template.py
from __future__ import annotations

import pytest


@pytest.fixture
def sources():
    from eve.widgets.sources import parse

    return {
        "kitchen": parse({"type": "home.entity", "entities": ["light.kitchen"]}),
        "cal": parse({"type": "calendar"}),
    }


def accepts(action_id, target):
    return action_id == "home.toggle" and target.startswith("light.")


def card(*children, **props):
    return {"id": "root", "type": "card", "properties": props, "children": list(children)}


def text(value, id="t"):
    return {"id": id, "type": "text", "properties": {"text": value}, "children": []}


def test_a_valid_template_passes(sources):
    from eve.widgets import template

    components = [card(text("$data.kitchen.primary.state_label"), actionId="home.toggle", actionValue="light.kitchen")]
    assert template.validate(components, sources, action_accepts=accepts) is None


def test_unknown_alias_is_named_in_the_error(sources):
    from eve.widgets import template

    error = template.validate([card(text("$data.garage.primary.state"))], sources, action_accepts=accepts)
    assert "garage" in error and "kitchen" in error


def test_unknown_field_is_rejected(sources):
    from eve.widgets import template

    error = template.validate([card(text("$data.kitchen.nope"))], sources, action_accepts=accepts)
    assert "nope" in error


def test_an_action_on_an_undeclared_target_is_rejected(sources):
    from eve.widgets import template

    components = [card(text("x"), actionId="home.toggle", actionValue="light.bedroom")]
    assert "light.bedroom" in template.validate(components, sources, action_accepts=accepts)


def test_an_unregistered_action_is_rejected(sources):
    from eve.widgets import template

    components = [card(text("x"), actionId="home.explode", actionValue="light.kitchen")]
    assert "home.explode" in template.validate(components, sources, action_accepts=accepts)


def test_item_outside_a_repeat_is_rejected(sources):
    from eve.widgets import template

    assert "$item" in template.validate([card(text("$item.summary"))], sources, action_accepts=accepts)


def test_repeat_expands_items_and_strips_template_properties():
    from eve.widgets import template

    components = [{"id": "events", "type": "list",
                   "properties": {"repeat": "$data.cal.items", "limit": 2, "empty": "Nothing"},
                   "children": [text("$item.summary", id="s")]}]
    data = {"cal": {"items": [{"summary": "A"}, {"summary": "B"}, {"summary": "C"}]}}

    rendered, problems = template.render(components, data)

    assert rendered[0]["properties"] == {}
    assert [c["properties"]["text"] for c in rendered[0]["children"]] == ["A", "B"]
    assert [c["id"] for c in rendered[0]["children"]] == ["s_0", "s_1"]
    assert problems == []


def test_repeat_over_nothing_shows_the_empty_text():
    from eve.widgets import template

    components = [{"id": "events", "type": "list", "properties": {"repeat": "$data.cal.items", "empty": "Nothing"},
                   "children": [text("$item.summary")]}]
    rendered, _ = template.render(components, {"cal": {"items": []}})
    assert rendered[0]["children"][0]["properties"]["text"] == "Nothing"


def test_an_unresolvable_binding_degrades_to_a_dash_not_a_broken_widget():
    """A binding the client cannot resolve makes the WHOLE surface fall back
    to "This content can't be shown". One missing value must not do that."""
    from eve.widgets import template

    rendered, problems = template.render([card(text("$data.kitchen.primary.state"))], {"kitchen": {}})
    assert rendered[0]["children"][0]["properties"]["text"] == "—"
    assert problems == ["binding"]


def test_a_template_that_fails_the_catalog_is_rejected(sources):
    from eve.widgets import template

    bad = [{"id": "x", "type": "marquee", "properties": {}, "children": []}]
    assert "component-type" in template.validate(bad, sources, action_accepts=accepts)
