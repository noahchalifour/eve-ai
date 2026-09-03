"""Every eve_coding_session SQL statement. Eve's own record of a delegated
coding session - not the box's live session, which the box holds in memory
and loses on restart.

`status` here is Eve's vocabulary, not the box's, and the two deliberately
differ. The box has `idle`; Eve has `blocked`, which the box could never
produce because deciding that an agent's question is unanswerable needs the
member and the household. Terminal for Eve: finished, failed, stale,
blocked.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool


async def create_session(
    session_id: str,
    member_sub: str,
    thread_id: str,
    goal: str,
    agent: str,
    model: str,
    repos: list[str],
    context: str,
) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_coding_session"
            " (id, member_sub, thread_id, goal, agent, model, repos, context, status)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running')",
            (session_id, member_sub, thread_id, goal, agent, model, Jsonb(repos), context),
        )


async def get(session_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM eve_coding_session WHERE id = %s", (session_id,))
            return await cur.fetchone()


async def live_sessions() -> list[dict]:
    """Every session the supervisor is still driving."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_coding_session"
                " WHERE status IN ('running', 'idle', 'blocked')"
                " ORDER BY created_at"
            )
            return list(await cur.fetchall())


async def live_sessions_for(member_sub: str) -> list[dict]:
    """What `check_coding_session` lists. Scoped to the asking member: one
    member has no business seeing another's delegated work in a tool result."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_coding_session"
                " WHERE status IN ('running', 'idle', 'blocked') AND member_sub = %s"
                " ORDER BY created_at",
                (member_sub,),
            )
            return list(await cur.fetchall())


async def set_status(session_id: str, status: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_coding_session SET status = %s, updated_at = now() WHERE id = %s",
            (status, session_id),
        )


async def advance_cursor(session_id: str, cursor: int) -> None:
    """Eve's bookmark into the box's turn log. Advanced only after a turn has
    actually been reasoned over, so a crash mid-decision re-reads rather than
    skips."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_coding_session SET cursor = %s, updated_at = now() WHERE id = %s",
            (cursor, session_id),
        )


async def bump_supervisor_turns(session_id: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "UPDATE eve_coding_session"
                " SET supervisor_turns = supervisor_turns + 1, updated_at = now()"
                " WHERE id = %s RETURNING supervisor_turns",
                (session_id,),
            )
            row = await cur.fetchone()
            return row["supervisor_turns"] if row else 0


async def mark_resolved(session_id: str, status: str, result: dict) -> None:
    """`status` is one of finished, failed, stale, blocked."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_coding_session SET status = %s, result = %s,"
            " updated_at = now(), finished_at = now() WHERE id = %s",
            (status, Jsonb(result), session_id),
        )


async def recently_resolved_sessions(since: datetime) -> list[dict]:
    """Every session that resolved since `since` - not only those that
    resolved on this exact tick. Lets the ambient source re-derive a signal
    whose delivery was suppressed (quiet hours, daily cap) or deferred, the
    same way every other polled source re-derives from live upstream state."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_coding_session"
                " WHERE status IN ('finished', 'failed', 'stale', 'blocked')"
                "   AND finished_at >= %s"
                " ORDER BY finished_at",
                (since,),
            )
            return list(await cur.fetchall())
