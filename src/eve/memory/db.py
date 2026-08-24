"""Connection pool and schema migration for Eve's memory.

Migrations are a hand-rolled ordered list rather than Alembic. Aegra already
runs its own Alembic migrations at startup and ours must not interleave with
them, and there are only three tables here, across three migration entries.

    ponytail: hand-rolled because there are so few tables. Move to Alembic if
    MIGRATIONS exceeds ~5 entries.
"""

from __future__ import annotations

import asyncio

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from eve.settings import get_settings

# Arbitrary but fixed. Two pods starting at once must not both try to create
# the table; the loser waits and then finds every step already applied.
_MIGRATION_LOCK = 0x45564532

MIGRATIONS: list[tuple[str, str]] = [
    (
        "0001_memory",
        """
        CREATE EXTENSION IF NOT EXISTS vector;

        CREATE TABLE IF NOT EXISTS eve_memory (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          layer          text        NOT NULL,
          scope_kind     text        NOT NULL,
          scope_id       text        NOT NULL,
          kind           text        NOT NULL,
          subject        text,
          content        text        NOT NULL,
          confidence     real        NOT NULL DEFAULT 0.7,
          salience       real        NOT NULL DEFAULT 0.5,
          source_thread  text,
          source_run     text,
          created_at     timestamptz NOT NULL DEFAULT now(),
          last_seen_at   timestamptz NOT NULL DEFAULT now(),
          superseded_by  uuid REFERENCES eve_memory(id) ON DELETE SET NULL,
          superseded_why text,
          embedding      vector(1536),
          content_tsv    tsvector GENERATED ALWAYS AS
                           (to_tsvector('english', content)) STORED
        );

        CREATE INDEX IF NOT EXISTS eve_memory_tsv
          ON eve_memory USING gin (content_tsv);
        CREATE INDEX IF NOT EXISTS eve_memory_embedding
          ON eve_memory USING hnsw (embedding vector_cosine_ops);
        CREATE INDEX IF NOT EXISTS eve_memory_scope
          ON eve_memory (scope_kind, scope_id, layer)
          WHERE superseded_why IS NULL;
        CREATE INDEX IF NOT EXISTS eve_memory_subject
          ON eve_memory (subject) WHERE superseded_why IS NULL;
        """,
    ),
    (
        "0002_ambient",
        """
        -- Dedup and cooldown for ambient signals (Phase 4, design section
        -- 4.5). There is deliberately no cursor table: every source is
        -- time-windowed or content-keyed, so this table alone gives
        -- exactly-once delivery.
        CREATE TABLE IF NOT EXISTS eve_ambient_seen (
          source        text        NOT NULL,
          key           text        NOT NULL,
          last_seen_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (source, key)
        );

        -- Every notification actually sent. This IS the daily-cap counter
        -- (counted per member per local day) and the record of what Eve
        -- chose to interrupt, which is Phase 5's training signal.
        CREATE TABLE IF NOT EXISTS eve_ambient_notice (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub text        NOT NULL,
          source     text        NOT NULL,
          key        text        NOT NULL,
          urgent     boolean     NOT NULL DEFAULT false,
          thread_id  text,
          sent_at    timestamptz NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS eve_ambient_notice_member_sent
          ON eve_ambient_notice (member_sub, sent_at DESC);
        """,
    ),
    (
        "0003_ambient_notice_window",
        """
        -- Supports store.already_notified's cooldown-bounded lookup (fix
        -- round 2 on the ambient pipeline task): "has this member already
        -- been told about this (source, key) within its cooldown window",
        -- run once per member per signal. Without this the query would
        -- fall back to a sequential scan of eve_ambient_notice.
        CREATE INDEX IF NOT EXISTS eve_ambient_notice_member_source_key_sent
          ON eve_ambient_notice (member_sub, source, key, sent_at DESC);
        """,
    ),
]

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
    pool = await get_pool()
    async with pool.connection() as conn:
        # Scoped to this one cursor, not `conn.row_factory = dict_row`: the
        # pool's default reset only rolls back an open transaction between
        # checkouts (psycopg_pool.pool_async._reset_connection), it does not
        # restore row_factory. Setting it on the connection would leak
        # dict rows to every later caller that reuses this pooled connection.
        await conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS eve_schema_version ("
                " name text PRIMARY KEY,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT name FROM eve_schema_version")
                applied = {row["name"] for row in await cur.fetchall()}
            for name, ddl in MIGRATIONS:
                if name in applied:
                    continue
                await conn.execute(ddl)
                await conn.execute(
                    "INSERT INTO eve_schema_version (name) VALUES (%s)", (name,)
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
