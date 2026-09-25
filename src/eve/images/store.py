"""Every SQL statement on `eve_image`. Same discipline as `eve/routines/store.py`:
one module owns the table, and every statement filters by member_sub, so a
foreign row and a missing row are indistinguishable to every caller (and to
the phone, which gets 404 for both)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from psycopg.rows import dict_row

from eve.images.process import Normalised
from eve.memory.db import get_pool
from eve.settings import get_settings

_FULL = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHORT = re.compile(r"^[0-9a-f]{8}$")


@dataclass(frozen=True)
class ImageRow:
    id: str
    member_sub: str
    thread_id: str | None
    origin: str
    source_ref: str | None
    content_type: str
    bytes: bytes
    width: int
    height: int
    caption: str | None
    created_at: datetime
    expires_at: datetime


def short_id(image_id: str) -> str:
    """What a model cites: `[image 3f2a9c01]`. Eight hex characters is 4
    billion values per member-thread - a collision is refused by `resolve`,
    never guessed."""
    return image_id[:8]


def is_full_id(value: object) -> bool:
    return isinstance(value, str) and bool(_FULL.match(value))


def _row(record: dict) -> ImageRow:
    return ImageRow(**{**record, "id": str(record["id"]), "bytes": bytes(record["bytes"])})


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def put(
    member_sub: str,
    image: Normalised,
    *,
    origin: str,
    thread_id: str | None = None,
    source_ref: str | None = None,
    now: datetime | None = None,
) -> ImageRow:
    settings = get_settings()
    days = settings.image_cache_days if origin == "immich" else settings.image_retention_days
    created = _now(now)
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # ON CONFLICT refreshes an Immich copy in place, so its id - which
            # may already sit in a persisted surface - stays valid. Uploads
            # never conflict: source_ref is NULL and NULLs are distinct.
            await cur.execute(
                "INSERT INTO eve_image (member_sub, thread_id, origin, source_ref,"
                " content_type, bytes, width, height, created_at, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (member_sub, source_ref) DO UPDATE SET"
                " thread_id = EXCLUDED.thread_id, content_type = EXCLUDED.content_type,"
                " bytes = EXCLUDED.bytes, width = EXCLUDED.width,"
                " height = EXCLUDED.height, caption = NULL,"
                " created_at = EXCLUDED.created_at, expires_at = EXCLUDED.expires_at"
                " RETURNING *",
                (
                    member_sub, thread_id, origin, source_ref, image.content_type,
                    image.data, image.width, image.height, created,
                    created + timedelta(days=days),
                ),
            )
            return _row(await cur.fetchone())


async def get(
    image_id: str,
    member_sub: str,
    *,
    include_expired: bool = False,
    now: datetime | None = None,
) -> ImageRow | None:
    """None for missing, foreign, malformed, or (by default) expired.
    `include_expired` exists for one caller - the GET route - which must tell
    410 from 404."""
    if not is_full_id(image_id):
        return None
    sql = "SELECT * FROM eve_image WHERE id = %s AND member_sub = %s"
    params: list = [image_id, member_sub]
    if not include_expired:
        sql += " AND expires_at > %s"
        params.append(_now(now))
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            record = await cur.fetchone()
            return _row(record) if record else None


async def resolve(
    ref: str, member_sub: str, thread_id: str | None, *, now: datetime | None = None
) -> ImageRow | None:
    """A full or short id, as a model or specialist wrote it, back to a live
    row this member owns in this thread. The single provenance check for every
    consumer (spec 3.2): a model cannot place an id it was never shown."""
    ref = (ref or "").strip().lower()
    if is_full_id(ref):
        clause, value = "id = %s", ref
    elif _SHORT.match(ref):
        clause, value = "id::text LIKE %s", ref + "%"
    else:
        return None
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT * FROM eve_image WHERE {clause} AND member_sub = %s"
                " AND thread_id IS NOT DISTINCT FROM %s AND expires_at > %s LIMIT 2",
                (value, member_sub, thread_id, _now(now)),
            )
            records = await cur.fetchall()
    return _row(records[0]) if len(records) == 1 else None


async def set_caption(image_id: str, member_sub: str, caption: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_image SET caption = %s WHERE id = %s AND member_sub = %s",
            (caption, image_id, member_sub),
        )


async def sweep(now: datetime | None = None) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM eve_image WHERE expires_at <= %s", (_now(now),)
        )
        return cur.rowcount
