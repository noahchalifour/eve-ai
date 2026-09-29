"""tests/test_dashboards_store.py: the SQL, against a real Postgres."""
import pytest

pytestmark = pytest.mark.integration

LAYOUT = [{"resourceId": "w-1", "sizes": ["2x2"], "x": 0, "y": 0, "w": 2, "h": 2}]


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve")
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_dashboard")
    yield p
    await db.close_pool()


async def test_replace_creates_then_replaces_keeping_the_revision_moving(pool):
    from eve.dashboards import store

    first = await store.replace("sub-noah", "device-1", "Kitchen", 4, LAYOUT)
    second = await store.replace("sub-noah", "device-1", "Office", 8, [])

    assert first["revision"] == 1
    assert second["revision"] == 2 and second["purpose"] == "Office" and second["layout"] == []
    assert second["id"] == first["id"]


async def test_one_dashboard_per_member_per_device(pool):
    from eve.dashboards import store

    await store.replace("sub-noah", "device-1", "Kitchen", 4, LAYOUT)
    await store.replace("sub-noah", "device-2", "Office", 4, [])

    assert (await store.get("sub-noah", "device-1"))["purpose"] == "Kitchen"
    assert (await store.get("sub-noah", "device-2"))["purpose"] == "Office"


async def test_a_foreign_dashboard_is_invisible(pool):
    from eve.dashboards import store

    await store.replace("sub-noah", "device-1", "Kitchen", 4, LAYOUT)

    assert await store.get("sub-kendra", "device-1") is None
    assert await store.update_layout("sub-kendra", "device-1", [], 1) is None
    assert await store.delete("sub-kendra", "device-1") is False


async def test_update_layout_is_revision_guarded(pool):
    from eve.dashboards import store

    await store.replace("sub-noah", "device-1", "Kitchen", 4, LAYOUT)
    updated = await store.update_layout("sub-noah", "device-1", [], 1)

    assert updated["revision"] == 2 and updated["layout"] == []
    assert await store.update_layout("sub-noah", "device-1", LAYOUT, 1) is None


async def test_delete_removes_the_dashboard(pool):
    from eve.dashboards import store

    await store.replace("sub-noah", "device-1", "Kitchen", 4, LAYOUT)
    assert await store.delete("sub-noah", "device-1") is True
    assert await store.get("sub-noah", "device-1") is None
