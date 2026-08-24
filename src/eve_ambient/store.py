"""Every eve_ambient_seen and eve_ambient_notice SQL statement.

Separate from `eve.memory.store` because it is a different subsystem with a
different lifetime, but it deliberately shares `eve.memory.db`'s pool and
migration list: one Postgres, one migration entrypoint, one place a schema
failure can stop a pod.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from eve.memory.db import get_pool


async def _fetchone(sql: str, params: dict) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        # Cursor-scoped row factory, not connection-scoped: see the comment
        # in eve.memory.db.migrate() for why the difference matters to a
        # pooled connection.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()


async def _execute(sql: str, params: dict) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(sql, params)


async def is_fresh(source: str, key: str, cooldown_hours: int) -> bool:
    """True when this signal has never been seen, or was last seen longer ago
    than its cooldown window."""
    row = await _fetchone(
        """
        SELECT last_seen_at < now() - make_interval(hours => %(hours)s)
                 AS expired
        FROM eve_ambient_seen
        WHERE source = %(source)s AND key = %(key)s
        """,
        {"source": source, "key": key, "hours": cooldown_hours},
    )
    return True if row is None else bool(row["expired"])


async def mark_seen(source: str, key: str) -> None:
    """Called only once a signal has been *resolved* — dropped by a gate,
    vetoed by Eve, or delivered. Marking on receipt would lose a signal to
    any crash in between (design section 4.5)."""
    await _execute(
        """
        INSERT INTO eve_ambient_seen (source, key) VALUES (%(source)s, %(key)s)
        ON CONFLICT (source, key) DO UPDATE SET last_seen_at = now()
        """,
        {"source": source, "key": key},
    )


async def prune_seen(days: int = 30) -> int:
    row = await _fetchone(
        """
        WITH gone AS (
          DELETE FROM eve_ambient_seen
          WHERE last_seen_at < now() - make_interval(days => %(days)s)
          RETURNING 1
        )
        SELECT count(*) AS n FROM gone
        """,
        {"days": days},
    )
    return int(row["n"]) if row else 0


async def record_notice(
    member_sub: str, source: str, key: str, urgent: bool, thread_id: str | None
) -> None:
    await _execute(
        """
        INSERT INTO eve_ambient_notice (member_sub, source, key, urgent, thread_id)
        VALUES (%(sub)s, %(source)s, %(key)s, %(urgent)s, %(thread)s)
        """,
        {
            "sub": member_sub,
            "source": source,
            "key": key,
            "urgent": urgent,
            "thread": thread_id,
        },
    )


async def has_any(source: str) -> bool:
    """Whether this source has ever produced a signal. False means the next
    poll is a first poll, which primes rather than notifies (app.py)."""
    row = await _fetchone(
        "SELECT 1 AS found FROM eve_ambient_seen WHERE source = %(source)s LIMIT 1",
        {"source": source},
    )
    return row is not None


async def notices_since(member_sub: str, since: datetime) -> int:
    row = await _fetchone(
        """
        SELECT count(*) AS n FROM eve_ambient_notice
        WHERE member_sub = %(sub)s AND sent_at >= %(since)s
        """,
        {"sub": member_sub, "since": since},
    )
    return int(row["n"]) if row else 0
