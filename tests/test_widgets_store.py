"""tests/test_widgets_store.py"""
import pytest

pytestmark = pytest.mark.integration


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
        await conn.execute("TRUNCATE eve_widget_resource")
    yield p
    await db.close_pool()


RECIPE = {"sources": [{"type": "records", "collection": "alpha.thing"}]}


async def test_create_and_get_round_trip(pool):
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {"days": 30})
    fetched = await store.get("sub-noah", created["id"])

    assert fetched["kind"] == "chart"
    assert fetched["title"] == "Alpha"
    assert fetched["recipe"] == RECIPE
    assert fetched["revision"] == 1


async def test_a_foreign_resource_is_invisible(pool):
    """A resource id is a locator, never authorization."""
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {})

    assert await store.get("sub-kendra", created["id"]) is None
    assert await store.list_for("sub-kendra") == []


async def test_updating_filters_bumps_the_revision(pool):
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {"days": 30})
    updated = await store.update_filters(
        "sub-noah", created["id"], {"days": 7}, expected_revision=1
    )

    assert updated["filters"] == {"days": 7}
    assert updated["revision"] == 2


async def test_a_stale_revision_is_refused(pool):
    """Two devices editing filters must not silently clobber each other."""
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {"days": 30})
    await store.update_filters("sub-noah", created["id"], {"days": 7}, expected_revision=1)

    stale = await store.update_filters(
        "sub-noah", created["id"], {"days": 90}, expected_revision=1
    )

    assert stale is None
    current = await store.get("sub-noah", created["id"])
    assert current["filters"] == {"days": 7}


async def test_a_foreign_member_cannot_update_or_delete(pool):
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {})

    assert await store.update_filters(
        "sub-kendra", created["id"], {"days": 1}, expected_revision=1
    ) is None
    assert await store.delete("sub-kendra", created["id"]) is False
    assert await store.get("sub-noah", created["id"]) is not None


async def test_delete_removes_only_the_named_resource(pool):
    from eve.widgets import store

    first = await store.create("sub-noah", "chart", "A", RECIPE, {})
    await store.create("sub-noah", "chart", "B", RECIPE, {})

    assert await store.delete("sub-noah", first["id"]) is True
    assert [r["title"] for r in await store.list_for("sub-noah")] == ["B"]