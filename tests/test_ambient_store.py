"""Integration tests against the real Postgres in docker-compose.test.yml."""

from datetime import UTC, datetime, timedelta

import pytest

from eve.memory import db
from eve_ambient import store

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    from eve.settings import get_settings

    get_settings.cache_clear()
    db._pool = None
    pool = await db.get_pool()
    await db.migrate()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_ambient_seen, eve_ambient_notice")
    yield pool
    db._pool = None


async def test_an_unseen_key_is_fresh(pool):
    assert await store.is_fresh("home", "door:open", 6) is True


async def test_a_seen_key_is_not_fresh_inside_the_window(pool):
    await store.mark_seen("home", "door:open")
    assert await store.is_fresh("home", "door:open", 6) is False


async def test_a_seen_key_is_fresh_again_past_the_window(pool):
    """A door that was open six hours ago and is open again is news again."""
    await store.mark_seen("home", "door:open")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_ambient_seen SET first_seen_at = now() - interval '7 hours'"
        )
    assert await store.is_fresh("home", "door:open", 6) is True


async def test_marking_seen_twice_refreshes_rather_than_erroring(pool):
    await store.mark_seen("home", "door:open")
    await store.mark_seen("home", "door:open")
    assert await store.is_fresh("home", "door:open", 6) is False


async def test_the_same_key_in_two_sources_is_independent(pool):
    await store.mark_seen("home", "shared-key")
    assert await store.is_fresh("mail", "shared-key", 6) is True


async def test_notices_are_counted_per_member_since_an_instant(pool):
    await store.record_notice("sub-noah", "home", "k1", False, "t1")
    await store.record_notice("sub-noah", "mail", "k2", False, "t2")
    await store.record_notice("sub-kendra", "home", "k3", False, "t3")
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await store.notices_since("sub-noah", since) == 2
    assert await store.notices_since("sub-kendra", since) == 1


async def test_notices_before_the_instant_are_not_counted(pool):
    """The daily cap is a window, not a lifetime total."""
    await store.record_notice("sub-noah", "home", "k1", False, "t1")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_ambient_notice SET sent_at = now() - interval '2 days'"
        )
    since = datetime.now(UTC) - timedelta(hours=24)
    assert await store.notices_since("sub-noah", since) == 0


async def test_pruning_removes_only_rows_past_the_horizon(pool):
    await store.mark_seen("home", "old")
    await store.mark_seen("home", "new")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_ambient_seen SET first_seen_at = now() - interval '40 days' "
            "WHERE key = 'old'"
        )
    assert await store.prune_seen(30) == 1
    assert await store.is_fresh("home", "new", 6) is False
