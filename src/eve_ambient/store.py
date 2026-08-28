"""Every eve_ambient_seen and eve_ambient_notice SQL statement.

Separate from `eve.memory.store` because it is a different subsystem with a
different lifetime, but it deliberately shares `eve.memory.db`'s pool and
migration list: one Postgres, one migration entrypoint, one place a schema
failure can stop a pod.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool
from eve_ambient.types import FilterVerdict, Signal


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
    """The default of 30 days is deliberately equal to
    `sources.finances.BUDGET_COOLDOWN_HOURS` (720 hours) - see the comment
    there for why moving one without the other makes every budget overrun
    re-fire.

    The `__primed__` sentinel (`app._PRIMED_SENTINEL`) is excluded on
    purpose (fix round 4, item 8): without this, a source that produces
    nothing for 30 days has its priming row deleted right alongside
    everything else, `has_any` goes back to reporting false, and the next
    real signal that source produces gets silently primed away instead of
    notified - exactly the failure priming exists to prevent, just delayed a
    month.
    """
    row = await _fetchone(
        """
        WITH gone AS (
          DELETE FROM eve_ambient_seen
          WHERE last_seen_at < now() - make_interval(days => %(days)s)
            AND key <> '__primed__'
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


async def already_notified(
    member_sub: str, source: str, key: str, cooldown_hours: int
) -> bool:
    """True when a notice row already exists for this member and signal
    *within the current cooldown window*.

    Makes a retry after a partial defer idempotent per member: a signal that
    reached two of three members before `deliver` raised must not re-deliver,
    re-push and re-spend the daily cap for the two who already have it on the
    next poll (fix round 1, item 1).

    Bounded by the same window `is_fresh` uses, not open-ended (fix round 2,
    item 1): `sources/home.py` and `sources/finances.py` both put state in
    the key — open -> closed -> open, or a budget crossing back under and
    over — so the *same* `(source, key)` legitimately recurs once its
    cooldown has passed. An unbounded lookup would find that first notice
    row forever and drop every recurrence permanently — worse once
    `prune_seen` expires the `eve_ambient_seen` row while this row survives
    and keeps suppressing. A member is owed at most one notice per cooldown
    window for a given signal; `is_fresh` already guarantees a genuine
    recurrence is only reachable after that window elapses, so the same
    window bounds both checks.

    One consequence accepted deliberately: a defer that outlives the
    cooldown (Aegra down for seven hours against a six-hour cooldown) makes
    the retry notify the member again rather than treating them as already
    done. That is the right trade at that distance in time, and — unlike
    the bug this replaces — it is bounded."""
    row = await _fetchone(
        """
        SELECT 1 AS found FROM eve_ambient_notice
        WHERE member_sub = %(sub)s AND source = %(source)s AND key = %(key)s
          AND sent_at >= now() - make_interval(hours => %(hours)s)
        LIMIT 1
        """,
        {"sub": member_sub, "source": source, "key": key, "hours": cooldown_hours},
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


async def record_decision(signal: Signal, verdict: FilterVerdict) -> None:
    """One row per judged signal, for Phase 5b's dataset.

    The verdict, NOT the eventual outcome: a signal the filter approved and
    the daily cap then suppressed is still a notify=true decision. Scoring
    the outcome would measure the gate chain instead of the filter
    (eval design 4.2).
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_ambient_decision (source, key, signal, verdict)"
            " VALUES (%s, %s, %s, %s)",
            (
                signal.source,
                signal.key,
                Jsonb(
                    {
                        "source": signal.source,
                        "key": signal.key,
                        "occurred_at": signal.occurred_at.isoformat(),
                        "member_sub": signal.member_sub,
                        "summary": signal.summary,
                        "payload": signal.payload,
                        "cooldown_hours": signal.cooldown_hours,
                    }
                ),
                Jsonb(verdict.model_dump()),
            ),
        )


async def decisions_since(since: datetime, limit: int) -> list[dict]:
    """Newest first, joined to whether the notification earned a reply.

    LEFT JOIN, not INNER: a notify=false decision has no notice row, and
    dropping those would leave the dataset with only the positives.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT d.id, d.source, d.key, d.signal, d.verdict, d.decided_at,
                       bool_or(n.replied_at IS NOT NULL) AS replied,
                       count(n.id) AS notices
                  FROM eve_ambient_decision d
                  LEFT JOIN eve_ambient_notice n
                    ON n.source = d.source AND n.key = d.key
                 WHERE d.decided_at >= %(since)s
                 GROUP BY d.id
                 ORDER BY d.decided_at DESC
                 LIMIT %(limit)s
                """,
                {"since": since, "limit": limit},
            )
            return list(await cur.fetchall())


async def prune_decisions(days: int) -> int:
    """Retention. One row per judged signal at a five-minute poll across four
    sources grows without bound, and a year-old signal is not measuring the
    current filter anyway."""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM eve_ambient_decision"
            f" WHERE decided_at < now() - interval '{int(days)} days'"
        )
        return cur.rowcount


async def mark_replied(thread_id: str) -> None:
    """A member speaking in an ambient thread IS the label (eval design 5).

    No lookup first: a thread with no matching row is not an ambient thread,
    and the UPDATE affects nothing.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_ambient_notice SET replied_at = now()"
            " WHERE thread_id = %s AND replied_at IS NULL",
            (thread_id,),
        )
