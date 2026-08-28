"""Connection pool and schema migration for Eve's own tables.

Migrations run through Alembic (alembic/versions/) against a private
`eve_alembic_version` table. Aegra runs its own Alembic migrations at
startup against the same database and the default `alembic_version` table;
the private table is what keeps the two histories from interleaving.

Mostly memory, hence the module's location, plus `eve_pat` - which is auth,
not memory, but shares this pool rather than standing up a second of each
for one table.
"""

from __future__ import annotations

import asyncio

from psycopg_pool import AsyncConnectionPool

from eve.settings import get_settings

# Arbitrary but fixed. Two pods starting at once must not both try to create
# the table; the loser waits and then finds every step already applied.
_MIGRATION_LOCK = 0x45564532

# Retired in Phase 5c. The five entries that used to live here are reproduced
# in alembic/versions/0001_baseline.py; schema changes are Alembic revisions
# now. Kept as an empty list so the idempotency test's old assertion fails
# loudly rather than importing nothing.
MIGRATIONS: list[tuple[str, str]] = []

_pool: AsyncConnectionPool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> AsyncConnectionPool:
    global _pool
    async with _pool_lock:
        if _pool is None:
            url = get_settings().database_url
            if not url:
                raise RuntimeError(
                    "EVE_DATABASE_URL (or DATABASE_URL) is unset; memory "
                    "cannot start"
                )
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


async def migrate() -> None:
    """Run Alembic to head under the same advisory lock the hand-rolled list
    used. Aegra runs its own Alembic at startup against alembic_version; ours
    uses eve_alembic_version (alembic/env.py), so the two never interleave.
    """
    import asyncio
    from pathlib import Path

    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            root = Path(__file__).resolve().parents[3]
            proc = await asyncio.create_subprocess_exec(
                "alembic", "upgrade", "head",
                cwd=str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    "alembic upgrade failed:\n" + out.decode(errors="replace")
                )
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))


def main() -> None:
    """`eve-migrate` console script. Run before `aegra serve`.

    A separate command rather than an import side effect: a schema failure
    then kills the pod visibly at start, instead of surfacing as a confusing
    runtime error on somebody's first message.
    """

    async def _run() -> None:
        await migrate()
        await close_pool()

    asyncio.run(_run())
