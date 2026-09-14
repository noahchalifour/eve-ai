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


from datetime import datetime, timedelta, timezone


async def test_append_and_query_round_trip(pool):
    from eve.records import store

    now = datetime.now(timezone.utc)
    await store.append("sub-noah", "anything.entry", {"n": 1}, occurred_at=now)

    rows = await store.query("sub-noah", "anything.entry")
    assert len(rows) == 1
    assert rows[0]["payload"] == {"n": 1}


async def test_two_unrelated_collections_share_one_table(pool):
    """Genericity: nothing about either name is known to the code."""
    from eve.records import store

    await store.append("sub-noah", "alpha.thing", {"a": 1})
    await store.append("sub-noah", "beta.other", {"b": 2})

    assert len(await store.query("sub-noah", "alpha.thing")) == 1
    assert len(await store.query("sub-noah", "beta.other")) == 1
    assert set(await store.collections("sub-noah")) == {"alpha.thing", "beta.other"}


async def test_queries_are_scoped_to_the_member(pool):
    from eve.records import store

    await store.append("sub-noah", "alpha.thing", {"a": 1})
    await store.append("sub-kendra", "alpha.thing", {"a": 2})

    rows = await store.query("sub-noah", "alpha.thing")
    assert [r["payload"] for r in rows] == [{"a": 1}]


async def test_a_repeated_key_is_deduped_rather_than_duplicated(pool):
    from eve.records import store

    first = await store.append("sub-noah", "alpha.thing", {"a": 1}, key="k1")
    second = await store.append("sub-noah", "alpha.thing", {"a": 999}, key="k1")

    assert first["deduped"] is False
    assert second["deduped"] is True
    rows = await store.query("sub-noah", "alpha.thing")
    assert len(rows) == 1
    # The first write wins: an idempotent retry must not silently rewrite.
    assert rows[0]["payload"] == {"a": 1}


async def test_query_filters_by_window(pool):
    from eve.records import store

    now = datetime.now(timezone.utc)
    await store.append("sub-noah", "alpha.thing", {"old": 1},
                       occurred_at=now - timedelta(days=30))
    await store.append("sub-noah", "alpha.thing", {"new": 1}, occurred_at=now)

    rows = await store.query("sub-noah", "alpha.thing", since=now - timedelta(days=7))
    assert len(rows) == 1
    assert rows[0]["payload"] == {"new": 1}


async def test_known_fields_reports_what_this_collection_has_seen(pool):
    from eve.records import store

    await store.append("sub-noah", "alpha.thing", {"weight": 100, "reps": 5})
    await store.append("sub-noah", "alpha.thing", {"weight": 105, "note": "ok"})

    assert set(await store.known_fields("sub-noah", "alpha.thing")) == {
        "weight", "reps", "note"
    }


async def test_known_fields_is_empty_for_an_unseen_collection(pool):
    from eve.records import store

    assert await store.known_fields("sub-noah", "never.used") == []