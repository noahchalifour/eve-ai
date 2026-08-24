import asyncio
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from eve_ambient import app as app_module
from eve_ambient.types import Signal


class _StopLoop(Exception):
    """Raised by a patched `asyncio.sleep` to end an otherwise-infinite
    `_poll_forever` loop after a fixed number of iterations, without ever
    sleeping for real."""


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("EVE_AMBIENT_HA_WEBHOOK_SECRET", "ha-secret")
    monkeypatch.setenv("EVE_AMBIENT_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_background_tasks():
    # The module-level `_background` set is never reset by app.py itself
    # (there's no reason to, in production - the process just runs). Left
    # alone between tests, a task from one test's `client` (created on that
    # `TestClient`'s own background-thread event loop) could still be sitting
    # in the set when a later test's `lifespan` shutdown - or a direct
    # `asyncio.wait(_background, ...)` call - tries to await it on a
    # different event loop entirely.
    app_module._background.clear()
    yield
    app_module._background.clear()


@pytest.fixture
def client():
    # Entered as a context manager so the app's `lifespan` actually runs
    # (startup and shutdown) - a bare `TestClient(app)` never triggers it,
    # which is why `ENABLED=false` starting no task, and the shutdown-cancel
    # path, both need their own lifespan-driving tests below rather than
    # relying on this fixture's absence of a task as evidence of anything.
    with TestClient(app_module.app) as test_client:
        yield test_client


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
        # A real suspension point: without it this test would pass even if
        # `_handle_in_background`'s task reference were dropped entirely,
        # because a coroutine with no `await` runs to completion the moment
        # it's scheduled and never actually exercises retention.
        await asyncio.sleep(0)
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
    # `client` runs its ASGI app on a persistent background thread with its
    # own event loop; the `await asyncio.sleep(0)` above means the task
    # finishes on that loop's own schedule, not necessarily by the instant
    # this (synchronous, main-thread) response comes back. Poll briefly
    # rather than asserting immediately or sleeping a fixed guessed amount.
    for _ in range(200):
        if handled:
            break
        time.sleep(0.005)
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


def test_the_webhook_rejects_a_malformed_json_body(client):
    response = client.post(
        "/signals/home-assistant",
        headers={
            "x-eve-ambient-secret": "ha-secret",
            "content-type": "application/json",
        },
        content=b"{not valid json",
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
    # The two real keys plus the per-source sentinel that makes priming an
    # explicit fact rather than an inference from "any row exists".
    assert seen == ["k1", "k2", "__primed__"]
    assert counts["primed"] == 2


async def test_an_empty_first_poll_still_primes_so_the_next_real_signal_notifies(
    monkeypatch,
):
    """An empty inbox, no transactions yet, nothing in the calendar window:
    the first poll finding nothing must still count as primed, or the next
    tick - the first one with a real signal - gets silently swallowed as
    "still priming" instead of notified."""
    primed_sources: set[str] = set()
    handled = []
    calls = {"n": 0}

    async def _has_any(source):
        return source in primed_sources

    async def _mark_seen(source, key):
        if key == "__primed__":
            primed_sources.add(source)

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    async def _poll(member_sub):
        calls["n"] += 1
        if calls["n"] == 1:
            return []
        return [
            Signal(
                source="fake", key="k1", occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
                member_sub=None, summary="summary k1", payload={},
            )
        ]

    from eve_ambient.sources import Source

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (Source("fake", False, "finances", _poll),))

    first = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert handled == []
    assert first.get("primed", 0) == 0
    assert "fake" in primed_sources

    second = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert [s.key for s in handled] == ["k1"]
    assert second.get("sent") == 1
    assert "primed" not in second


async def test_an_unprimed_source_whose_poll_fails_the_way_real_sources_fail_stays_unprimed(
    monkeypatch,
):
    """(fix round 4, item 2) `test_a_source_where_every_members_poll_fails_stays_unprimed`
    above injects a synthetic `RuntimeError`, which no real source can
    produce - eve-tools reports an upstream failure as an `error:` string,
    not a raised exception, and every source used to turn that into `[]`
    (`sources/mail.py`, `sources/calendar.py`, `sources/finances.py` in two
    places), which made this exact regression invisible to that test. This
    drives a real source (`mail.poll`) through a real `error:` string from
    `invoke`, so it actually exercises the fix: the source raises
    `SourceUnavailable` instead of returning `[]`, `poll_once`'s existing
    per-member isolation counts it as an error, and the source stays
    unprimed rather than being primed against a poll that never actually
    ran."""
    from unittest.mock import AsyncMock

    from eve_ambient.sources import Source, mail

    marked = []

    async def _has_any(source):
        return False

    async def _mark_seen(source, key):
        marked.append((source, key))

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(mail, "invoke", AsyncMock(return_value="error: gmail unavailable"))
    monkeypatch.setattr(app_module, "SOURCES", (Source("mail", True, "mail.read", mail.poll),))
    monkeypatch.setattr(app_module, "_audience_for", lambda source: ["sub-noah"])

    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert marked == []
    assert counts.get("primed", 0) == 0
    assert counts.get("errors") == 1


async def test_priming_against_an_empty_poll_logs_at_warning(monkeypatch, caplog):
    """(fix round 4, item 2, unattended-operation note) An empty first poll
    still primes, but the one line recording that decision must survive at
    WARNING - the level an unattended deployment's default log configuration
    is far less likely to drop than INFO - so it is still there on Friday."""
    async def _has_any(source):
        return False

    async def _mark_seen(source, key):
        return None

    async def _poll(member_sub):
        return []

    from eve_ambient.sources import Source

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(app_module, "SOURCES", (Source("fake", False, "finances", _poll),))

    with caplog.at_level("WARNING"):
        counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))

    assert counts.get("primed", 0) == 0
    line = next(
        r.getMessage() for r in caplog.records
        if r.levelname == "WARNING" and "primed fake with 0" in r.getMessage()
    )
    assert line


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


