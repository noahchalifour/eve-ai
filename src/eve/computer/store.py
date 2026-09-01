"""Every eve_computer_task SQL statement. Eve's own record of a dispatched
computer task - not the box's internal queue, which the box tracks itself
in memory and loses on restart (design doc: "Storage")."""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool


async def create_task(task_id: str, member_sub: str, thread_id: str, goal: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_computer_task (id, member_sub, thread_id, goal, status)"
            " VALUES (%s, %s, %s, %s, 'running')",
            (task_id, member_sub, thread_id, goal),
        )


async def get(task_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_computer_task WHERE id = %s", (task_id,)
            )
            return await cur.fetchone()


async def running_tasks() -> list[dict]:
    """Every task Eve is still waiting on. The poller (Task 5) asks the box
    about each of these once per tick."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_computer_task WHERE status = 'running'"
                " ORDER BY created_at"
            )
            return list(await cur.fetchall())


async def mark_finished(task_id: str, status: str, result: dict) -> None:
    """`status` is `'finished'` or `'failed'` - the poller decides which by
    inspecting the box's own result payload."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_computer_task SET status = %s, result = %s,"
            " updated_at = now(), finished_at = now() WHERE id = %s",
            (status, Jsonb(result), task_id),
        )


async def recently_resolved_tasks(since: datetime) -> list[dict]:
    """Every task that resolved (finished/failed/stale) since `since` -
    not just ones that resolved on this exact poll tick. Lets the ambient
    source re-derive a signal for a task whose delivery was suppressed
    (quiet hours, daily cap) or failed (deferred), the same way every
    other polled source re-derives from live upstream state each tick."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_computer_task"
                " WHERE status IN ('finished', 'failed', 'stale')"
                "   AND finished_at >= %s"
                " ORDER BY finished_at",
                (since,),
            )
            return list(await cur.fetchall())


async def mark_stale(task_id: str) -> None:
    """The box stopped answering for this task past its own timeout -
    likely a restart mid-run (design doc: "Reporting back")."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_computer_task SET status = 'stale',"
            " updated_at = now(), finished_at = now() WHERE id = %s",
            (task_id,),
        )
