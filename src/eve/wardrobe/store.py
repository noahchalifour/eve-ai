"""Every wardrobe catalogue SQL statement. Same discipline as
`eve/computer/store.py`: one module owns the table, and nothing else in the
codebase writes SQL against it.
"""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool


async def catalogued_asset_ids(member_sub: str) -> set[str]:
    """Which assets this member's catalogue has already seen, including
    successful photographs that contained no garments."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT asset_id FROM eve_wardrobe_asset WHERE member_sub = %s",
                (member_sub,),
            )
            return {row[0] for row in await cur.fetchall()}


async def insert_items(member_sub: str, asset_id: str, items: list[dict]) -> None:
    """Replace everything catalogued from one asset.

    Delete-then-insert rather than upsert: a re-describe can return a
    different NUMBER of garments than last time (the prompt got better at
    seeing the second shirt on the rail), and an upsert keyed on item_index
    would leave the surplus rows from the longer previous run behind.
    """
    pool = await get_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO eve_wardrobe_asset (member_sub, asset_id) VALUES (%s, %s)"
            " ON CONFLICT (member_sub, asset_id)"
            " DO UPDATE SET catalogued_at = now()",
            (member_sub, asset_id),
        )
        await conn.execute(
            "DELETE FROM eve_wardrobe_item WHERE member_sub = %s AND asset_id = %s",
            (member_sub, asset_id),
        )
        for index, item in enumerate(items):
            await conn.execute(
                "INSERT INTO eve_wardrobe_item"
                " (member_sub, asset_id, item_index, name, category, attrs)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    member_sub,
                    asset_id,
                    index,
                    item["name"],
                    item["category"],
                    Jsonb(item.get("attrs") or {}),
                ),
            )


async def delete_assets(member_sub: str, asset_ids: list[str]) -> None:
    """Assets that have left the album. A no-op on an empty list rather than
    an `IN ()` syntax error."""
    if not asset_ids:
        return
    pool = await get_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM eve_wardrobe_asset"
            " WHERE member_sub = %s AND asset_id = ANY(%s)",
            (member_sub, list(asset_ids)),
        )
        await conn.execute(
            "DELETE FROM eve_wardrobe_item"
            " WHERE member_sub = %s AND asset_id = ANY(%s)",
            (member_sub, list(asset_ids)),
        )


async def list_items(member_sub: str) -> list[dict]:
    """The whole wardrobe, grouped-ready. Ordering is by category then name so
    the rendered catalogue and `eve-wardrobe list` agree without either
    sorting again."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_wardrobe_item WHERE member_sub = %s"
                " ORDER BY category, name",
                (member_sub,),
            )
            return list(await cur.fetchall())


async def count_items(member_sub: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT count(*) FROM eve_wardrobe_item WHERE member_sub = %s",
                (member_sub,),
            )
            row = await cur.fetchone()
            return int(row[0])
