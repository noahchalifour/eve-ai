"""Every SQL statement Eve's memory issues.

One module so the schema has exactly one consumer, and so a change to the
table is a change to one file.
"""

from __future__ import annotations

import re

from psycopg.rows import dict_row

from eve.memory.db import get_pool
from eve.memory.embed import to_pgvector
from eve.memory.types import Memory
from eve.settings import get_settings

_COLUMNS = (
    "id, layer, scope_kind, scope_id, kind, subject, content, "
    "confidence, salience, created_at, last_seen_at"
)

# Deliberately tiny. This is not linguistics - it is a cheap way to stop
# 'the' and 'is' matching every subject in the table. Postgres's own
# stopword list handles the full-text arm.
_STOPWORDS = frozenset(
    "a an and are as at be by do does did for from how i in is it its me my "
    "of on or our that the their they this to was we what when where which "
    "who why will with you your".split()
)
_WORD = re.compile(r"[a-z0-9']+")


def subjects_in(text: str) -> list[str]:
    """Candidate entity tokens from a query, for the `subject` arm."""
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def _row_to_memory(row: dict) -> Memory:
    return Memory(
        id=str(row["id"]),
        layer=row["layer"],
        scope_kind=row["scope_kind"],
        scope_id=row["scope_id"],
        kind=row["kind"],
        subject=row["subject"],
        content=row["content"],
        confidence=row["confidence"],
        salience=row["salience"],
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
    )


async def _fetch(sql: str, params: dict) -> list[Memory]:
    pool = await get_pool()
    async with pool.connection() as conn:
        # Scoped to this one cursor, not `conn.row_factory = dict_row`: see
        # db.migrate()'s comment. psycopg_pool's check-in reset only rolls
        # back an open transaction, it does not restore row_factory, so
        # setting it on the connection would leak dict rows to whichever
        # caller next checks this pooled connection out.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return [_row_to_memory(row) for row in await cur.fetchall()]


async def load_always_on(
    sub: str, thread_id: str | None
) -> tuple[list[Memory], list[Memory], str | None]:
    """Profile, household, and this thread's digest.

    One query rather than three: three round trips to fetch a hundred short
    rows is three times the latency for no benefit, and this runs before
    every single token Eve produces.
    """
    rows = await _fetch(
        f"""
        SELECT {_COLUMNS} FROM eve_memory
        WHERE superseded_why IS NULL
          AND (
            (layer = 'profile'   AND scope_kind = 'member'    AND scope_id = %(sub)s)
         OR (layer = 'household' AND scope_kind = 'household')
         OR (layer = 'digest'    AND scope_kind = 'thread'    AND scope_id = %(thread)s)
          )
        ORDER BY salience DESC, last_seen_at DESC
        """,
        {"sub": sub, "thread": thread_id or ""},
    )
    profile = [m for m in rows if m.layer == "profile"]
    household = [m for m in rows if m.layer == "household"]
    digest = next((m.content for m in rows if m.layer == "digest"), None)
    return profile, household, digest


async def search_episodic_lexical(
    sub: str, query: str, limit: int = 20
) -> list[Memory]:
    """Full text OR entity match, weighted by recency and salience.

    This arm CANNOT FAIL and must never be made to depend on a network call.
    It is what the turn ships when the vector arm misses its budget.
    """
    subjects = subjects_in(query)
    if not query.strip():
        return []
    return await _fetch(
        f"""
        SELECT {_COLUMNS} FROM eve_memory
        WHERE superseded_why IS NULL
          AND layer = 'episodic'
          AND ((scope_kind = 'member' AND scope_id = %(sub)s)
               OR scope_kind = 'household')
          AND (content_tsv @@ plainto_tsquery('english', %(q)s)
               OR subject = ANY(%(subjects)s))
        ORDER BY
          (ts_rank(content_tsv, plainto_tsquery('english', %(q)s)) + 0.1)
          * (CASE WHEN subject = ANY(%(subjects)s) THEN 2.0 ELSE 1.0 END)
          -- ln(2) makes this a true half-life decay (0.5 at
          -- age == half_life), matching ranking.recency_decay.
          -- exp(-age/half_life) alone is e-folding decay (~0.368 at
          -- age == half_life) and would silently disagree with the
          -- Python arm about what "half-life" means.
          * exp(-ln(2) * EXTRACT(EPOCH FROM (now() - last_seen_at)) / 86400.0
                / %(half_life)s)
          * salience DESC
        LIMIT %(limit)s
        """,
        {
            "sub": sub,
            "q": query,
            "subjects": subjects,
            "half_life": get_settings().memory_episodic_half_life_days,
            "limit": limit,
        },
    )


async def search_episodic_vector(
    sub: str, embedding: list[float], limit: int = 20
) -> list[Memory]:
    """Nearest neighbours by cosine distance.

    `embedding IS NOT NULL` is load-bearing: rows are inserted before they are
    embedded, and the embedding call can fail, so unembedded rows exist
    routinely rather than exceptionally.
    """
    return await _fetch(
        f"""
        SELECT {_COLUMNS} FROM eve_memory
        WHERE superseded_why IS NULL
          AND layer = 'episodic'
          AND embedding IS NOT NULL
          AND ((scope_kind = 'member' AND scope_id = %(sub)s)
               OR scope_kind = 'household')
        ORDER BY embedding <=> %(vec)s::vector
        LIMIT %(limit)s
        """,
        {"sub": sub, "vec": to_pgvector(embedding), "limit": limit},
    )
