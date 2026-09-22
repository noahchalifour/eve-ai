"""Authentication is Aegra's; ownership is ours. Deliberately no POST: a
routine is authored in conversation, where Eve can push back on a vague
instruction, never from the screen."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

ROW = {
    "id": "r-1",
    "member_sub": "sub-noah",
    "title": "Flights",
    "instruction": "Check fares.",
    "cadence": {"daily_at": "08:00"},
    "timezone": "America/Vancouver",
    "status": "active",
    "next_run_at": datetime(2026, 1, 11, 16, 0, tzinfo=UTC),
    "last_run_at": None,
    "last_outcome": None,
    "consecutive_failures": 0,
    "expires_at": None,
    "revision": 1,
    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
}


@pytest.fixture
def client():
    from eve import http_app
    from eve.routines import app as routines_app

    http_app.app.dependency_overrides[routines_app.require_auth] = lambda: None
    http_app.app.dependency_overrides[routines_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": ["routines"]}
    )
    yield TestClient(http_app.app)
    http_app.app.dependency_overrides.clear()


def test_capabilities_names_the_protocol_and_the_cadence_vocabulary(client):
    response = client.get("/provider-resources/v1/routines/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["protocol"] == "provider-routine/1.0"
    assert set(body["cadenceKinds"]) == {"every_hours", "daily_at", "weekly_at"}
    assert body["limits"]["minEveryHours"] == 1


def test_there_is_no_create_route(client):
    """Authoring happens in conversation. A POST here would let a client
    write an instruction Eve never got to question."""
    response = client.post("/provider-resources/v1/routines", json={})

    assert response.status_code in (404, 405)


def test_listing_returns_only_this_members_routines(client, monkeypatch):
    from eve.routines import app as routines_app

    seen = {}

    async def fake_list_for(member_sub):
        seen["member_sub"] = member_sub
        return [ROW]

    monkeypatch.setattr(routines_app.store, "list_for", fake_list_for)

    response = client.get("/provider-resources/v1/routines")

    assert response.status_code == 200
    assert seen["member_sub"] == "sub-noah"
    assert response.json()["routines"][0]["routineId"] == "r-1"


def test_a_foreign_routine_is_404_not_403(client, monkeypatch):
    from eve.routines import app as routines_app

    async def fake_update(*args, **kwargs):
        return None

    async def fake_get(member_sub, routine_id):
        return None

    monkeypatch.setattr(routines_app.store, "update", fake_update)
    monkeypatch.setattr(routines_app.store, "get", fake_get)

    response = client.patch(
        "/provider-resources/v1/routines/r-x",
        json={"status": "paused", "expectedRevision": 1},
    )

    assert response.status_code == 404


def test_pausing_is_accepted(client, monkeypatch):
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen.update(fields)
        return {**ROW, "status": "paused", "revision": 2}

    monkeypatch.setattr(routines_app.store, "update", fake_update)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"status": "paused", "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert seen["status"] == "paused"


def test_expired_cannot_be_assigned_by_a_client(client, monkeypatch):
    """`expired` is the server's to assign, from the expiry it was given."""
    from eve.routines import app as routines_app

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not write a client-supplied expired status")

    monkeypatch.setattr(routines_app.store, "update", unreachable)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"status": "expired", "expectedRevision": 1},
    )

    assert response.status_code == 400


def test_an_invalid_cadence_is_refused(client, monkeypatch):
    from eve.routines import app as routines_app

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store an invalid cadence")

    monkeypatch.setattr(routines_app.store, "update", unreachable)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"cadence": {"every_hours": 0}, "expectedRevision": 1},
    )

    assert response.status_code == 400


def test_a_cadence_change_recomputes_the_next_run(client, monkeypatch):
    """Computed server-side: a client that sent its own next_run_at could
    schedule a routine to fire immediately and repeatedly."""
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen.update(fields)
        return {**ROW, "revision": 2}

    monkeypatch.setattr(routines_app.store, "get", _async_return(ROW))
    monkeypatch.setattr(routines_app.store, "update", fake_update)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"cadence": {"every_hours": 6}, "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert "next_run_at" in seen


def test_a_client_supplied_next_run_at_is_dropped(client, monkeypatch):
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen.update(fields)
        return {**ROW, "revision": 2}

    monkeypatch.setattr(routines_app.store, "update", fake_update)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"title": "New", "next_run_at": "2026-01-01T00:00:00+00:00",
              "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert "next_run_at" not in seen


def test_a_member_sub_in_the_body_is_ignored(client, monkeypatch):
    """A resource id is a locator, and a body field is never an identity."""
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen["member_sub"] = member_sub
        seen.update(fields)
        return {**ROW, "revision": 2}

    monkeypatch.setattr(routines_app.store, "update", fake_update)

    client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"title": "New", "member_sub": "sub-kendra", "expectedRevision": 1},
    )

    assert seen["member_sub"] == "sub-noah"


def test_a_stale_revision_is_409_carrying_the_current_routine(client, monkeypatch):
    from eve.routines import app as routines_app

    async def fake_update(*args, **kwargs):
        return None

    monkeypatch.setattr(routines_app.store, "update", fake_update)
    monkeypatch.setattr(routines_app.store, "get", _async_return({**ROW, "revision": 7}))

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"title": "New", "expectedRevision": 1},
    )

    assert response.status_code == 409
    assert response.json()["revision"] == 7


def test_resuming_a_paused_routine_clears_its_failures(client, monkeypatch):
    """The member resuming it is the acknowledgement the counter waited for."""
    from eve.routines import app as routines_app

    seen = {}
    clear_failures_calls = []

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen.update(fields)
        return {**ROW, "revision": 2}

    async def fake_clear_failures(member_sub, routine_id):
        clear_failures_calls.append((member_sub, routine_id))

    monkeypatch.setattr(routines_app.store, "update", fake_update)
    monkeypatch.setattr(routines_app.store, "clear_failures", fake_clear_failures)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"status": "active", "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert clear_failures_calls == [("sub-noah", "r-1")]


def test_deleting_a_missing_routine_is_404(client, monkeypatch):
    from eve.routines import app as routines_app

    async def fake_delete(member_sub, routine_id):
        return False

    monkeypatch.setattr(routines_app.store, "delete", fake_delete)

    response = client.delete("/provider-resources/v1/routines/r-x")

    assert response.status_code == 404


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn
