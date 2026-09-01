"""Per-member OAuth tokens for the health providers, and the protocol that
keeps a rotating refresh token from being rotated twice at once.

Provider-agnostic by design: it takes a `refresh` callable rather than
knowing anything about WHOOP or Oura, so a third wearable is a new client
plus a row, not a change to the locking here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from opentelemetry import trace
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


# A token that expires mid-request is a failed request. Refresh this long
# before the stated expiry rather than at it.
SKEW_SECONDS = 120

# Called with the current refresh token; returns the provider's token
# response. The store never learns which provider it is talking to.
Refresher = Callable[[str], Awaitable[dict]]


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


def _is_stale(expires_at: datetime | None) -> bool:
    """A NULL expiry means "does not expire" - an Oura personal access token,
    or any non-rotating credential. Reading NULL as "expired at the epoch"
    would refresh it on every call, with no refresh token to do it with."""
    if expires_at is None:
        return False
    return expires_at <= datetime.now(UTC) + timedelta(seconds=SKEW_SECONDS)


async def _refresh_locked(
    provider: str, member_sub: str, refresh: Refresher, force: bool
) -> str:
    """Refresh under a row lock, or return what another caller just stored.

    Row-level FOR UPDATE rather than an advisory lock: contention is
    per-member-per-provider, exactly the granularity the primary key already
    gives. (`eve.memory.db`'s migration lock is advisory because its
    contention is process-wide - different problem, different tool.)
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        # The pool is autocommit, so the transaction has to be explicit -
        # FOR UPDATE outside one would release the lock immediately and this
        # whole function would be decoration.
        async with conn.transaction():
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT access_token, refresh_token, expires_at"
                    " FROM eve_oauth_token"
                    " WHERE provider = %s AND member_sub = %s"
                    " FOR UPDATE",
                    (provider, member_sub),
                )
                row = await cur.fetchone()
            if row is None:
                raise NotConnected(
                    f"{member_sub} has no {provider} credential; run "
                    "scripts/health_oauth_setup.py"
                )
            # Re-check inside the lock. Whoever held it a moment ago may have
            # already refreshed, in which case their token is the good one and
            # refreshing again would rotate theirs away - the exact bug.
            if not force and not _is_stale(row["expires_at"]):
                return row["access_token"]
            if not row["refresh_token"]:
                raise ReconnectRequired(
                    f"{provider} credential for {member_sub} has expired and "
                    "there is no refresh token; re-run "
                    "scripts/health_oauth_setup.py"
                )
            try:
                fresh = await refresh(row["refresh_token"])
            except Exception as exc:
                raise ReconnectRequired(
                    f"{provider} refused to refresh {member_sub}'s "
                    f"credential ({exc}); re-run "
                    "scripts/health_oauth_setup.py"
                ) from exc
            expires_in = fresh.get("expires_in")
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=int(expires_in))
                if expires_in
                else None
            )
            await conn.execute(
                "UPDATE eve_oauth_token SET access_token = %s,"
                " refresh_token = %s, expires_at = %s, updated_at = now()"
                " WHERE provider = %s AND member_sub = %s",
                (
                    fresh["access_token"],
                    # Keep the old one if the provider did not rotate. Storing
                    # NULL here would strand the row: expired, unrefreshable,
                    # and only fixable by a human.
                    fresh.get("refresh_token") or row["refresh_token"],
                    expires_at,
                    provider,
                    member_sub,
                ),
            )
    # Observability, spec section 10: a token refreshing far more often than
    # hourly means the skew window or the locking is wrong, and nothing else
    # makes that visible until auth breaks.
    logger.info("refreshed the %s token for %s", provider, member_sub)
    span = trace.get_current_span()
    span.set_attribute("eve.health.token_refreshed", provider)
    return fresh["access_token"]


async def access_token(provider: str, member_sub: str, refresh: Refresher) -> str:
    """Return a fresh access token without locking the common fresh path."""
    row = await get_row(provider, member_sub)
    if row is None:
        raise NotConnected(
            f"{member_sub} has no {provider} credential; run "
            "scripts/health_oauth_setup.py"
        )
    if not _is_stale(row["expires_at"]):
        return row["access_token"]
    return await _refresh_locked(provider, member_sub, refresh, force=False)


async def refresh_now(provider: str, member_sub: str, refresh: Refresher) -> str:
    """Refresh after one 401; callers retry exactly once with the result."""
    return await _refresh_locked(provider, member_sub, refresh, force=True)
