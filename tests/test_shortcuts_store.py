"""eve_shortcut SQL against real Postgres (ENG-296)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

OFF = {"domain": "light", "service": "turn_off", "entity_id": "light.living_room"}
ON = {**OFF, "service": "turn_on"}


@pytest.fixture(autouse=True)
async def pool(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve")
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_shortcut, eve_shortcut_observation")
    yield p
    await db.close_pool()
    get_settings.cache_clear()


async def _observe(args=OFF, sub="sub-noah", thread="t-1", phrasing="lights off"):
    from eve.shortcuts import store

    return await store.observe(sub, "home", "call_service", args, phrasing, thread)


async def test_promotes_on_the_third_observation_not_before():
    assert await _observe() is None
    assert await _observe() is None
    row = await _observe()
    assert row["name"] == "living-room-light"
    assert row["fixed_args"] == {"domain": "light", "entity_id": "light.living_room"}
    assert row["variants"] == ["turn_off"]
    assert row["permission"] == "home.control"


async def test_on_and_off_collapse_into_one_shortcut_with_both_options():
    from eve.shortcuts import store

    for _ in range(3):
        await _observe()
    row = await _observe(ON, phrasing="lights on")
    assert sorted(row["variants"]) == ["turn_off", "turn_on"]
    assert len(await store.list_all("sub-noah")) == 1


async def test_a_contradicted_observation_does_not_count():
    from eve.shortcuts import store

    await _observe()
    await _observe()
    assert await store.contradict_latest("sub-noah", "t-1") == 1
    assert await _observe() is None
    assert await _observe() is not None


async def test_shortcuts_are_per_member():
    from eve.shortcuts import store

    for _ in range(3):
        await _observe()
    assert await store.active_for("sub-kid", 10, 90) == []
    assert await store.active_by_name("sub-kid", "living-room-light") is None
    assert len(await store.active_for("sub-noah", 10, 90)) == 1


async def test_failures_retire_and_hits_reset_the_counter():
    from eve.shortcuts import store

    for _ in range(3):
        row = await _observe()
    assert await store.record_failure(row["id"], 3) is False
    await store.record_hit(row["id"])
    assert (await store.get(row["id"]))["consecutive_failures"] == 0
    assert (await store.get(row["id"]))["hits"] == 1
    for expected in (False, False, True):
        assert await store.record_failure(row["id"], 3) is expected
    assert await store.active_by_name("sub-noah", row["name"]) is None
    # It can be re-learned once retired.
    assert (await _observe())["status"] == "active"


async def test_a_revoked_shortcut_is_never_relearned():
    from eve.shortcuts import store

    for _ in range(3):
        row = await _observe()
    assert await store.revoke(row["id"])
    assert await _observe() is None
    assert await store.active_for("sub-noah", 10, 90) == []


async def test_name_collisions_get_a_suffix():
    from eve.shortcuts import store

    for _ in range(3):
        await _observe()
    async with (await __import__("eve.memory.db", fromlist=["get_pool"]).get_pool()).connection() as conn:
        await conn.execute("UPDATE eve_shortcut SET name = 'kitchen-light'")
    other = {"domain": "light", "service": "turn_off", "entity_id": "light.kitchen"}
    for _ in range(3):
        row = await _observe(other)
    assert row["name"] == "kitchen-light-2"
    names = {r["name"] for r in await store.list_all("sub-noah")}
    assert names == {"kitchen-light", "kitchen-light-2"}
