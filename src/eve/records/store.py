"""Every eve_record SQL statement. Same discipline as `eve/computer/store.py`
and `eve/wardrobe/store.py`: one module owns the table.

Nothing here knows what a collection means. `collection` is an opaque string
supplied at runtime, and `payload` is opaque jsonb. That is the whole design:
a new domain is a new string, never a schema change.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool

MAX_LIMIT = 2000


async def append(
    member_sub: str,
    collection: str,
    payload: dict,
    occurred_at: datetime | None = None,
    key: str | None = None,
) -> dict:
    """Insert one entry. With a `key`, a repeat is recognised and the FIRST
    write is kept.

    First-write-wins rather than upsert: `key` exists for retry idempotency,
    and a retry carrying different values is a client bug, not an edit. An
    edit is a new entry, so the history stays append-only and a chart cannot
    change shape retroactively.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO eve_record"
                " (member_sub, collection, key, payload, occurred_at)"
                " VALUES (%s, %s, %s, %s, COALESCE(%s, now()))"
                " ON CONFLICT (member_sub, collection, key)"
                "   WHERE key IS NOT NULL DO NOTHING"
                " RETURNING id",
                (member_sub, collection, key, Jsonb(payload), occurred_at),
            )
            inserted = await cur.fetchone()
            if inserted is not None:
                return {"id": str(inserted["id"]), "deduped": False}

            # DO NOTHING returned no row, so the key already existed.
            await cur.execute(
                "SELECT id FROM eve_record"
                " WHERE member_sub = %s AND collection = %s AND key = %s",
                (member_sub, collection, key),
            )
            existing = await cur.fetchone()
            return {"id": str(existing["id"]), "deduped": True}


async def query(
    member_sub: str,
    collection: str,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 500,
) -> list[dict]:
    """This member's entries in one collection, newest first."""
    pool = await get_pool()
    bounded = max(1, min(limit, MAX_LIMIT))
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, collection, key, payload, occurred_at"
                " FROM eve_record"
                " WHERE member_sub = %s AND collection = %s"
                "   AND (%s::timestamptz IS NULL OR occurred_at >= %s)"
                "   AND (%s::timestamptz IS NULL OR occurred_at <= %s)"
                " ORDER BY occurred_at DESC"
                " LIMIT %s",
                (member_sub, collection, since, since, until, until, bounded),
            )
            return [dict(row) for row in await cur.fetchall()]


async def known_fields(member_sub: str, collection: str) -> list[str]:
    """Payload keys this collection has actually used.

    Advisory, not a schema. It is returned to the model on append so later
    entries match earlier ones, which is what makes a chart over the
    collection possible without ever declaring one.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT jsonb_object_keys(payload) FROM eve_record"
                " WHERE member_sub = %s AND collection = %s",
                (member_sub, collection),
            )
            return sorted(row[0] for row in await cur.fetchall())


async def collections(member_sub: str) -> list[str]:
    """Every collection this member has recorded into."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT collection FROM eve_record WHERE member_sub = %s"
                " ORDER BY collection",
                (member_sub,),
            )
            return [row[0] for row in await cur.fetchall()]