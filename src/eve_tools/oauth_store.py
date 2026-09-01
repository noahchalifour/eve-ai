"""Per-member OAuth tokens for the health providers, and the protocol that
keeps a rotating refresh token from being rotated twice at once.

Provider-agnostic by design: it takes a `refresh` callable rather than
knowing anything about WHOOP or Oura, so a third wearable is a new client
plus a row, not a change to the locking here.
"""

from __future__ import annotations

import logging
from datetime import datetime

from psycopg.rows import dict_row

from eve_tools.db import get_pool

logger = logging.getLogger(__name__)


class NotConnected(Exception):
    """No token row for this provider and member - the member has never
    completed the authorization flow. Distinct from ReconnectRequired: this
    one is "never set up", that one is "set up and now broken"."""


class ReconnectRequired(Exception):
    """A refresh was attempted and the provider rejected it - a revoked
    refresh token, or the member disconnected the app. Only a human re-running
    scripts/health_oauth_setup.py fixes it, so it must never present to the
    model as "no data": that would have the coach reporting a quiet night's
    sleep when the truth is broken auth."""


async def get_row(provider: str, member_sub: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_oauth_token"
                " WHERE provider = %s AND member_sub = %s",
                (provider, member_sub),
            )
            return await cur.fetchone()


async def save(
    provider: str,
    member_sub: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
) -> None:
    """Upsert. Rotation means this runs repeatedly for one row, so a plain
    INSERT would fail the primary key on the second refresh."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_oauth_token"
            " (provider, member_sub, access_token, refresh_token, expires_at)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (provider, member_sub) DO UPDATE SET"
            "   access_token = EXCLUDED.access_token,"
            "   refresh_token = EXCLUDED.refresh_token,"
            "   expires_at = EXCLUDED.expires_at,"
            "   updated_at = now()",
            (provider, member_sub, access_token, refresh_token, expires_at),
        )


async def configured_providers(member_sub: str) -> list[str]:
    """Which providers this member has connected. `health.py` uses it to
    decide who to fan out to, and to build the `unconfigured` list. Sorted so
    that list is stable."""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT provider FROM eve_oauth_token WHERE member_sub = %s"
            " ORDER BY provider",
            (member_sub,),
        )
        return [row[0] for row in await cur.fetchall()]
