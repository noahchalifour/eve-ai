"""Statement-level tests: the store's job is to emit the right SQL with the
right parameters, and a real Postgres for that belongs in the integration
tier (tests/test_memory_integration.py's shape), not here."""

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
