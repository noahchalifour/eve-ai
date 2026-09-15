"""`save_widget` is the only way a widget is created, so every guard that
matters is here."""
from __future__ import annotations

CONFIG = {
    "configurable": {
        "member": {"sub": "sub-noah", "permissions": ["health"]},
    }
}
NO_PERMS = {"configurable": {"member": {"sub": "sub-noah", "permissions": []}}}

RECIPE = {
    "sources": [{"type": "records", "collection": "alpha.thing"}],
    "metric": {"op": "count"},
}


def _call(tool, args, config=CONFIG):
    return tool.ainvoke(
        {"type": "tool_call", "name": tool.name, "args": args, "id": "t1"},
        config=config,
    )


async def test_saving_a_widget_stores_it_for_the_authenticated_member(monkeypatch):
    from eve.widgets import tools

    seen = {}

    async def fake_create(member_sub, kind, title, recipe, filters):
        seen.update(member_sub=member_sub, kind=kind, title=title, recipe=recipe)
        return {"id": "res-1", "revision": 1}

    monkeypatch.setattr(tools.store, "create", fake_create)

    result = await _call(
        tools.save_widget,
        {"title": "Alpha", "kind": "chart", "recipe": RECIPE},
    )

    assert seen["member_sub"] == "sub-noah"
    assert seen["kind"] == "chart"
    assert "res-1" in result.content


async def test_an_invalid_recipe_is_refused_with_a_diagnostic(monkeypatch):
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store an invalid recipe")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.save_widget,
        {
            "title": "Bad",
            "kind": "chart",
            "recipe": {"sources": [{"type": "http", "url": "https://x"}]},
        },
    )

    assert "source-type" in result.content


async def test_an_unknown_kind_is_refused(monkeypatch):
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store an unknown kind")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.save_widget, {"title": "X", "kind": "dashboard", "recipe": RECIPE}
    )

    assert "kind" in result.content.lower()


async def test_an_overlong_title_is_refused(monkeypatch):
    from eve.widgets import recipe as recipe_rules
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store an overlong title")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.save_widget,
        {
            "title": "T" * (recipe_rules.MAX_NAME + 1),
            "kind": "chart",
            "recipe": RECIPE,
        },
    )

    assert "title" in result.content.lower()


async def test_a_health_recipe_requires_the_health_permission(monkeypatch):
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store without permission")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.save_widget,
        {
            "title": "H",
            "kind": "chart",
            "recipe": {
                "sources": [{"type": "health", "metric": "activity"}],
                "metric": {"op": "count"},
            },
        },
        config=NO_PERMS,
    )

    assert "permission" in result.content.lower()


async def test_a_records_only_recipe_needs_no_extra_permission(monkeypatch):
    """Reading your own records is the least privileged thing there is."""
    from eve.widgets import tools

    async def fake_create(member_sub, kind, title, recipe, filters):
        return {"id": "res-2", "revision": 1}

    monkeypatch.setattr(tools.store, "create", fake_create)

    result = await _call(
        tools.save_widget,
        {"title": "Alpha", "kind": "chart", "recipe": RECIPE},
        config=NO_PERMS,
    )

    assert "res-2" in result.content


async def test_an_ambient_turn_cannot_save_a_widget(monkeypatch):
    """The ambient token can impersonate any member (spec risk)."""
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("ambient turns must not author widgets")

    monkeypatch.setattr(tools.store, "create", unreachable)

    ambient = {
        "configurable": {
            "member": {"sub": "sub-noah", "permissions": []},
            "is_ambient": True,
        }
    }
    result = await _call(
        tools.save_widget,
        {"title": "X", "kind": "chart", "recipe": RECIPE},
        config=ambient,
    )

    assert "cannot" in result.content.lower()


async def test_storage_failure_degrades_to_a_string(monkeypatch):
    from eve.widgets import tools

    async def boom(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(tools.store, "create", boom)

    result = await _call(
        tools.save_widget, {"title": "A", "kind": "chart", "recipe": RECIPE}
    )

    assert "error" in result.content.lower()
    assert "postgres" not in result.content