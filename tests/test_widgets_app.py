"""Routes. Authentication is Aegra's; ownership is ours, and the difference
is the whole point of these tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from eve.widgets import app as widgets_app

    # Stand in for Aegra's require_auth, which is what production uses. The
    # widget routes depend on it explicitly (aegra-api 0.10.3's
    # enable_custom_route_auth walk is a no-op), so tests must override it
    # too, or the overridden current_member would never be reached.
    widgets_app.app.dependency_overrides[widgets_app.require_auth] = lambda: None
    widgets_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": ["health"]}
    )
    yield TestClient(widgets_app.app)
    widgets_app.app.dependency_overrides.clear()


def test_capabilities_names_the_supported_kinds(client):
    response = client.get("/provider-resources/v1/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["protocol"] == "provider-resource/1.0"
    assert "chart" in body["kinds"]


def test_listing_returns_only_this_members_resources(client, monkeypatch):
    from eve.widgets import app as widgets_app

    seen = {}

    async def fake_list_for(member_sub):
        seen["member_sub"] = member_sub
        return [{"id": "res-1", "kind": "chart", "title": "A", "recipe": {},
                 "filters": {}, "revision": 1, "updated_at": None}]

    monkeypatch.setattr(widgets_app.store, "list_for", fake_list_for)

    response = client.get("/provider-resources/v1/resources")

    assert response.status_code == 200
    assert seen["member_sub"] == "sub-noah"


def test_a_foreign_resource_is_404_not_403(client, monkeypatch):
    """403 would confirm the id exists; 404 tells a prober nothing."""
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return None

    monkeypatch.setattr(widgets_app.store, "get", fake_get)

    response = client.get("/provider-resources/v1/resources/res-x/snapshot")

    assert response.status_code == 404


def test_a_snapshot_never_runs_the_graph(client, monkeypatch):
    """The whole point of the feature: refresh costs no model call."""
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [], "metric": {"op": "count"}},
                "filters": {}, "revision": 1}

    async def fake_snapshot(resource, member_sub, **kwargs):
        return {"resourceId": "res-1", "revision": 1, "view": {}, "sources": {}}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)

    response = client.get("/provider-resources/v1/resources/res-1/snapshot")

    assert response.status_code == 200
    assert response.json()["resourceId"] == "res-1"


def test_a_filters_action_persists_and_returns_a_fresh_snapshot(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [], "metric": {"op": "count"}},
                "filters": {"days": 30}, "revision": 1}

    async def fake_update(member_sub, resource_id, filters, expected_revision):
        assert filters == {"days": 7}
        return {**(await fake_get(member_sub, resource_id)),
                "filters": filters, "revision": 2}

    async def fake_snapshot(resource, member_sub, **kwargs):
        return {"resourceId": "res-1", "revision": resource["revision"],
                "filters": resource["filters"], "view": {}, "sources": {}}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.store, "update_filters", fake_update)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {"days": 7},
              "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert response.json()["revision"] == 2


def test_a_stale_revision_returns_409_with_the_current_snapshot(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [], "metric": {"op": "count"}},
                "filters": {"days": 7}, "revision": 2}

    async def fake_update(member_sub, resource_id, filters, expected_revision):
        return None

    async def fake_snapshot(resource, member_sub, **kwargs):
        return {"resourceId": "res-1", "revision": 2, "view": {}, "sources": {}}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.store, "update_filters", fake_update)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {"days": 90},
              "expectedRevision": 1},
    )

    assert response.status_code == 409
    assert response.json()["revision"] == 2


def test_an_unknown_action_type_is_rejected(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A", "recipe": {},
                "filters": {}, "revision": 1}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "resource.exfiltrate", "input": {}, "expectedRevision": 1},
    )

    assert response.status_code == 400


def test_invalid_filters_are_rejected_before_storage(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A", "recipe": {},
                "filters": {}, "revision": 1}

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not persist invalid filters")

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.store, "update_filters", unreachable)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {"days": 99999},
              "expectedRevision": 1},
    )

    assert response.status_code == 400


def test_a_snapshot_requires_the_recipes_permissions(client, monkeypatch):
    """Checked at the route too, not only at authoring time: permissions can
    be revoked after a widget was saved."""
    from eve.widgets import app as widgets_app

    widgets_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": []}
    )

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [{"type": "health", "metric": "activity"}],
                           "metric": {"op": "count"}},
                "filters": {}, "revision": 1}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)

    response = client.get("/provider-resources/v1/resources/res-1/snapshot")

    assert response.status_code == 403


def test_deleting_a_foreign_resource_is_404(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_delete(member_sub, resource_id):
        return False

    monkeypatch.setattr(widgets_app.store, "delete", fake_delete)

    response = client.delete("/provider-resources/v1/resources/res-x")

    assert response.status_code == 404


def test_a_body_supplied_member_is_ignored(client, monkeypatch):
    """Identity is the authenticated principal's, never the request's."""
    from eve.widgets import app as widgets_app

    seen = {}

    async def fake_get(member_sub, resource_id):
        seen["member_sub"] = member_sub
        return None

    monkeypatch.setattr(widgets_app.store, "get", fake_get)

    client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {"days": 7},
              "expectedRevision": 1, "member_sub": "sub-kendra"},
    )

    assert seen["member_sub"] == "sub-noah"


ENTITY_RESOURCE = {
    "id": "res-2", "kind": "entity", "title": "Kitchen", "revision": 2, "filters": {},
    "recipe": {"version": 2, "sources": {"entity": {"type": "home.entity", "entities": ["light.kitchen"]}},
               "template": [{"id": "root", "type": "card", "properties": {"actionId": "home.toggle",
                             "actionValue": "light.kitchen"}, "children": []}]},
}


@pytest.fixture
def home_client(monkeypatch):
    from eve.widgets import app as widgets_app

    widgets_app.app.dependency_overrides[widgets_app.require_auth] = lambda: None
    widgets_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": ["home.control"]}
    )

    async def fake_get(member_sub, resource_id):
        return ENTITY_RESOURCE if resource_id == "res-2" else None

    async def fake_snapshot(resource, member_sub):
        return {"resourceId": resource["id"], "revision": resource["revision"]}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)
    yield TestClient(widgets_app.app)
    widgets_app.app.dependency_overrides.clear()


def _post(client, type, target, revision=2):
    return client.post("/provider-resources/v1/resources/res-2/actions",
                       json={"type": type, "input": {"target": target}, "expectedRevision": revision})


def test_capabilities_advertise_actions_with_risk_and_label(client):
    body = client.get("/provider-resources/v1/capabilities").json()
    toggle = next(a for a in body["actions"] if a["type"] == "home.toggle")
    assert toggle == {"type": "home.toggle", "risk": "safe", "label": "Toggle"}
    assert "weather" in body["sourceTypes"] and "entity" in body["kinds"]


def test_a_toggle_runs_and_returns_the_fresh_snapshot(home_client, monkeypatch):
    from dataclasses import replace

    from eve.widgets.actions import REGISTRY

    ran = []

    async def fake_run(ctx):
        ran.append(ctx.target)

    monkeypatch.setitem(REGISTRY, "home.toggle", replace(REGISTRY["home.toggle"], run=fake_run))

    response = _post(home_client, "home.toggle", "light.kitchen")

    assert response.status_code == 200
    assert ran == ["light.kitchen"]


def test_a_target_the_widget_never_declared_is_400(home_client):
    """A tampered client cannot turn a light widget into a door opener."""
    assert _post(home_client, "home.toggle", "lock.front_door").status_code == 400


def test_an_unknown_action_is_400(home_client):
    assert _post(home_client, "home.explode", "light.kitchen").status_code == 400


def test_an_action_needs_its_permission(home_client):
    from eve.widgets import app as widgets_app

    widgets_app.app.dependency_overrides[widgets_app.current_member] = lambda: {"sub": "sub-noah", "permissions": []}
    assert _post(home_client, "home.toggle", "light.kitchen").status_code == 403


def test_an_upstream_failure_is_502(home_client, monkeypatch):
    from eve.widgets.actions import REGISTRY
    from eve.widgets.actions.base import ActionFailed

    from dataclasses import replace

    async def boom(ctx):
        raise ActionFailed("ha down")

    monkeypatch.setitem(REGISTRY, "home.toggle", replace(REGISTRY["home.toggle"], run=boom))
    response = _post(home_client, "home.toggle", "light.kitchen")
    assert response.status_code == 502
    assert "ha down" not in response.text