async def test_a_failing_members_poll_does_not_discard_a_sibling_members_signals(
    monkeypatch,
):
    """An expired token for one member must not throw away the signals
    already collected for everyone else polled under the same source."""
    handled = []

    async def _has_any(source):
        return True

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    async def _poll(member_sub):
        if member_sub == "sub-a":
            raise RuntimeError("expired token")
        return [
            Signal(
                source="fake", key=f"{member_sub}:k1",
                occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
                member_sub=member_sub, summary="summary", payload={},
            )
        ]

    from eve_ambient.sources import Source

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (Source("fake", True, "mail.read", _poll),))
    monkeypatch.setattr(app_module, "_audience_for", lambda source: ["sub-a", "sub-b"])

    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert [s.key for s in handled] == ["sub-b:k1"]
    assert counts["errors"] == 1
    assert counts["sent"] == 1


async def test_a_source_where_every_members_poll_fails_stays_unprimed(monkeypatch):
    """A source's credential can be broken on its very first tick. That must
    not get it marked primed having seen nothing at all - the old single
    `try` around the whole per-member loop used to make this impossible by
    aborting the source before priming was ever reached, but the per-member
    guard (round 1) makes `signals == []` indistinguishable from "every
    member's poll failed" unless priming checks for that explicitly. If it
    didn't, fixing the credential later would surface the entire genuine
    backlog as live notifications all at once - exactly what priming exists
    to prevent."""
    primed_sources: set[str] = set()
    marked = []
    handled = []

    async def _has_any(source):
        return source in primed_sources

    async def _mark_seen(source, key):
        marked.append((source, key))
        if key == "__primed__":
            primed_sources.add(source)

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    tick = {"n": 1}

    async def _poll(member_sub):
        if tick["n"] == 1:
            raise RuntimeError("credential expired")
        return [
            Signal(
                source="fake", key=f"{member_sub}:k1",
                occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
                member_sub=member_sub, summary="summary", payload={},
            )
        ]

    from eve_ambient.sources import Source

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (Source("fake", True, "mail.read", _poll),))
    monkeypatch.setattr(app_module, "_audience_for", lambda source: ["sub-a", "sub-b"])

    first = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert marked == []
    assert "fake" not in primed_sources
    assert first.get("errors") == 2
    assert first.get("primed", 0) == 0
    assert handled == []

    # The credential is fixed; the next tick succeeds for every member.
    tick["n"] = 2
    second = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert handled == []
    assert "fake" in primed_sources
    assert second.get("primed", 0) == 2
    assert {key for _, key in marked} == {"sub-a:k1", "sub-b:k1", "__primed__"}


