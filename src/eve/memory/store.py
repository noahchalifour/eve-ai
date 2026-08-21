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


async def _execute(sql: str, params: dict | tuple) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(sql, params)


async def add(
    *,
    layer: str,
    scope_kind: str,
    scope_id: str,
    kind: str,
    content: str,
    subject: str | None = None,
    confidence: float = 0.7,
    salience: float = 0.5,
    source_thread: str | None = None,
    source_run: str | None = None,
) -> str:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO eve_memory
              (layer, scope_kind, scope_id, kind, subject, content,
               confidence, salience, source_thread, source_run)
            VALUES
              (%(layer)s, %(scope_kind)s, %(scope_id)s, %(kind)s, %(subject)s,
               %(content)s, %(confidence)s, %(salience)s, %(thread)s, %(run)s)
            RETURNING id
            """,
            {
                "layer": layer,
                "scope_kind": scope_kind,
                "scope_id": scope_id,
                "kind": kind,
                "subject": subject,
                "content": content,
                "confidence": confidence,
                "salience": salience,
                "thread": source_thread,
                "run": source_run,
            },
        )
        return str((await cur.fetchone())[0])


async def supersede(old_id: str, new_id: str | None, why: str) -> None:
    """Retire a row. `new_id` may be None for an eviction, which replaces
    nothing."""
    await _execute(
        "UPDATE eve_memory SET superseded_by = %(new)s, superseded_why = %(why)s"
        " WHERE id = %(old)s AND superseded_why IS NULL",
        {"old": old_id, "new": new_id, "why": why},
    )


async def reinforce(memory_id: str) -> None:
    """Restated or used - reset the decay clock and raise salience.

    Salience is clamped at 1.0. Without the clamp a fact mentioned daily
    drifts to a value nothing else can outrank, and the layer stops being
    sortable at all.
    """
    await _execute(
        "UPDATE eve_memory"
        " SET last_seen_at = now(), salience = least(salience + 0.1, 1.0)"
        " WHERE id = %s",
        (memory_id,),
    )


async def forget(memory_id: str) -> None:
    """Hard delete. The ONE exception to supersede-don't-delete (spec 4.2).

    'Eve, forget I said that' has to mean the row is gone. A tombstone that
    still holds the text is not forgetting - it is a quiet lie to a family
    member about their own data.
    """
    await _execute("DELETE FROM eve_memory WHERE id = %s", (memory_id,))


async def set_embeddings(pairs: list[tuple[str, list[float]]]) -> None:
    if not pairs:
        return
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "UPDATE eve_memory SET embedding = %s::vector WHERE id = %s",
                [(to_pgvector(vec), mid) for mid, vec in pairs],
            )


async def upsert_digest(thread_id: str, content: str) -> None:
    """One digest row per thread, replaced in place.

    Delete-then-insert rather than ON CONFLICT: there is no natural unique
    key here (scope_id is a plain text column shared with three other layers),
    and adding a partial unique index for a row written once every six turns
    is machinery for nothing.

    The pair runs inside `conn.transaction()` even though the pool is
    autocommit: psycopg opens a real transaction block for the duration of
    the `async with` and commits it on exit, so a concurrent read (or a
    second overlapping call for the same thread_id) can never observe the
    gap between the DELETE and the INSERT - no transient zero-digest read,
    no chance of two rows landing side by side.
    """
    pool = await get_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM eve_memory WHERE layer='digest' AND scope_kind='thread'"
            " AND scope_id=%s",
            (thread_id,),
        )
        await conn.execute(
            "INSERT INTO eve_memory"
            " (layer, scope_kind, scope_id, kind, content, source_thread)"
            " VALUES ('digest','thread',%s,'digest',%s,%s)",
            (thread_id, content, thread_id),
        )


async def evict_over_cap(
    layer: str, scope_kind: str, scope_id: str, cap: int
) -> int:
    """Retire the weakest rows until the scope fits under its cap.

    Eviction is what makes the cap mean anything. A profile that grows without
    limit stops being a profile and becomes an episodic log with a misleading
    name (spec 3).

    The subselect's WHERE must filter on `superseded_why IS NULL`, not
    `superseded_by IS NULL`: an eviction replaces nothing, so an evicted row's
    `superseded_by` stays NULL forever. Filtering on the wrong column would
    re-select every already-evicted row on every run and the returned count
    would never mean anything.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            UPDATE eve_memory SET superseded_why = 'evicted',
                                  superseded_by = NULL
            WHERE id IN (
              SELECT id FROM eve_memory
              WHERE superseded_why IS NULL
                AND layer = %(layer)s AND scope_kind = %(kind)s
                AND scope_id = %(scope)s
              ORDER BY salience
                -- Half-life decay, matching ranking.recency_decay and
                -- search_episodic_lexical's SQL: exp(-ln(2) * age / half_life)
                -- is 0.5 at age == half_life, not exp(-age/half_life)'s
                -- ~0.368 (e-folding decay - a different constant with the
                -- same shape, and this codebase does not get to hold three
                -- opinions about what "half-life" means. 365 days here,
                -- deliberately much longer than episodic recall's 90:
                -- eviction decides what to keep forever, not what is
                -- relevant to surface right now.
                * exp(-ln(2) * EXTRACT(EPOCH FROM (now() - last_seen_at))
                      / 86400.0 / 365.0)
                DESC
              OFFSET %(cap)s
            )
            RETURNING id
            """,
            {"layer": layer, "kind": scope_kind, "scope": scope_id, "cap": cap},
        )
        return len(await cur.fetchall())


async def overlapping(
    sub: str, subjects: list[str], embedding: list[float] | None, limit: int = 10
) -> list[Memory]:
    """Existing memories a new fact might contradict.

    Extraction judges new facts against these rather than in a vacuum - which
    is the whole reason contradiction handling lives at write time and not in
    a nightly reconciler that would see two conflicting sentences and no way
    to tell which is current (spec 5.4).
    """
    if embedding is None:
        return await _fetch(
            f"""
            SELECT {_COLUMNS} FROM eve_memory
            WHERE superseded_why IS NULL AND layer <> 'digest'
              AND ((scope_kind='member' AND scope_id=%(sub)s)
                   OR scope_kind='household')
              AND subject = ANY(%(subjects)s)
            LIMIT %(limit)s
            """,
            {"sub": sub, "subjects": subjects, "limit": limit},
        )
    return await _fetch(
        f"""
        (SELECT {_COLUMNS} FROM eve_memory
         WHERE superseded_why IS NULL AND layer <> 'digest'
           AND ((scope_kind='member' AND scope_id=%(sub)s)
                OR scope_kind='household')
           AND subject = ANY(%(subjects)s)
         LIMIT %(limit)s)
        UNION
        (SELECT {_COLUMNS} FROM eve_memory
         WHERE superseded_why IS NULL AND layer <> 'digest'
           AND embedding IS NOT NULL
           AND ((scope_kind='member' AND scope_id=%(sub)s)
                OR scope_kind='household')
         ORDER BY embedding <=> %(vec)s::vector
         LIMIT %(limit)s)
        """,
        {
            "sub": sub,
            "subjects": subjects,
            "vec": to_pgvector(embedding),
            "limit": limit,
        },
    )
