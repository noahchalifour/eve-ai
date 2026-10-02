"""Every eve_shortcut and eve_shortcut_observation statement.

Member-scoped throughout: every member-facing statement carries
`member_sub`. The CLI's household-wide reads (`list_all`, `get`, `revoke`)
are the one deliberate exception, the same shape `eve-skill` has, because
the operator is reviewing everyone's.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool
from eve.settings import get_settings
from eve.shortcuts import allowlist
from eve.shortcuts.capture import fingerprint

MAX_PHRASINGS = 5
# A correction only retracts an observation this recent: "no" an hour later
# is about something else.
CORRECTION_WINDOW_MINUTES = 10

_COLUMNS = (
    "id, member_sub, fingerprint, name, specialist, tool, fixed_args, variant_key,"
    " variants, phrasings, permission, hits, consecutive_failures, status,"
    " last_used_at, created_at, updated_at"
)


def _row(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {**row, "id": str(row["id"])}


async def observe(member_sub: str, specialist: str, tool: str, args: dict, phrasing: str,
                  thread_id: str | None) -> dict | None:
    """Record one eligible run, then promote if it crossed the threshold.
    Returns the shortcut row when this observation created or widened one."""
    action = allowlist.lookup(specialist, tool)
    if action is None:
        return None
    digest, variant, fixed = fingerprint(specialist, tool, args)
    settings = get_settings()
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO eve_shortcut_observation"
                " (member_sub, fingerprint, specialist, tool, variant, args, phrasing, thread_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (member_sub, digest, specialist, tool, variant, Jsonb(args), phrasing, thread_id),
            )
            await cur.execute(
                "SELECT variant, phrasing FROM eve_shortcut_observation"
                " WHERE member_sub = %s AND fingerprint = %s AND NOT contradicted"
                "   AND observed_at >= now() - make_interval(days => %s)"
                " ORDER BY observed_at DESC",
                (member_sub, digest, settings.shortcut_window_days),
            )
            seen = await cur.fetchall()
            if len(seen) < settings.shortcut_promote_after:
                return None
            variants = sorted({row["variant"] for row in seen if row["variant"] is not None})
            phrasings = list(dict.fromkeys(row["phrasing"] for row in seen if row["phrasing"]))[:MAX_PHRASINGS]

            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_shortcut WHERE member_sub = %s AND fingerprint = %s",
                (member_sub, digest),
            )
            existing = await cur.fetchone()
            if existing is not None and existing["status"] == "revoked":
                # An operator said no. Nothing re-learns it.
                return None
            if existing is not None:
                merged = sorted(set(existing["variants"] or []) | set(variants))
                # A retired row coming back may find its name taken by a
                # newer shortcut; the partial unique index is over active rows.
                name = existing["name"]
                if existing["status"] != "active":
                    name = await _free_name(cur, member_sub, name)
                await cur.execute(
                    "UPDATE eve_shortcut SET name = %s, variants = %s, phrasings = %s,"
                    " status = 'active',"
                    " consecutive_failures = CASE WHEN status = 'retired' THEN 0 ELSE consecutive_failures END,"
                    " updated_at = now()"
                    f" WHERE id = %s RETURNING {_COLUMNS}",
                    (name, Jsonb(merged), Jsonb(phrasings), existing["id"]),
                )
                return _row(await cur.fetchone())

            name = await _free_name(cur, member_sub, action.name(args))
            await cur.execute(
                "INSERT INTO eve_shortcut"
                " (member_sub, fingerprint, name, specialist, tool, fixed_args, variant_key,"
                "  variants, phrasings, permission)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                f" RETURNING {_COLUMNS}",
                (member_sub, digest, name, specialist, tool, Jsonb(fixed), action.variant_key,
                 Jsonb(variants), Jsonb(phrasings), action.permission),
            )
            return _row(await cur.fetchone())


async def _free_name(cur, member_sub: str, base: str) -> str:
    await cur.execute(
        "SELECT name FROM eve_shortcut WHERE member_sub = %s AND status = 'active'"
        " AND (name = %s OR name LIKE %s)",
        (member_sub, base, f"{base}-%"),
    )
    taken = {row["name"] for row in await cur.fetchall()}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


async def contradict_latest(member_sub: str, thread_id: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            UPDATE eve_shortcut_observation SET contradicted = true
             WHERE id = (
               SELECT id FROM eve_shortcut_observation
                WHERE member_sub = %s AND thread_id = %s AND NOT contradicted
                  AND observed_at >= now() - make_interval(mins => %s)
                ORDER BY observed_at DESC LIMIT 1
             )
            """,
            (member_sub, thread_id, CORRECTION_WINDOW_MINUTES),
        )
        return cur.rowcount


async def active_for(member_sub: str, limit: int, idle_days: int) -> list[dict]:
    """What goes in the prompt: live shortcuts used (or made) recently,
    most-used first."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_shortcut"
                " WHERE member_sub = %s AND status = 'active'"
                "   AND COALESCE(last_used_at, created_at) >= now() - make_interval(days => %s)"
                " ORDER BY hits DESC, COALESCE(last_used_at, created_at) DESC"
                " LIMIT %s",
                (member_sub, idle_days, limit),
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def active_by_name(member_sub: str, name: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_shortcut"
                " WHERE member_sub = %s AND name = %s AND status = 'active'",
                (member_sub, name),
            )
            return _row(await cur.fetchone())


async def record_hit(shortcut_id: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_shortcut SET hits = hits + 1, consecutive_failures = 0,"
            " last_used_at = now(), updated_at = now() WHERE id = %s",
            (shortcut_id,),
        )


async def record_failure(shortcut_id: str, limit: int) -> bool:
    """True when this failure retired the shortcut. Increment and retire in
    one statement, so a row is never observed at the limit and still active."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                UPDATE eve_shortcut
                   SET consecutive_failures = consecutive_failures + 1,
                       status = CASE WHEN consecutive_failures + 1 >= %(limit)s
                                     THEN 'retired' ELSE status END,
                       updated_at = now()
                 WHERE id = %(id)s
                RETURNING status
                """,
                {"id": shortcut_id, "limit": limit},
            )
            row = await cur.fetchone()
            return bool(row) and row["status"] == "retired"


async def list_all(member_sub: str | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_shortcut"
                " WHERE (%s::text IS NULL OR member_sub = %s)"
                " ORDER BY status, member_sub, hits DESC",
                (member_sub, member_sub),
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def get(shortcut_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {_COLUMNS} FROM eve_shortcut WHERE id = %s", (shortcut_id,))
            return _row(await cur.fetchone())


async def revoke(shortcut_id: str) -> bool:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE eve_shortcut SET status = 'revoked', updated_at = now() WHERE id = %s",
            (shortcut_id,),
        )
        return cur.rowcount > 0


def last_used(row: dict) -> datetime | None:
    return row.get("last_used_at") or row.get("created_at")
