"""Every eve_tool SQL statement."""

from __future__ import annotations

import hashlib

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool


def source_hash(source: str) -> str:
    """What an approval binds to. The sandbox recomputes this and refuses on a
    mismatch, so approved bytes cannot change underneath the approval."""
    return hashlib.sha256(source.encode()).hexdigest()


async def propose(
    *,
    name: str,
    description: str,
    args_schema: dict,
    source: str,
    proposed_by: str,
    thread_id: str | None,
    run_id: str | None,
) -> str:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO eve_tool
              (name, description, args_schema, source, source_sha256,
               proposed_by, source_thread, source_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                name, description, Jsonb(args_schema), source,
                source_hash(source), proposed_by, thread_id, run_id,
            ),
        )
        return str((await cur.fetchone())[0])


async def approve(tool_id: str, approver: str) -> bool:
    """Stamp approval. The partial unique index raises UniqueViolation if a
    live approved version of this name already exists - deliberately not
    caught here: the caller decides whether that is an error or a signal to
    revoke first."""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE eve_tool SET approved_by = %s, approved_at = now()"
            " WHERE id = %s AND approved_at IS NULL AND rejected_why IS NULL",
            (approver, tool_id),
        )
        return cur.rowcount == 1


async def reject(tool_id: str, why: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_tool SET rejected_why = %s"
            " WHERE id = %s AND approved_at IS NULL",
            (why, tool_id),
        )


async def revoke(name: str, why: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE eve_tool SET revoked_at = now(), revoked_why = %s"
            " WHERE name = %s AND approved_at IS NOT NULL AND revoked_at IS NULL",
            (why, name),
        )
        return cur.rowcount


async def revoke_all(why: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE eve_tool SET revoked_at = now(), revoked_why = %s"
            " WHERE approved_at IS NOT NULL AND revoked_at IS NULL",
            (why,),
        )
        return cur.rowcount


async def live_tools() -> list[dict]:
    """Approved and not revoked. Read on every search_skills call."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, name, description, args_schema, source,"
                " source_sha256, invocations, last_used_at FROM eve_tool"
                " WHERE approved_at IS NOT NULL AND revoked_at IS NULL"
                " ORDER BY name"
            )
            return list(await cur.fetchall())


async def by_id(tool_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM eve_tool WHERE id = %s", (tool_id,))
            return await cur.fetchone()


async def all_tools() -> list[dict]:
    """Everything, for `eve-tool list`: pending, approved, rejected, revoked."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM eve_tool ORDER BY proposed_at DESC")
            return list(await cur.fetchall())


async def record_invocation(tool_id: str) -> None:
    """A tool used once was a wasted approval; Eve should have just done the
    arithmetic. This is how that is visible (design section 11)."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_tool SET invocations = invocations + 1,"
            " last_used_at = now() WHERE id = %s",
            (tool_id,),
        )
