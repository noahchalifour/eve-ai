"""Ambient's impersonation path against a live `aegra serve`.

Requires `docker compose -f docker-compose.test.yml up -d`. The roster is
tests/fixtures/family.yaml, which the aegra_server fixture points the server
at, so `sub-noah` and `sub-kid` are the two identities here.
"""

from __future__ import annotations

import pytest
from langgraph_sdk import get_client
from langgraph_sdk.errors import APIStatusError

from eve.memory import db
from eve_ambient import store

pytestmark = pytest.mark.integration

AMBIENT_TOKEN = "ambient-integration-token-0123456789abcdef"


def _ambient_client(url, on_behalf_of):
    return get_client(
        url=url,
        headers={
            "Authorization": f"Bearer {AMBIENT_TOKEN}",
            "x-eve-on-behalf-of": on_behalf_of,
        },
    )


def _member_client(url, token):
    return get_client(url=url, headers={"Authorization": f"Bearer {token}"})


async def test_the_ambient_token_creates_a_thread_owned_by_the_member(aegra_server):
    thread = await _ambient_client(aegra_server, "sub-noah").threads.create(
        metadata={"ambient": True, "source": "home", "signal_key": "k1"}
    )
    assert thread["metadata"]["owner"] == "sub-noah"
    assert thread["metadata"]["ambient"] is True


async def test_the_member_can_read_the_thread_ambient_created_for_them(aegra_server):
    """This is the whole point of impersonating rather than pushing only: the
    member opens Eve and the proactive message is there, in a thread they own
    and can reply in."""
    thread = await _ambient_client(aegra_server, "sub-noah").threads.create()
    fetched = await _member_client(aegra_server, "tok-noah").threads.get(
        thread["thread_id"]
    )
    assert fetched["thread_id"] == thread["thread_id"]


async def test_another_member_cannot_read_it(aegra_server):
    thread = await _ambient_client(aegra_server, "sub-noah").threads.create()
    with pytest.raises(APIStatusError) as exc_info:
        await _member_client(aegra_server, "tok-kid").threads.get(thread["thread_id"])
    assert exc_info.value.status_code == 404


async def test_a_wrong_ambient_token_is_rejected(aegra_server):
    client = get_client(
        url=aegra_server,
        headers={
            "Authorization": "Bearer ambient-wrong-token-0123456789abcdefgh",
            "x-eve-on-behalf-of": "sub-noah",
        },
    )
    with pytest.raises(APIStatusError) as exc_info:
        await client.threads.create()
    assert exc_info.value.status_code == 401


async def test_a_member_token_with_the_header_still_authenticates_as_itself(aegra_server):
    """Belt and braces on the unit test in Task 9: at the HTTP boundary, the
    header must be inert without the ambient token."""
    client = get_client(
        url=aegra_server,
        headers={"Authorization": "Bearer tok-kid", "x-eve-on-behalf-of": "sub-noah"},
    )
    thread = await client.threads.create()
    assert thread["metadata"]["owner"] == "sub-kid"


async def test_the_ambient_thread_can_be_deleted_by_ambient(aegra_server):
    """The veto path deletes the thread it just created; it must be allowed
    to."""
    client = _ambient_client(aegra_server, "sub-noah")
    thread = await client.threads.create()
    await client.threads.delete(thread["thread_id"])
    with pytest.raises(APIStatusError):
        await client.threads.get(thread["thread_id"])


async def test_the_ambient_tables_exist_after_migration(monkeypatch):
    # The brief's Step 1 text omits this line, but every sibling integration
    # test that touches `db` directly (test_ambient_store.py,
    # test_memory_integration.py) sets it before clearing the settings cache -
    # without it db.get_pool() raises "DATABASE_URL is unset" regardless of
    # anything this task touches.
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.settings import get_settings

    get_settings.cache_clear()
    db._pool = None
    pool = await db.get_pool()
    await db.migrate()
    assert await store.has_any("nothing-here") is False
    async with pool.connection() as conn:
        result = await conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('eve_ambient_seen', 'eve_ambient_notice')"
        )
        assert (await result.fetchone())[0] == 2
    db._pool = None
