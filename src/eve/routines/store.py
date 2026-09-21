"""Every eve_routine SQL statement.

Every statement here carries `member_sub` in its WHERE clause, with exactly
one deliberate exception: `claim_due`, which the ambient tick runs
household-wide and which returns each row's own `member_sub` for the signal
to carry. That is the same shape `eve.computer.store.recently_resolved_tasks`
already has, and it is named here so it reads as a decision rather than an
oversight. `record_run` and `expire` are keyed by a routine id the caller
only ever obtains from `claim_due`, so they inherit that scoping.

A routine id is a high-entropy locator and nothing more: Aegra's `@auth.on`
handlers scope threads and the store API, but they do not reach custom
routes, so ownership is enforced in this module or not at all.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool

_COLUMNS = (
    "id, member_sub, title, instruction, cadence, timezone, status,"
    " next_run_at, last_run_at, last_outcome, consecutive_failures,"
    " expires_at, revision, created_at, updated_at"
)

# Only these may be written through `update`. A caller cannot reach
# consecutive_failures, revision, or member_sub by naming them.
_UPDATABLE = ("title", "instruction", "cadence", "status", "next_run_at", "expires_at")

# How long a claimed routine stays un-claimable while its firing runs. Bounds
# how long a firing lost to a crashed worker stays invisible, so it must
# comfortably exceed one compose turn while staying well under the shortest
# legal cadence (one hour). See `claim_due`.
CLAIM_LEASE_MINUTES = 15


def _row(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {**row, "id": str(row["id"])}


async def create(
    member_sub: str,
    title: str,
    instruction: str,
    cadence: dict,
    timezone: str,
    next_run_at: datetime,
    expires_at: datetime | None,
) -> dict:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO eve_routine"
                " (member_sub, title, instruction, cadence, timezone,"
                "  next_run_at, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                f" RETURNING {_COLUMNS}",
                (
                    member_sub,
                    title,
                    instruction,
                    Jsonb(cadence),
                    timezone,
                    next_run_at,
                    expires_at,
                ),
            )
            return _row(await cur.fetchone())


async def list_for(member_sub: str) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_routine"
                " WHERE member_sub = %s ORDER BY created_at DESC",
                (member_sub,),
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def get(member_sub: str, routine_id: str) -> dict | None:
    """None for both a missing id and another member's id: the caller turns
    both into the same 404, so probing cannot distinguish them."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_routine"
                " WHERE id = %s AND member_sub = %s",
                (routine_id, member_sub),
            )
            return _row(await cur.fetchone())


