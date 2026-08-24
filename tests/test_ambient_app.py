from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from eve_ambient import app as app_module
from eve_ambient.types import Signal


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("EVE_AMBIENT_HA_WEBHOOK_SECRET", "ha-secret")
    monkeypatch.setenv("EVE_AMBIENT_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app_module.app)


def test_healthz_reports_whether_ambient_is_enabled(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["ambient_enabled"] is False


def test_the_webhook_rejects_a_wrong_secret(client):
    response = client.post(
        "/signals/home-assistant",
        headers={"x-eve-ambient-secret": "wrong"},
        json={"entity_id": "binary_sensor.garage", "state": "open"},
    )
    assert response.status_code == 401


def test_the_webhook_rejects_a_missing_secret(client):
    response = client.post(
        "/signals/home-assistant",
        json={"entity_id": "binary_sensor.garage", "state": "open"},
    )
    assert response.status_code == 401


def test_the_webhook_accepts_and_queues(monkeypatch, client):
    """202 rather than waiting: a compose turn takes far longer than Home
    Assistant will hold a webhook open."""
    handled = []

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    monkeypatch.setattr(app_module, "handle_signal", _handle)
    response = client.post(
        "/signals/home-assistant",
        headers={"x-eve-ambient-secret": "ha-secret"},
        json={
            "entity_id": "binary_sensor.garage",
            "state": "open",
            "friendly_name": "Garage door",
        },
    )
    assert response.status_code == 202
    assert handled[0].source == "home"
    assert handled[0].key == "binary_sensor.garage:open"
    assert "Garage door" in handled[0].summary


def test_the_webhook_rejects_a_payload_without_an_entity(client):
    response = client.post(
        "/signals/home-assistant",
        headers={"x-eve-ambient-secret": "ha-secret"},
        json={"state": "open"},
    )
    assert response.status_code == 422


def test_a_home_signal_is_household_scoped():
    signal = app_module.from_webhook(
        {"entity_id": "lock.front", "state": "unlocked", "friendly_name": "Front door"}
    )
    assert isinstance(signal, Signal)
    assert signal.member_sub is None
    assert signal.source == "home"


async def test_the_first_poll_of_a_source_primes_without_notifying(monkeypatch):
    """A month of calendar entries must not become a month of notifications."""
    seen, handled = [], []

    async def _has_any(source):
        return False

    async def _mark_seen(source, key):
        seen.append(key)

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (_fake_source(),))

    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert handled == []
    assert seen == ["k1", "k2"]
    assert counts["primed"] == 2


async def test_a_later_poll_runs_the_pipeline(monkeypatch):
    handled = []

    async def _has_any(source):
        return True

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "filtered"

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (_fake_source(),))

    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert [s.key for s in handled] == ["k1", "k2"]
    assert counts["filtered"] == 2


async def test_one_failing_source_does_not_stop_the_others(monkeypatch):
    handled = []

    async def _has_any(source):
        return True

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    async def _broken_poll(member_sub):
        raise RuntimeError("monarch exploded")

    from eve_ambient.sources import Source

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(
        app_module,
        "SOURCES",
        (Source("broken", False, "finances", _broken_poll), _fake_source()),
    )
    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert [s.key for s in handled] == ["k1", "k2"]
    assert counts["errors"] == 1


async def test_a_failing_signal_does_not_stop_its_siblings(monkeypatch):
    async def _has_any(source):
        return True

    calls = {"n": 0}

    async def _handle(signal, **kwargs):
        calls["n"] += 1
        if signal.key == "k1":
            raise RuntimeError("something in the pipeline")
        return "sent"

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (_fake_source(),))
    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert calls["n"] == 2
    assert counts["errors"] == 1


def _fake_source():
    from eve_ambient.sources import Source

    async def _poll(member_sub):
        return [
            Signal(
                source="fake", key=key, occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
                member_sub=None, summary=f"summary {key}", payload={},
            )
            for key in ("k1", "k2")
        ]

    return Source("fake", False, "finances", _poll)
