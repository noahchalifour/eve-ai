"""eve-tools' own pool, against the real Postgres. ADR 0016: eve-tools holds
one table under its own role, so this proves the pool opens and the table
Alembic created is the shape oauth_store expects - not that Alembic works.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

TEST_DSN = "postgresql://eve:eve@127.0.0.1:15432/eve"


@pytest.fixture
async def migrated(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", TEST_DSN)
    monkeypatch.setenv("EVE_TOOLS_DATABASE_URL", TEST_DSN)
    from eve.settings import get_settings
    from eve_tools.settings import get_tools_settings

    get_settings.cache_clear()
    get_tools_settings.cache_clear()

    from eve.memory import db as eve_db
    from eve_tools import db as tools_db

    await eve_db.close_pool()
    await eve_db.migrate()
    await tools_db.close_pool()
    yield
    await tools_db.close_pool()
    await eve_db.close_pool()


async def test_the_pool_opens_from_the_tools_settings(migrated):
    from eve_tools import db

    pool = await db.get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 1")
        assert await cur.fetchone() == (1,)


async def test_the_oauth_token_table_has_the_columns_the_store_needs(migrated):
    from eve_tools import db

    pool = await db.get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'eve_oauth_token'"
        )
        columns = {name: nullable for name, nullable in await cur.fetchall()}
    assert columns == {
        "provider": "NO",
        "member_sub": "NO",
        "access_token": "NO",
        # Nullable on purpose: a non-rotating credential (an Oura PAT) is a
        # normal row, not a special case. Spec 3.1.
        "refresh_token": "YES",
        "expires_at": "YES",
        "updated_at": "NO",
    }


async def test_the_primary_key_is_provider_plus_member(migrated):
    """Two members with the same provider must coexist, and one member must
    not get two rows for one provider - the store's upsert relies on it."""
    from eve_tools import db

    pool = await db.get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT a.attname FROM pg_index i"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid"
            "   AND a.attnum = ANY(i.indkey)"
            " WHERE i.indrelid = 'eve_oauth_token'::regclass AND i.indisprimary"
        )
        assert {row[0] for row in await cur.fetchall()} == {"provider", "member_sub"}


async def test_get_pool_without_a_url_says_which_variable_is_missing(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_DATABASE_URL", "")
    from eve_tools.settings import get_tools_settings

    get_tools_settings.cache_clear()
    from eve_tools import db

    await db.close_pool()
    with pytest.raises(RuntimeError, match="EVE_TOOLS_DATABASE_URL"):
        await db.get_pool()
