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


async def test_a_fresh_token_is_returned_without_refreshing(store):
    future = datetime.now(UTC) + timedelta(hours=1)
    await store.save("whoop", "sub-noah", "acc-1", "ref-1", future)
    calls = []

    async def refresh(token):
        calls.append(token)
        raise AssertionError("must not refresh a fresh token")

    assert await store.access_token("whoop", "sub-noah", refresh) == "acc-1"
    assert calls == []


async def test_a_null_expiry_never_refreshes(store):
    """An Oura PAT has no expiry. Treating NULL as "expired long ago" would
    refresh it on every single call - with no refresh token to do it with."""
    await store.save("oura", "sub-noah", "pat-1", None, None)

    async def refresh(token):
        raise AssertionError("must not refresh a non-expiring credential")

    assert await store.access_token("oura", "sub-noah", refresh) == "pat-1"


async def test_a_token_inside_the_skew_window_refreshes(store):
    """Expiring in 30s is expiring mid-request. SKEW_SECONDS is 120."""
    await store.save(
        "whoop", "sub-noah", "acc-1", "ref-1",
        datetime.now(UTC) + timedelta(seconds=30),
    )

    async def refresh(token):
        assert token == "ref-1"
        return {"access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600}

    assert await store.access_token("whoop", "sub-noah", refresh) == "acc-2"
    row = await store.get_row("whoop", "sub-noah")
    assert row["refresh_token"] == "ref-2", "the rotated token must be persisted"
    assert row["expires_at"] > datetime.now(UTC) + timedelta(minutes=50)


async def test_an_expired_token_refreshes(store):
    await store.save(
        "whoop", "sub-noah", "acc-1", "ref-1",
        datetime.now(UTC) - timedelta(hours=2),
    )

    async def refresh(token):
        return {"access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600}

    assert await store.access_token("whoop", "sub-noah", refresh) == "acc-2"


async def test_a_missing_row_raises_not_connected(store):
    async def refresh(token):
        raise AssertionError("nothing to refresh")

    with pytest.raises(store.NotConnected):
        await store.access_token("whoop", "sub-noah", refresh)


async def test_a_rejected_refresh_raises_reconnect_required(store):
    await store.save(
        "whoop", "sub-noah", "acc-1", "ref-1",
        datetime.now(UTC) - timedelta(hours=2),
    )

    async def refresh(token):
        raise RuntimeError("400 invalid_grant")

    with pytest.raises(store.ReconnectRequired, match="whoop"):
        await store.access_token("whoop", "sub-noah", refresh)


async def test_an_expired_token_with_no_refresh_token_raises_reconnect_required(store):
    """Expired and nothing to refresh with. Must not silently return the dead
    access token."""
    await store.save(
        "whoop", "sub-noah", "acc-1", None,
        datetime.now(UTC) - timedelta(hours=2),
    )

    async def refresh(token):
        raise AssertionError("there is no refresh token to use")

    with pytest.raises(store.ReconnectRequired):
        await store.access_token("whoop", "sub-noah", refresh)


async def test_refresh_now_refreshes_even_a_fresh_token(store):
    """The reactive path: the provider answered 401 on a token that has not
    reached its stated expiry, because it was revoked server-side."""
    await store.save(
        "whoop", "sub-noah", "acc-1", "ref-1",
        datetime.now(UTC) + timedelta(hours=1),
    )

    async def refresh(token):
        return {"access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600}

    assert await store.refresh_now("whoop", "sub-noah", refresh) == "acc-2"


async def test_two_concurrent_refreshes_rotate_the_token_exactly_once(store):
    """Concurrent rotation must preserve the one valid refresh token.

    A real Postgres is required: FOR UPDATE semantics are the thing under
    test, and a fake would assert nothing.
    """
    import asyncio

    await store.save(
        "whoop", "sub-noah", "acc-0", "ref-0",
        datetime.now(UTC) - timedelta(hours=2),
    )
    refreshes = []

    async def refresh(token):
        refreshes.append(token)
        # Hold the lock long enough that a naive implementation interleaves.
        await asyncio.sleep(0.3)
        n = len(refreshes)
        return {
            "access_token": f"acc-{n}",
            "refresh_token": f"ref-{n}",
            "expires_in": 3600,
        }

    results = await asyncio.gather(
        store.access_token("whoop", "sub-noah", refresh),
        store.access_token("whoop", "sub-noah", refresh),
    )

    assert len(refreshes) == 1, f"refreshed {len(refreshes)} times, must be 1"
    assert refreshes == ["ref-0"]
    # The second caller must return the token the first one stored, not a
    # stale read from before the lock.
    assert results == ["acc-1", "acc-1"]
    assert (await store.get_row("whoop", "sub-noah"))["refresh_token"] == "ref-1"
