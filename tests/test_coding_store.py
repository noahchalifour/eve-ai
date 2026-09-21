"""Statement-level tests: the store's job is to emit the right SQL with the
right parameters, and a real Postgres for that belongs in the integration
tier (tests/test_memory_integration.py's shape), not here."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from eve.coding import store


@pytest.fixture
def conn(monkeypatch):
    connection = MagicMock()
    connection.execute = AsyncMock()
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    connection.cursor.return_value.__aenter__ = AsyncMock(return_value=cursor)
    connection.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=connection)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(store, "get_pool", AsyncMock(return_value=pool))
    connection.cursor_obj = cursor
    return connection


async def test_create_session_inserts_every_column_the_supervisor_needs(conn):
    await store.create_session(
        session_id="s1", member_sub="sub-noah", thread_id="t1", goal="fix it",
        agent="codex", model="chatgpt/gpt-5.6-sol", repos=["acme/repo"],
        context="Noah prefers httpx.",
    )

    sql, params = conn.execute.await_args.args
    assert "INSERT INTO eve_coding_session" in sql
    assert "sub-noah" in params and "codex" in params
    assert "Noah prefers httpx." in params


async def test_live_sessions_asks_for_running_and_idle(conn):
    await store.live_sessions()

    sql = conn.cursor_obj.execute.await_args.args[0]
    # `blocked` is in the live set too: the supervisor wakes a blocked
    # session the moment its member interjects (see
    # test_coding_supervisor.py::test_a_blocked_session_wakes_when_a_member_interjects).
    assert "status IN ('running', 'idle', 'blocked')" in sql


async def test_live_sessions_for_a_member_is_scoped_to_that_member(conn):
    await store.live_sessions_for("sub-noah")

    sql, params = conn.cursor_obj.execute.await_args.args
    assert "member_sub = %s" in sql
    assert params == ("sub-noah",)


async def test_advance_cursor_records_the_bookmark(conn):
    await store.advance_cursor("s1", 7)

    sql, params = conn.execute.await_args.args
    assert "cursor = %s" in sql
    assert params == (7, "s1")


async def test_bump_supervisor_turns_returns_the_new_count(conn):
    conn.cursor_obj.fetchone = AsyncMock(return_value={"supervisor_turns": 4})

    assert await store.bump_supervisor_turns("s1") == 4

    sql = conn.cursor_obj.execute.await_args.args[0]
    assert "supervisor_turns = supervisor_turns + 1" in sql
    assert "RETURNING" in sql


async def test_mark_resolved_stamps_finished_at(conn):
    await store.mark_resolved("s1", "finished", {"prs": []})

    sql, params = conn.execute.await_args.args
    assert "finished_at = now()" in sql
    assert params[0] == "finished"


async def test_recently_resolved_covers_every_terminal_status(conn):
    from datetime import UTC, datetime

    await store.recently_resolved_sessions(since=datetime.now(UTC))

    sql = conn.cursor_obj.execute.await_args.args[0]
    for status in ("finished", "failed", "stale", "blocked"):
        assert status in sql


# Integration tier: the six tests below exercise a real unique constraint and
# a real row round-trip, which a mocked connection cannot observe honestly.
# See tests/test_computer_store.py for the same real-DB pattern.


@pytest.fixture
async def db(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:25432/eve"
    )
    from eve.memory import db as memory_db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await memory_db.close_pool()
    await memory_db.migrate()
    pool = await memory_db.get_pool()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_coding_session")
    yield
    await memory_db.close_pool()


@pytest.mark.integration
async def test_a_linear_session_id_is_stored_and_looked_up(db):
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id,
        member_sub="noah-sub",
        thread_id="thread-1",
        goal="fix the thing",
        agent="dsh",
        model="claude-sonnet-5",
        repos=["owner/repo"],
        context="",
        linear_session_id="lin_sess_1",
        linear_issue_id="lin_issue_1",
    )
    row = await store.get_by_linear_session("lin_sess_1")
    assert row["id"] == session_id
    assert row["linear_issue_id"] == "lin_issue_1"


@pytest.mark.integration
async def test_a_chat_dispatched_session_has_no_linear_columns(db):
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id,
        member_sub="noah-sub",
        thread_id="thread-1",
        goal="fix the thing",
        agent="dsh",
        model="claude-sonnet-5",
        repos=["owner/repo"],
        context="",
    )
    row = await store.get(session_id)
    assert row["linear_session_id"] is None
    assert row["linear_issue_id"] is None


@pytest.mark.integration
async def test_two_sessions_cannot_share_one_linear_session_id(db):
    # This constraint IS the retry idempotency key. Linear retries on 5xx and
    # on timeout, and a retry that dispatches twice spends real money and
    # opens two pull requests for one request.
    await store.create_session(
        session_id=str(uuid.uuid4()),
        member_sub="noah-sub",
        thread_id="t",
        goal="g",
        agent="dsh",
        model="m",
        repos=["owner/repo"],
        context="",
        linear_session_id="lin_dupe",
    )
    with pytest.raises(Exception):
        await store.create_session(
            session_id=str(uuid.uuid4()),
            member_sub="noah-sub",
            thread_id="t",
            goal="g",
            agent="dsh",
            model="m",
            repos=["owner/repo"],
            context="",
            linear_session_id="lin_dupe",
        )


@pytest.mark.integration
async def test_many_sessions_may_have_no_linear_session_id(db):
    # A unique constraint over NULLs must not make chat dispatch single-use.
    for _ in range(3):
        await store.create_session(
            session_id=str(uuid.uuid4()),
            member_sub="noah-sub",
            thread_id="t",
            goal="g",
            agent="dsh",
            model="m",
            repos=["owner/repo"],
            context="",
        )


@pytest.mark.integration
async def test_get_by_linear_session_returns_none_when_unknown(db):
    assert await store.get_by_linear_session("lin_never") is None


@pytest.mark.integration
async def test_touch_linear_emitted_advances_the_heartbeat_clock(db):
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id,
        member_sub="noah-sub",
        thread_id="t",
        goal="g",
        agent="dsh",
        model="m",
        repos=["owner/repo"],
        context="",
        linear_session_id="lin_touch",
    )
    before = (await store.get(session_id))["linear_emitted_at"]
    await store.touch_linear_emitted(session_id)
    after = (await store.get(session_id))["linear_emitted_at"]
    assert before is None
    assert after is not None
