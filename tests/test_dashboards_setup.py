"""The dashboard builder: library-first, same guards as save_widget, and a
terminal frame on every path."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from eve.dashboards import setup
from tests.conftest import FakeToolCallingModel

MEMBER = {"sub": "sub-noah", "name": "Noah", "permissions": ["home.control"]}


@pytest.fixture
def library(monkeypatch):
    rows = {
        "w-weather": {"id": "w-weather", "kind": "weather", "title": "Weather"},
    }
    created = []

    async def list_for(sub):
        assert sub == "sub-noah"
        return list(rows.values())

    async def get(sub, resource_id):
        return rows.get(resource_id) if sub == "sub-noah" else None

    async def create(sub, kind, title, recipe, filters):
        row = {"id": f"w-new-{len(created)}", "kind": kind, "title": title}
        created.append(row)
        rows[row["id"]] = row
        return row

    monkeypatch.setattr(setup.widgets, "list_for", list_for)
    monkeypatch.setattr(setup.widgets, "get", get)
    monkeypatch.setattr(setup.widgets, "create", create)
    return created


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


async def test_an_existing_library_widget_is_reused_not_copied(library):
    chosen = []
    tools = setup.build_tools(MEMBER, chosen)
    await _tool(tools, "use_widget").ainvoke({"resource_id": "w-weather"})

    assert [c["resourceId"] for c in chosen] == ["w-weather"]
    assert library == []
    assert chosen[0]["default"] == "4x2"


async def test_a_new_widget_lands_in_the_library(library):
    chosen = []
    tools = setup.build_tools(MEMBER, chosen)
    out = await _tool(tools, "save_dashboard_widget").ainvoke(
        {"title": "Kitchen", "preset": "entity", "options": {"entity": "light.kitchen"}})

    assert "Added" in out
    assert library[0]["kind"] == "entity"
    assert chosen[0]["resourceId"] == library[0]["id"]
    assert chosen[0]["sizes"] == ["2x2", "4x2"]


async def test_a_new_widget_passes_the_save_widget_guards(library):
    chosen = []
    tools = setup.build_tools({**MEMBER, "permissions": []}, chosen)
    out = await _tool(tools, "save_dashboard_widget").ainvoke(
        {"title": "Kitchen", "preset": "entity", "options": {"entity": "light.kitchen"}})

    assert "Permission denied" in out
    assert chosen == [] and library == []


async def test_a_foreign_or_missing_widget_cannot_be_used(library):
    chosen = []
    out = await _tool(setup.build_tools(MEMBER, chosen), "use_widget").ainvoke({"resource_id": "nope"})
    assert "No widget" in out and chosen == []


async def test_the_same_widget_is_not_placed_twice(library):
    chosen = []
    use = _tool(setup.build_tools(MEMBER, chosen), "use_widget")
    await use.ainvoke({"resource_id": "w-weather"})
    out = await use.ainvoke({"resource_id": "w-weather"})
    assert "already" in out and len(chosen) == 1


async def test_home_entities_need_the_home_permission():
    out = await _tool(setup.build_tools({**MEMBER, "permissions": []}, []), "find_home_entities").ainvoke({})
    assert "may not" in out


def test_place_uses_each_default_size_in_order():
    placed = setup.place([
        {"resourceId": "a", "sizes": ["4x2"], "default": "4x2"},
        {"resourceId": "b", "sizes": ["2x2"], "default": "2x2"},
        {"resourceId": "c", "sizes": ["2x2"], "default": "2x2"},
    ], 4)
    assert [(p["resourceId"], p["x"], p["y"], p["w"], p["h"]) for p in placed] == [
        ("a", 0, 0, 4, 2), ("b", 0, 2, 2, 2), ("c", 2, 2, 2, 2)]


# --- the node ---------------------------------------------------------------

def _config(request, **principal):
    return {"configurable": {"dashboard_setup": request,
                             "langgraph_auth_user": {"identity": "sub-noah", **principal}}}


REQUEST = {"deviceId": "device-12345678", "purpose": "Kitchen tablet", "columns": 4}


@pytest.fixture
def frames(monkeypatch):
    seen = []
    monkeypatch.setattr(setup, "_emit", seen.append)
    return seen


def _model_that_uses(resource_id):
    def factory(_tier):
        return FakeToolCallingModel(messages=iter([
            AIMessage(content="", tool_calls=[{"name": "use_widget", "args": {"resource_id": resource_id},
                                               "id": "c1"}]),
            AIMessage(content="Done."),
        ]))
    return factory


async def test_the_node_builds_and_saves_the_dashboard(library, frames, monkeypatch):
    saved = {}

    async def replace(sub, device_id, purpose, columns, layout):
        saved.update(sub=sub, device_id=device_id, purpose=purpose, columns=columns, layout=layout)
        return {"revision": 1}

    monkeypatch.setattr(setup.dashboards, "replace", replace)
    node = setup.make_node(_model_that_uses("w-weather"))
    await node({"member": MEMBER}, _config(REQUEST))

    assert saved["device_id"] == "device-12345678" and saved["columns"] == 4
    assert saved["layout"] == [{"resourceId": "w-weather", "sizes": ["4x2", "4x4"], "x": 0, "y": 0, "w": 4, "h": 2}]
    assert frames[0] == {"phase": "Choosing widgets"}
    assert frames[-1] == {"done": {"revision": 1, "tiles": 1}}


async def test_an_empty_choice_ends_with_an_error_and_saves_nothing(library, frames, monkeypatch):
    async def replace(*_):
        raise AssertionError("must not save")

    monkeypatch.setattr(setup.dashboards, "replace", replace)

    def factory(_tier):
        return FakeToolCallingModel(messages=iter([AIMessage(content="Nothing fits.")]))

    await setup.make_node(factory)({"member": MEMBER}, _config(REQUEST))
    assert "error" in frames[-1]


async def test_a_model_failure_ends_with_an_error(library, frames):
    def factory(_tier):
        raise RuntimeError("proxy down")

    await setup.make_node(factory)({"member": MEMBER}, _config(REQUEST))
    assert frames[-1] == {"error": "Couldn't build the dashboard. Try again."}


@pytest.mark.parametrize("bad", [
    {**REQUEST, "deviceId": "x"},
    {**REQUEST, "purpose": "  "},
    {**REQUEST, "purpose": "x" * 281},
    {**REQUEST, "columns": 5},
    {**REQUEST, "columns": True},
])
async def test_a_malformed_request_is_answered_not_run(bad, frames):
    def factory(_tier):
        raise AssertionError("no model call for a bad request")

    await setup.make_node(factory)({"member": MEMBER}, _config(bad))
    assert list(frames[-1]) == ["error"]


async def test_the_ambient_service_cannot_build_a_dashboard(frames):
    """The ambient token can act as any member; a run it makes must not
    create durable widgets in their library."""
    def factory(_tier):
        raise AssertionError("no model call for an ambient run")

    await setup.make_node(factory)({"member": MEMBER}, _config(REQUEST, ambient=True))
    assert "ambient" in frames[-1]["error"]


def test_requested_reads_only_a_dict():
    assert setup.requested({"configurable": {"dashboard_setup": REQUEST}}) == REQUEST
    assert setup.requested({"configurable": {"dashboard_setup": "yes"}}) is None
    assert setup.requested({}) is None
