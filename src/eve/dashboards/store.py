"""Every eve_dashboard SQL statement.

Every statement carries `member_sub` in its WHERE clause. A device id is a
locator chosen by the client and nothing more: the same device id under two
members is two dashboards, and a foreign one reads as absent.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool

_COLUMNS = "id, device_id, purpose, columns, layout, revision, updated_at"


def _row(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {**row, "id": str(row["id"])}


async def get(member_sub: str, device_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_dashboard"
                " WHERE member_sub = %s AND device_id = %s",
                (member_sub, device_id),
            )
            return _row(await cur.fetchone())


async def replace(
    member_sub: str, device_id: str, purpose: str, columns: int, layout: list[dict]
) -> dict:
    """Creates the device's dashboard, or replaces it wholesale.

    Replacing keeps counting revisions rather than restarting at 1, so a
    client still holding the old dashboard's revision gets a 409 on its next
    layout save instead of silently overwriting the new one."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO eve_dashboard (member_sub, device_id, purpose, columns, layout)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (member_sub, device_id) DO UPDATE SET"
                " purpose = EXCLUDED.purpose, columns = EXCLUDED.columns,"
                " layout = EXCLUDED.layout, revision = eve_dashboard.revision + 1,"
                " updated_at = now()"
                f" RETURNING {_COLUMNS}",
                (member_sub, device_id, purpose, columns, Jsonb(layout)),
            )
            return _row(await cur.fetchone())


async def update_layout(
    member_sub: str, device_id: str, layout: list[dict], expected_revision: int
) -> dict | None:
    """None when absent, foreign, or at a different revision. The revision
    check is in the UPDATE's own WHERE, so two writers cannot both pass it."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "UPDATE eve_dashboard"
                " SET layout = %s, revision = revision + 1, updated_at = now()"
                " WHERE member_sub = %s AND device_id = %s AND revision = %s"
                f" RETURNING {_COLUMNS}",
                (Jsonb(layout), member_sub, device_id, expected_revision),
            )
            return _row(await cur.fetchone())


async def delete(member_sub: str, device_id: str) -> bool:
    """Deletes the dashboard only. Its tiles are library widgets and stay."""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM eve_dashboard WHERE member_sub = %s AND device_id = %s",
            (member_sub, device_id),
        )
        return cur.rowcount == 1