async def test_a_partial_member_failure_on_an_unprimed_source_does_not_prime_either(
    monkeypatch,
):
    """Not just total failure: if one of two members failed, that member's
    backlog has not actually been seen, so priming now would lose it just
    the same. Leave the source unprimed; the next tick is soon."""
    primed_sources: set[str] = set()
    marked = []
    handled = []

    async def _has_any(source):
        return source in primed_sources

    async def _mark_seen(source, key):
        marked.append((source, key))
        if key == "__primed__":
            primed_sources.add(source)

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    async def _poll(member_sub):
        if member_sub == "sub-a":
            raise RuntimeError("expired token")
        return [
            Signal(
                source="fake", key=f"{member_sub}:k1",
                occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
                member_sub=member_sub, summary="summary", payload={},
            )
        ]

    from eve_ambient.sources import Source

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (Source("fake", True, "mail.read", _poll),))
    monkeypatch.setattr(app_module, "_audience_for", lambda source: ["sub-a", "sub-b"])

    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert marked == []
    assert "fake" not in primed_sources
    assert counts.get("errors") == 1
    assert counts.get("primed", 0) == 0
    assert handled == []


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


def test_ambient_disabled_starts_no_polling_task(monkeypatch):
    """The headline claim for `EVE_AMBIENT_ENABLED=false`: not just "no
    signals sent" but no polling task exists at all. `TestClient` must be
    entered as a context manager for `lifespan` to run at all."""
    calls = {"n": 0}

    async def _poll_forever():
        calls["n"] += 1

    monkeypatch.setattr(app_module, "_poll_forever", _poll_forever)
    with TestClient(app_module.app):
        pass
    assert calls["n"] == 0


def test_ambient_enabled_starts_polling_and_cancels_it_cleanly_at_shutdown(monkeypatch):
    monkeypatch.setenv("EVE_AMBIENT_ENABLED", "true")
    monkeypatch.setenv("EVE_AMBIENT_TOKEN", "a" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()

    started = {"n": 0}
    cancelled = {"n": 0}

    async def _poll_forever():
        started["n"] += 1
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled["n"] += 1
            raise

    monkeypatch.setattr(app_module, "_poll_forever", _poll_forever)
    with TestClient(app_module.app):
        pass
    assert started["n"] == 1
    assert cancelled["n"] == 1


async def test_the_poll_loop_survives_a_raising_poll_once(monkeypatch):
    """The headline claim of this service: a tick that fails outright does
    not stop the next one."""
    calls = {"n": 0}

    async def _poll_once(now=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return {}

    async def _prune_seen():
        return 0

    sleeps = {"n": 0}

    async def _fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _StopLoop()

    monkeypatch.setattr(app_module, "poll_once", _poll_once)
    monkeypatch.setattr(app_module.store, "prune_seen", _prune_seen)
    monkeypatch.setattr(app_module.asyncio, "sleep", _fake_sleep)

    with pytest.raises(_StopLoop):
        await app_module._poll_forever()

    assert calls["n"] == 2


async def test_the_poll_loop_survives_a_raising_prune_seen(monkeypatch):
    async def _poll_once(now=None):
        return {}

    prune_calls = {"n": 0}

    async def _prune_seen():
        prune_calls["n"] += 1
        if prune_calls["n"] == 1:
            raise RuntimeError("prune boom")
        return 0

    sleeps = {"n": 0}

    async def _fake_sleep(_seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _StopLoop()

    monkeypatch.setattr(app_module, "poll_once", _poll_once)
    monkeypatch.setattr(app_module.store, "prune_seen", _prune_seen)
    monkeypatch.setattr(app_module.asyncio, "sleep", _fake_sleep)

    with pytest.raises(_StopLoop):
        await app_module._poll_forever()

    assert prune_calls["n"] == 2


async def test_lifespan_drains_in_flight_background_tasks_at_shutdown(monkeypatch):
    """A compose turn already running when the process stops gets a chance
    to finish rather than being destroyed mid-way (design 6.4)."""
    finished = {"n": 0}

    async def _slow_handle(signal, **kwargs):
        await asyncio.sleep(0.01)
        finished["n"] += 1
        return "sent"

    monkeypatch.setattr(app_module, "handle_signal", _slow_handle)

    async with app_module.lifespan(app_module.app):
        signal = Signal(
            source="home", key="k", occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
            member_sub=None, summary="s", payload={},
        )
        task = asyncio.create_task(app_module._handle_in_background(signal))
        app_module._background.add(task)
        task.add_done_callback(app_module._background.discard)

    assert finished["n"] == 1


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