async def find_by_title(member_sub: str, text: str) -> list[dict]:
    """Substring, case-insensitive, because a member says "stop tracking
    flights" and not a uuid. The caller decides what an ambiguous match
    means."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_routine"
                " WHERE member_sub = %s AND title ILIKE %s"
                " ORDER BY created_at DESC",
                (member_sub, f"%{text}%"),
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def delete(member_sub: str, routine_id: str) -> bool:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM eve_routine WHERE id = %s AND member_sub = %s",
            (routine_id, member_sub),
        )
        return cur.rowcount > 0


async def update(
    member_sub: str, routine_id: str, expected_revision: int, **fields
) -> dict | None:
    """None when the row is absent, foreign, or at a different revision.

    The revision check is in the UPDATE's own WHERE clause rather than a
    read-then-write, so two concurrent writers cannot both pass it - the same
    guard `eve.widgets.store.update_filters` uses.
    """
    writable = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not writable:
        return await get(member_sub, routine_id)

    assignments = ", ".join(f"{name} = %s" for name in writable)
    values = [
        Jsonb(value) if name == "cadence" else value
        for name, value in writable.items()
    ]

    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"UPDATE eve_routine SET {assignments},"
                " revision = revision + 1, updated_at = now()"
                " WHERE id = %s AND member_sub = %s AND revision = %s"
                f" RETURNING {_COLUMNS}",
                (*values, routine_id, member_sub, expected_revision),
            )
            return _row(await cur.fetchone())


async def claim_due(now: datetime) -> list[dict]:
    """Every active routine that is due, claimed in the same statement that
    finds it.

    Household-wide, which is the one query in this module without a
    `member_sub` filter: the ambient tick polls once per tick for everyone
    (`per_member=False`) and each row carries its own member. The claim
    happens HERE, before any signal is emitted, so a compose turn still
    running when the next tick arrives cannot fire the same occurrence twice.

    The claim writes a LEASE, not `now`. Setting `next_run_at = now()` looks
    right and is wrong: `now()` is the transaction timestamp, the predicate is
    `next_run_at <= now()`, so the row is still due the instant the statement
    commits and the very next tick re-claims it. Verified against real
    Postgres while writing this plan: the naive form returns the same row on
    two consecutive claims. Pushing `next_run_at` a bounded interval into the
    future makes the claim idempotent for the length of the lease, and
    `eve_ambient.sources.routines.poll` then overwrites it with the real
    cadence time via `schedule_next`.

    The lease is also the crash-recovery story. A worker that dies between
    claiming and calling `schedule_next` leaves the row leased rather than
    lost, so the firing is retried once the lease expires instead of being
    silently dropped forever. `CLAIM_LEASE_MINUTES` is therefore an upper
    bound on how long a crashed firing stays invisible, and must comfortably
    exceed one compose turn.

    `scheduled_for` is the value of `next_run_at` this claim consumed, not
    the lease; it is what the signal key is built from.

    A routine whose `next_run_at` is far in the past (the service was down
    overnight) fires ONCE and schedules forward from now, never a backlog:
    six silent 8am checks delivered at once is the most annoying possible
    behaviour, and the member wanted a habit rather than an audit trail.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"""
                WITH due AS (
                  SELECT id, next_run_at FROM eve_routine
                   WHERE status = 'active' AND next_run_at <= %(now)s
                   ORDER BY next_run_at
                   FOR UPDATE SKIP LOCKED
                )
                UPDATE eve_routine AS r
                   SET next_run_at = %(now)s
                                     + make_interval(mins => %(lease)s),
                       updated_at = now()
                  FROM due
                 WHERE r.id = due.id
                RETURNING {', '.join('r.' + c.strip() for c in _COLUMNS.split(','))},
                          due.next_run_at AS scheduled_for
                """,
                {"now": now, "lease": CLAIM_LEASE_MINUTES},
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def schedule_next(routine_id: str, next_run_at: datetime) -> None:
    """Set the next firing time after a claim. Separate from `claim_due`
    because the cadence maths lives in `eve.routines.cadence` and the store
    holds no vocabulary."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_routine SET next_run_at = %s, updated_at = now()"
            " WHERE id = %s",
            (next_run_at, routine_id),
        )


async def record_run(routine_id: str, outcome: str, failure_limit: int) -> None:
    """`outcome` is `spoke`, `silent`, or `error`.

    Only `error` increments. A `silent` run is the routine working correctly
    - Eve looked and there was nothing worth saying - and counting it as a
    failure would auto-pause every well-behaved routine within a week.

    The pause is applied in the same statement as the increment, so a routine
    cannot be observed at the limit and still active.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        if outcome == "error":
            await conn.execute(
                """
                UPDATE eve_routine
                   SET consecutive_failures = consecutive_failures + 1,
                       status = CASE
                         WHEN consecutive_failures + 1 >= %(limit)s THEN 'paused'
                         ELSE status
                       END,
                       last_run_at = now(),
                       last_outcome = 'error',
                       updated_at = now()
                 WHERE id = %(id)s
                """,
                {"id": routine_id, "limit": failure_limit},
            )
            return
        await conn.execute(
            "UPDATE eve_routine"
            " SET consecutive_failures = 0, last_run_at = now(),"
            "     last_outcome = %s, updated_at = now()"
            " WHERE id = %s",
            (outcome, routine_id),
        )


async def expire(routine_id: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_routine SET status = 'expired', updated_at = now()"
            " WHERE id = %s",
            (routine_id,),
        )
