"""Dashboard routes: owner-scoped, library-backed, revision-guarded."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

DEVICE = "device-12345678"
BASE = f"/provider-resources/v1/dashboards/{DEVICE}"

ROW = {
    "id": "d-1", "device_id": DEVICE, "purpose": "Kitchen", "columns": 4, "revision": 3,
    "layout": [
        {"resourceId": "w-1", "sizes": ["2x2", "4x2"], "x": 0, "y": 0, "w": 2, "h": 2},
        {"resourceId": "w-gone", "sizes": ["4x2"], "x": 0, "y": 2, "w": 4, "h": 2},
    ],
}


@pytest.fixture
def client(monkeypatch):
    from eve import http_app
    from eve.dashboards import app as dashboards_app
    from eve.widgets import app as widgets_app

    http_app.app.dependency_overrides[widgets_app.require_auth] = lambda: None
    http_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": []}
    )

    async def list_for(sub):
        assert sub == "sub-noah"
        return [{"id": "w-1", "kind": "entity", "title": "Kitchen light"}]

    monkeypatch.setattr(dashboards_app.widgets, "list_for", list_for)
    yield TestClient(http_app.app)
    http_app.app.dependency_overrides.clear()


def _store(monkeypatch, **fns):
    from eve.dashboards import app as dashboards_app

    for name, fn in fns.items():
        monkeypatch.setattr(dashboards_app.store, name, fn)


def test_no_dashboard_is_a_404(client, monkeypatch):
    async def get(sub, device_id):
        return None

    _store(monkeypatch, get=get)
    assert client.get(BASE).status_code == 404


def test_a_dashboard_lists_its_tiles_with_library_titles(client, monkeypatch):
    seen = {}

    async def get(sub, device_id):
        seen.update(sub=sub, device_id=device_id)
        return ROW

    _store(monkeypatch, get=get)
    body = client.get(BASE).json()

    assert seen == {"sub": "sub-noah", "device_id": DEVICE}
    assert body["purpose"] == "Kitchen" and body["columns"] == 4 and body["revision"] == 3
    # A tile whose widget left the library drops out on read.
    assert [t["resourceId"] for t in body["tiles"]] == ["w-1"]
    assert body["tiles"][0]["title"] == "Kitchen light"
    assert body["tiles"][0]["sizes"] == ["2x2", "4x2"]


def test_an_invalid_device_id_is_a_400(client):
    assert client.get("/provider-resources/v1/dashboards/bad").status_code == 400


def test_saving_a_layout_validates_against_the_stored_one(client, monkeypatch):
    written = {}

    async def get(sub, device_id):
        return ROW

    async def update_layout(sub, device_id, layout, expected_revision):
        written.update(layout=layout, rev=expected_revision)
        return {**ROW, "layout": layout, "revision": 4}

    _store(monkeypatch, get=get, update_layout=update_layout)
    response = client.put(f"{BASE}/layout", json={
        "layout": [{"resourceId": "w-1", "x": 0, "y": 0, "w": 4, "h": 2}], "expectedRevision": 3})

    assert response.status_code == 200
    assert response.json()["revision"] == 4
    assert written["rev"] == 3
    assert written["layout"][0]["sizes"] == ["2x2", "4x2"]


def test_an_illegal_layout_is_a_400_and_writes_nothing(client, monkeypatch):
    async def get(sub, device_id):
        return ROW

    async def update_layout(*_):
        raise AssertionError("must not write")

    _store(monkeypatch, get=get, update_layout=update_layout)
    response = client.put(f"{BASE}/layout", json={
        "layout": [{"resourceId": "w-1", "x": 0, "y": 0, "w": 2, "h": 4}], "expectedRevision": 3})
    assert response.status_code == 400


def test_a_stale_revision_is_a_409_carrying_the_current_dashboard(client, monkeypatch):
    async def get(sub, device_id):
        return ROW

    async def update_layout(*_):
        return None

    _store(monkeypatch, get=get, update_layout=update_layout)
    response = client.put(f"{BASE}/layout", json={"layout": [], "expectedRevision": 1})

    assert response.status_code == 409
    assert response.json()["revision"] == 3


def test_reset_deletes_only_the_dashboard(client, monkeypatch):
    from eve.dashboards import app as dashboards_app

    async def delete(sub, device_id):
        return True

    async def widget_delete(*_):
        raise AssertionError("a reset must not delete library widgets")

    _store(monkeypatch, delete=delete)
    monkeypatch.setattr(dashboards_app.widgets, "delete", widget_delete)
    assert client.delete(BASE).status_code == 204


def test_capabilities_advertise_the_dashboard_feature(client):
    body = client.get("/provider-resources/v1/capabilities").json()
    assert "dashboard" in body["features"]
