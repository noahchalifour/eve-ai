"""eve-tools' own connection pool. Mirrors `eve.memory.db`'s shape but is
deliberately a second, separate pool on a second, separate DSN: ADR 0016
gives eve-tools one table under its own restricted role, and sharing Eve's
pool would hand it Eve's role. `src/eve_tools/` importing from `src/eve/` is
the thing this module exists to avoid.

No `migrate()` here. Alembic runs from Eve's container against
`eve_alembic_version`; eve-tools has no DDL grant and must never try.
"""

from __future__ import annotations

import asyncio

from psycopg_pool import AsyncConnectionPool

from eve_tools.settings import get_tools_settings

_pool: AsyncConnectionPool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> AsyncConnectionPool:
    global _pool
    async with _pool_lock:
        if _pool is None:
            url = get_tools_settings().database_url
            if not url:
                raise RuntimeError(
                    "EVE_TOOLS_DATABASE_URL is unset; the health providers "
                    "cannot read their OAuth tokens"
                )
            # autocommit matches eve.memory.db. The refresh path in
            # oauth_store needs a real transaction for FOR UPDATE and opens
            # one explicitly with `conn.transaction()`.
            _pool = AsyncConnectionPool(
                url, min_size=1, max_size=5, open=False, kwargs={"autocommit": True}
            )
            await _pool.open(wait=True, timeout=30)
    return _pool


async def close_pool() -> None:
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None
