"""Every eve_widget_resource SQL statement.

Every statement in this module carries `member_sub` in its WHERE clause,
without exception. A resource id is a high-entropy locator and nothing more:
Aegra's `@auth.on` handlers scope threads and the store API, but they do not
reach custom routes, so ownership is enforced here or not at all.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool

_COLUMNS = "id, kind, title, recipe, filters, revision, updated_at"


def _row(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {**row, "id": str(row["id"])}


async def create(
    member_sub: str, kind: str, title: str, recipe: dict, filters: dict
) -> dict:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO eve_widget_resource"
                " (member_sub, kind, title, recipe, filters)"
                " VALUES (%s, %s, %s, %s, %s)"
                f" RETURNING {_COLUMNS}",
                (member_sub, kind, title, Jsonb(recipe), Jsonb(filters)),
            )
            return _row(await cur.fetchone())


async def get(member_sub: str, resource_id: str) -> dict | None:
    """None for both a missing id and another member's id: the caller turns
    both into the same 404, so probing cannot distinguish them."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_widget_resource"
                " WHERE id = %s AND member_sub = %s",
                (resource_id, member_sub),
            )
            return _row(await cur.fetchone())


async def list_for(member_sub: str) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_widget_resource"
                " WHERE member_sub = %s ORDER BY updated_at DESC",
                (member_sub,),
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def update_filters(
    member_sub: str, resource_id: str, filters: dict, expected_revision: int
) -> dict | None:
    """None when the row is absent, foreign, or at a different revision.

    The revision check is in the UPDATE's own WHERE clause rather than a
    read-then-write, so two concurrent writers cannot both pass it.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "UPDATE eve_widget_resource"
                " SET filters = %s, revision = revision + 1, updated_at = now()"
                " WHERE id = %s AND member_sub = %s AND revision = %s"
                f" RETURNING {_COLUMNS}",
                (Jsonb(filters), resource_id, member_sub, expected_revision),
            )
            return _row(await cur.fetchone())


async def delete(member_sub: str, resource_id: str) -> bool:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM eve_widget_resource WHERE id = %s AND member_sub = %s",
            (resource_id, member_sub),
        )
        return cur.rowcount == 1