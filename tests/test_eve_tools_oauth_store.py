"""The token store. Provider-agnostic on purpose: a third wearable should be
a new client plus a row, not a change to this protocol.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

TEST_DSN = "postgresql://eve:eve@127.0.0.1:15432/eve"


@pytest.fixture
async def store(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", TEST_DSN)
    monkeypatch.setenv("EVE_TOOLS_DATABASE_URL", TEST_DSN)
    from eve.settings import get_settings
    from eve_tools.settings import get_tools_settings

    get_settings.cache_clear()
    get_tools_settings.cache_clear()

    from eve.memory import db as eve_db
    from eve_tools import db as tools_db, oauth_store

    await eve_db.close_pool()
    await eve_db.migrate()
    await tools_db.close_pool()
    pool = await tools_db.get_pool()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_oauth_token")
    yield oauth_store
    await tools_db.close_pool()
    await eve_db.close_pool()


async def test_a_missing_row_reads_as_none(store):
    assert await store.get_row("whoop", "sub-noah") is None


async def test_save_then_read_round_trips_every_field(store):
    expires = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    await store.save("whoop", "sub-noah", "acc-1", "ref-1", expires)
    row = await store.get_row("whoop", "sub-noah")
    assert row["access_token"] == "acc-1"
    assert row["refresh_token"] == "ref-1"
    assert row["expires_at"] == expires


async def test_save_twice_updates_rather_than_duplicating(store):
    """The upsert is what makes rotation safe to repeat. A second INSERT
    would violate the primary key and take the whole request down."""
    await store.save("whoop", "sub-noah", "acc-1", "ref-1", None)
    await store.save("whoop", "sub-noah", "acc-2", "ref-2", None)
    row = await store.get_row("whoop", "sub-noah")
    assert (row["access_token"], row["refresh_token"]) == ("acc-2", "ref-2")


async def test_a_non_rotating_credential_is_an_ordinary_row(store):
    """An Oura PAT has no refresh token and no expiry. Spec 1.1 - this must
    not need a special case."""
    await store.save("oura", "sub-noah", "pat-1", None, None)
    row = await store.get_row("oura", "sub-noah")
    assert row["refresh_token"] is None
    assert row["expires_at"] is None


async def test_two_members_hold_the_same_provider_independently(store):
    await store.save("whoop", "sub-noah", "acc-noah", None, None)
    await store.save("whoop", "sub-kendra", "acc-kendra", None, None)
    noah = await store.get_row("whoop", "sub-noah")
    kendra = await store.get_row("whoop", "sub-kendra")
    assert noah["access_token"] == "acc-noah"
    assert kendra["access_token"] == "acc-kendra"


async def test_configured_providers_lists_only_the_members_own_rows(store):
    await store.save("whoop", "sub-noah", "a", None, None)
    await store.save("oura", "sub-kendra", "b", None, None)
    assert await store.configured_providers("sub-noah") == ["whoop"]
    assert await store.configured_providers("sub-kendra") == ["oura"]
    assert await store.configured_providers("sub-nobody") == []


async def test_configured_providers_is_sorted(store):
    """health.py's `unconfigured` list is compared in tests; an unstable
    order there would make those tests flap."""
    await store.save("whoop", "sub-noah", "a", None, None)
    await store.save("oura", "sub-noah", "b", None, None)
    assert await store.configured_providers("sub-noah") == ["oura", "whoop"]


async def test_updated_at_moves_on_every_save(store):
    await store.save("whoop", "sub-noah", "acc-1", None, None)
    first = (await store.get_row("whoop", "sub-noah"))["updated_at"]
    await store.save("whoop", "sub-noah", "acc-2", None, None)
    second = (await store.get_row("whoop", "sub-noah"))["updated_at"]
    assert second > first
    assert second > datetime.now(UTC) - timedelta(minutes=5)
