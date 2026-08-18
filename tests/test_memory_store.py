"""Integration tests against the real Postgres in docker-compose.test.yml.

The compose file already runs the VectorChord image the cluster runs, so the
vector path is exercised on the same engine as production.
"""

import pytest

from eve.memory import db

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_memory")
    yield p
    await db.close_pool()


async def test_migrate_creates_the_table(pool):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT to_regclass('public.eve_memory')")
        assert (await cur.fetchone())[0] == "eve_memory"


async def test_migrate_is_idempotent(pool):
    """It runs on every pod start. If a second run is not a no-op, a rolling
    restart is an outage."""
    await db.migrate()
    await db.migrate()
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_schema_version")
        assert (await cur.fetchone())[0] == len(db.MIGRATIONS)


async def test_the_vector_column_accepts_a_1536_dim_vector(pool):
    vec = "[" + ",".join(["0.01"] * 1536) + "]"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, "
            "content, embedding) VALUES "
            "('episodic','member','sub-noah','event','x', %s::vector)",
            (vec,),
        )
        cur = await conn.execute("SELECT count(*) FROM eve_memory")
        assert (await cur.fetchone())[0] == 1


async def test_superseded_rows_are_excluded_by_the_partial_index(pool):
    """Not an index test - a correctness test. Every read path relies on
    `superseded_why IS NULL`, and this is the one place it is asserted
    directly rather than through a query helper."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('profile','member','sub-noah','fact','old') RETURNING id"
        )
        old = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('profile','member','sub-noah','fact','new') RETURNING id"
        )
        new = (await cur.fetchone())[0]
        await conn.execute(
            "UPDATE eve_memory SET superseded_by=%s, superseded_why='contradicted'"
            " WHERE id=%s",
            (new, old),
        )
        cur = await conn.execute(
            "SELECT content FROM eve_memory WHERE superseded_why IS NULL"
        )
        assert [r[0] for r in await cur.fetchall()] == ["new"]
