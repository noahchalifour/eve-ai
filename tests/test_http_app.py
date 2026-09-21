"""One app, two routers. The widget routes must behave exactly as they did
when they were mounted directly."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from eve import http_app
    from eve.widgets import app as widgets_app

    http_app.app.dependency_overrides[widgets_app.require_auth] = lambda: None
    http_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": ["health"]}
    )
    yield TestClient(http_app.app)
    http_app.app.dependency_overrides.clear()


def test_the_widget_capabilities_route_is_still_mounted(client):
    response = client.get("/provider-resources/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["protocol"] == "provider-resource/1.0"


def test_aegra_points_at_the_combined_app():
    """A refactor that leaves aegra.json behind silently serves the old app."""
    with open("aegra.json") as handle:
        config = json.load(handle)

    assert config["http"]["app"] == "./src/eve/http_app.py:app"


def test_the_conflict_flattener_still_applies(client, monkeypatch):
    """The 409-carries-a-snapshot behaviour lives on the app, not the router,
    so a new app must re-register it or every conflict changes shape."""
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [], "metric": {"op": "count"}},
                "filters": {}, "revision": 5}

    async def fake_update(*args, **kwargs):
        return None

    async def fake_snapshot(resource, member_sub, **kwargs):
        return {"resourceId": "res-1", "revision": 5, "view": {}, "sources": {}}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.store, "update_filters", fake_update)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {}, "expectedRevision": 1},
    )

    assert response.status_code == 409
    # Flattened: the snapshot IS the body, not nested under `detail`.
    assert response.json()["resourceId"] == "res-1"
