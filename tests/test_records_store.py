"""tests/test_records_store.py"""
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
        await conn.execute("TRUNCATE eve_record")
    yield p
    await db.close_pool()


async def test_the_record_table_exists_after_migration(pool):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'eve_record'"
        )
        columns = {row[0] for row in await cur.fetchall()}

    assert {
        "id",
        "member_sub",
        "collection",
        "key",
        "payload",
        "occurred_at",
        "created_at",
    } <= columns


async def test_no_domain_specific_table_was_created(pool):
    """The whole point: a domain costs a collection name, never a table."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = 'public'"
        )
        tables = {row[0] for row in await cur.fetchall()}

    assert not [t for t in tables if "workout" in t or "exercise" in t]