"""tests/test_wardrobe_store.py"""
import pytest
from psycopg.errors import NotNullViolation

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
        await conn.execute("TRUNCATE eve_wardrobe_item")
    yield p
    await db.close_pool()


async def test_inserting_two_garments_from_one_asset(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah",
        "asset-1",
        [
            {"name": "white oxford shirt", "category": "top", "attrs": {"warmth": 2}},
            {"name": "blue oxford shirt", "category": "top", "attrs": {"warmth": 2}},
        ],
    )

    items = await store.list_items("sub-noah")
    assert [i["name"] for i in items] == ["blue oxford shirt", "white oxford shirt"]
    assert {i["item_index"] for i in items} == {0, 1}
    assert items[0]["attrs"] == {"warmth": 2}
    assert await store.count_items("sub-noah") == 2


async def test_catalogued_asset_ids_are_scoped_to_the_member(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah", "asset-1", [{"name": "blazer", "category": "outerwear", "attrs": {}}]
    )
    await store.insert_items(
        "sub-kendra", "asset-2", [{"name": "coat", "category": "outerwear", "attrs": {}}]
    )

    assert await store.catalogued_asset_ids("sub-noah") == {"asset-1"}
    assert await store.catalogued_asset_ids("sub-kendra") == {"asset-2"}


async def test_deleting_assets_removes_every_garment_from_them(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah",
        "asset-1",
        [
            {"name": "shirt a", "category": "top", "attrs": {}},
            {"name": "shirt b", "category": "top", "attrs": {}},
        ],
    )
    await store.insert_items(
        "sub-noah", "asset-2", [{"name": "boots", "category": "footwear", "attrs": {}}]
    )

    await store.delete_assets("sub-noah", ["asset-1"])

    assert [i["name"] for i in await store.list_items("sub-noah")] == ["boots"]


async def test_deleting_nothing_is_not_an_error(pool):
    from eve.wardrobe import store

    await store.delete_assets("sub-noah", [])
    assert await store.count_items("sub-noah") == 0


async def test_reinserting_the_same_asset_replaces_its_rows(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah", "asset-1", [{"name": "old name", "category": "top", "attrs": {}}]
    )
    await store.insert_items(
        "sub-noah", "asset-1", [{"name": "new name", "category": "top", "attrs": {}}]
    )

    assert [i["name"] for i in await store.list_items("sub-noah")] == ["new name"]


async def test_failed_replacement_preserves_the_asset_previous_rows(pool):
    from eve.wardrobe import store

    await store.insert_items(
        "sub-noah", "asset-1", [{"name": "old name", "category": "top", "attrs": {}}]
    )

    with pytest.raises(NotNullViolation):
        await store.insert_items(
            "sub-noah",
            "asset-1",
            [
                {"name": "new name", "category": "top", "attrs": {}},
                {"name": None, "category": "top", "attrs": {}},
            ],
        )

    assert [i["name"] for i in await store.list_items("sub-noah")] == ["old name"]
