"""Tests for the stub Home Assistant itself (`tests/fixtures/stub_home_assistant.py`),
using FastAPI's `TestClient` directly - no server, no `eve-tools` in between.
These exist so the stub's lock/media behaviour (added for ENG-269's Part C
walkthrough) is verified in isolation from the widgets code that consumes it.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """A fresh import per test: `_states`/`_attributes` are module-level
    dicts mutated in place by `call_service`, so tests would otherwise leak
    state into each other through Python's module cache."""
    from tests.fixtures import stub_home_assistant

    importlib.reload(stub_home_assistant)
    with TestClient(stub_home_assistant.app) as test_client:
        yield test_client


def test_lock_starts_locked_with_a_friendly_name(client):
    response = client.get("/api/states/lock.front_door")
    assert response.json() == {
        "entity_id": "lock.front_door",
        "state": "locked",
        "attributes": {"friendly_name": "Front door"},
    }


def test_lock_service_flips_locked_and_unlocked(client):
    client.post("/api/services/lock/unlock", json={"entity_id": "lock.front_door"})
    assert client.get("/api/states/lock.front_door").json()["state"] == "unlocked"

    client.post("/api/services/lock/lock", json={"entity_id": "lock.front_door"})
    assert client.get("/api/states/lock.front_door").json()["state"] == "locked"


def test_media_player_starts_playing_with_full_attributes(client):
    response = client.get("/api/states/media_player.living_room")
    body = response.json()
    assert body["state"] == "playing"
    assert body["attributes"] == {
        "friendly_name": "Living room",
        "media_title": "Blue in Green",
        "media_artist": "Miles Davis",
        "volume_level": 0.4,
    }


def test_media_play_pause_toggles(client):
    client.post("/api/services/media_player/media_play_pause", json={"entity_id": "media_player.living_room"})
    assert client.get("/api/states/media_player.living_room").json()["state"] == "paused"

    client.post("/api/services/media_player/media_play_pause", json={"entity_id": "media_player.living_room"})
    assert client.get("/api/states/media_player.living_room").json()["state"] == "playing"


def test_media_next_and_previous_are_accepted_no_ops(client):
    before = client.get("/api/states/media_player.living_room").json()
    response = client.post(
        "/api/services/media_player/media_next_track", json={"entity_id": "media_player.living_room"}
    )
    assert response.status_code == 200
    response = client.post(
        "/api/services/media_player/media_previous_track", json={"entity_id": "media_player.living_room"}
    )
    assert response.status_code == 200
    after = client.get("/api/states/media_player.living_room").json()
    assert after == before


def test_volume_up_and_down_step_and_clamp(client):
    client.post("/api/services/media_player/volume_up", json={"entity_id": "media_player.living_room"})
    volume = client.get("/api/states/media_player.living_room").json()["attributes"]["volume_level"]
    assert volume == pytest.approx(0.5)

    # Push past 1.0: clamps, doesn't overshoot.
    for _ in range(10):
        client.post("/api/services/media_player/volume_up", json={"entity_id": "media_player.living_room"})
    volume = client.get("/api/states/media_player.living_room").json()["attributes"]["volume_level"]
    assert volume == pytest.approx(1.0)

    # Push past 0.0: clamps at the bottom too.
    for _ in range(20):
        client.post("/api/services/media_player/volume_down", json={"entity_id": "media_player.living_room"})
    volume = client.get("/api/states/media_player.living_room").json()["attributes"]["volume_level"]
    assert volume == pytest.approx(0.0)


def test_existing_light_turn_on_and_off_still_work(client):
    client.post("/api/services/light/turn_on", json={"entity_id": "light.kitchen"})
    assert client.get("/api/states/light.kitchen").json()["state"] == "on"

    client.post("/api/services/light/turn_off", json={"entity_id": "light.kitchen"})
    assert client.get("/api/states/light.kitchen").json()["state"] == "off"


def test_list_states_includes_the_new_entities_with_full_attributes(client):
    entities = {e["entity_id"]: e for e in client.get("/api/states").json()}
    assert entities["lock.front_door"]["attributes"]["friendly_name"] == "Front door"
    assert entities["media_player.living_room"]["attributes"]["media_title"] == "Blue in Green"
    # Lights are unaffected: still just a derived friendly_name.
    assert entities["light.kitchen"]["attributes"] == {"friendly_name": "kitchen"}
