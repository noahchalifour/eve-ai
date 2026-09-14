# Widget Resources Implementation Plan (server)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Eve a generic record store and an authenticated resource API so a saved widget can refresh its own data directly, with no graph run and no model call, and no domain ever earning its own table.

**Architecture:** Two generic Postgres tables (`eve_record`, `eve_widget_resource`) behind two store modules. A pure resolver turns a saved declarative recipe into a snapshot by reading allowlisted sources. A custom FastAPI app, mounted through Aegra's `http.app` with `enable_custom_route_auth`, exposes capabilities/snapshot/action routes that authenticate as the member and scope every query by owner. Two model-facing tools (`record.append`, `record.query`) plus one authoring tool write through the same domain functions the routes use.

**Tech Stack:** Python 3.12, FastAPI, Aegra 0.10.3, LangGraph/LangChain tools, psycopg 3 + Alembic, pytest (`asyncio_mode = "auto"`, async tests need no decorator).

**Spec:** `../../../../open-assistant/docs/superpowers/specs/2026-09-14-reusable-widgets-design.md` (the spec lives in the client repo; read it before Task 1)

## Global Constraints

- **Repo:** all work here is in `/Users/nchalifo/GitHub/eve-ai`. The client half is a separate plan in the `open-assistant` repo.
- **Unit test command:** `uv run pytest` (defaults to `-m "not integration and not live and not docker"`).
- **Integration test command:** `docker compose -f docker-compose.test.yml up -d postgres` then `uv run pytest -m integration`.
- **DB tests** use `postgresql://eve:eve@127.0.0.1:15432/eve` and carry `pytestmark = pytest.mark.integration`.
- **No domain-specific table, column, endpoint, or handler.** No `workout`, `exercise`, `reps`, or `set` identifier may appear in any schema, route, or module name. Collections are runtime strings.
- **Every record and resource query is scoped by `member_sub` in SQL.** A resource id is a locator, never authorization. Missing and foreign ids both return 404.
- **Identity comes from the authenticated principal only.** Never read `member_sub` from a request body or query string.
- **Never invoke the graph from a route.** Snapshot and action routes are deterministic.
- **Every external call degrades to a returned string, never a raised exception**, matching `eve.tools_client.invoke`.
- **One module owns each table's SQL**, matching `eve/computer/store.py` and `eve/wardrobe/store.py`. Nothing else writes SQL against them.
- **Migration chain:** current head is `0008_merge_coding_and_wardrobe`. New revisions descend from it in order.
- **Public errors are sanitized.** Never echo a raw upstream exception string, an OAuth token, or the `eve-tools` bearer.

---

## File Structure

| Path | Responsibility | Change |
|---|---|---|
| `alembic/versions/0009_eve_record.py` | Generic record table | **Create** |
| `alembic/versions/0010_eve_widget_resource.py` | Widget resource table | **Create** |
| `src/eve/records/__init__.py` | Package marker | **Create** |
| `src/eve/records/store.py` | Every `eve_record` SQL statement | **Create** |
| `src/eve/records/tools.py` | `record_append` / `record_query` model tools | **Create** |
| `src/eve/widgets/__init__.py` | Package marker | **Create** |
| `src/eve/widgets/store.py` | Every `eve_widget_resource` SQL statement | **Create** |
| `src/eve/widgets/recipe.py` | Pure recipe validation + allowlists | **Create** |
| `src/eve/widgets/resolve.py` | Recipe to snapshot, pure over injected readers | **Create** |
| `src/eve/widgets/tools.py` | `save_widget` authoring tool | **Create** |
| `src/eve/widgets/app.py` | Custom FastAPI routes | **Create** |
| `src/eve/graph.py` | Bind the three new tools | Modify |
| `aegra.json` | Mount the custom app | Modify |
| `skills/track-anything/SKILL.md` | Judgement for recording and charting | **Create** |
| `tests/test_records_store.py` | Record store, integration | **Create** |
| `tests/test_records_tools.py` | Record tools, unit | **Create** |
| `tests/test_widgets_recipe.py` | Recipe validation, unit | **Create** |
| `tests/test_widgets_resolve.py` | Resolver, unit | **Create** |
| `tests/test_widgets_store.py` | Resource store, integration | **Create** |
| `tests/test_widgets_app.py` | Routes + auth + ownership, unit | **Create** |
| `tests/test_widgets_integration.py` | Live Aegra mount, integration | **Create** |
| `docs/adr/0019-one-generic-record-store.md` | Why no domain tables | **Create** |
| `docs/architecture.md` | Module map + new section | Modify |
| `README.md` | Feature summary | Modify |

---

## Task 1: The generic record table

One table for every domain, forever. `occurred_at` is a real column because a chart over a date range must be an indexed query rather than a scan that parses JSON.

**Files:**
- Create: `alembic/versions/0009_eve_record.py`
- Create: `tests/test_records_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces: table `eve_record` with columns `id uuid`, `member_sub text`, `collection text`, `key text NULL`, `payload jsonb`, `occurred_at timestamptz`, `created_at timestamptz`; unique index on `(member_sub, collection, key)` where `key` is not null; index on `(member_sub, collection, occurred_at DESC)`.

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_records_store.py`:

```python
"""tests/test_records_store.py"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve")
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_record")
    yield p
    await db.close_pool()


async def test_the_record_table_exists_after_migration(pool):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'eve_record'"
        )
        columns = {row[0] for row in await cur.fetchall()}

    assert {
        "id",
        "member_sub",
        "collection",
        "key",
        "payload",
        "occurred_at",
        "created_at",
    } <= columns


async def test_no_domain_specific_table_was_created(pool):
    """The whole point: a domain costs a collection name, never a table."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = 'public'"
        )
        tables = {row[0] for row in await cur.fetchall()}

    assert not [t for t in tables if "workout" in t or "exercise" in t]
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `docker compose -f docker-compose.test.yml up -d postgres && uv run pytest tests/test_records_store.py -m integration -v`
Expected: FAIL. `test_the_record_table_exists_after_migration` finds no columns.

- [ ] **Step 3: Write the migration**

Create `alembic/versions/0009_eve_record.py`:

```python
"""One row per member-recorded entry, in any domain.

Revision ID: 0009_eve_record
Revises: 0008_merge_coding_and_wardrobe

There is deliberately no per-domain table. A workout set, a reading session
and a finished chore are the same shape - "this member recorded this, then" -
and differ only by `collection` and the keys inside `payload`. ADR 0008 made
the same call for authored behaviour, storing rules and procedures as
`eve_memory` layers rather than building a store per kind; ADR 0019 records
this one.

`occurred_at` is a real column rather than a payload key because the whole
reason this table exists instead of `eve_memory` is range aggregation: a
chart over ninety days must be an index scan, not a sequential scan that
parses jsonb. `payload` holds everything domain-specific and is never
queried structurally here.

`key` is optional and, when present, unique per (member, collection): it is
how a re-submitted append is recognised as the same entry rather than
inserted twice.
"""
from alembic import op

revision = "0009_eve_record"
down_revision = "0008_merge_coding_and_wardrobe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_record (
          id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub  text        NOT NULL,
          collection  text        NOT NULL,
          key         text,
          payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Every read is "this member's entries in this collection, over a window".
    op.execute(
        "CREATE INDEX eve_record_member_collection_time"
        " ON eve_record (member_sub, collection, occurred_at DESC)"
    )
    # Idempotency, only for appends that supplied a key.
    op.execute(
        "CREATE UNIQUE INDEX eve_record_member_collection_key"
        " ON eve_record (member_sub, collection, key)"
        " WHERE key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_record")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_records_store.py -m integration -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0009_eve_record.py tests/test_records_store.py
git commit -m "feat(records): one generic record table for every domain"
```

---

## Task 2: The record store

**Files:**
- Create: `src/eve/records/__init__.py`
- Create: `src/eve/records/store.py`
- Modify: `tests/test_records_store.py`

**Interfaces:**
- Consumes: Task 1's `eve_record` table.
- Produces:
  - `async def append(member_sub: str, collection: str, payload: dict, occurred_at: datetime | None = None, key: str | None = None) -> dict` returning `{"id": str, "deduped": bool}`
  - `async def query(member_sub: str, collection: str, since: datetime | None = None, until: datetime | None = None, limit: int = 500) -> list[dict]` with rows `{"id","collection","key","payload","occurred_at"}`
  - `async def known_fields(member_sub: str, collection: str) -> list[str]`
  - `async def collections(member_sub: str) -> list[str]`

- [ ] **Step 1: Write the failing store tests**

Append to `tests/test_records_store.py`:

```python
from datetime import datetime, timedelta, timezone


async def test_append_and_query_round_trip(pool):
    from eve.records import store

    now = datetime.now(timezone.utc)
    await store.append("sub-noah", "anything.entry", {"n": 1}, occurred_at=now)

    rows = await store.query("sub-noah", "anything.entry")
    assert len(rows) == 1
    assert rows[0]["payload"] == {"n": 1}


async def test_two_unrelated_collections_share_one_table(pool):
    """Genericity: nothing about either name is known to the code."""
    from eve.records import store

    await store.append("sub-noah", "alpha.thing", {"a": 1})
    await store.append("sub-noah", "beta.other", {"b": 2})

    assert len(await store.query("sub-noah", "alpha.thing")) == 1
    assert len(await store.query("sub-noah", "beta.other")) == 1
    assert set(await store.collections("sub-noah")) == {"alpha.thing", "beta.other"}


async def test_queries_are_scoped_to_the_member(pool):
    from eve.records import store

    await store.append("sub-noah", "alpha.thing", {"a": 1})
    await store.append("sub-kendra", "alpha.thing", {"a": 2})

    rows = await store.query("sub-noah", "alpha.thing")
    assert [r["payload"] for r in rows] == [{"a": 1}]


async def test_a_repeated_key_is_deduped_rather_than_duplicated(pool):
    from eve.records import store

    first = await store.append("sub-noah", "alpha.thing", {"a": 1}, key="k1")
    second = await store.append("sub-noah", "alpha.thing", {"a": 999}, key="k1")

    assert first["deduped"] is False
    assert second["deduped"] is True
    rows = await store.query("sub-noah", "alpha.thing")
    assert len(rows) == 1
    # The first write wins: an idempotent retry must not silently rewrite.
    assert rows[0]["payload"] == {"a": 1}


async def test_query_filters_by_window(pool):
    from eve.records import store

    now = datetime.now(timezone.utc)
    await store.append("sub-noah", "alpha.thing", {"old": 1},
                       occurred_at=now - timedelta(days=30))
    await store.append("sub-noah", "alpha.thing", {"new": 1}, occurred_at=now)

    rows = await store.query("sub-noah", "alpha.thing", since=now - timedelta(days=7))
    assert len(rows) == 1
    assert rows[0]["payload"] == {"new": 1}


async def test_known_fields_reports_what_this_collection_has_seen(pool):
    from eve.records import store

    await store.append("sub-noah", "alpha.thing", {"weight": 100, "reps": 5})
    await store.append("sub-noah", "alpha.thing", {"weight": 105, "note": "ok"})

    assert set(await store.known_fields("sub-noah", "alpha.thing")) == {
        "weight", "reps", "note"
    }


async def test_known_fields_is_empty_for_an_unseen_collection(pool):
    from eve.records import store

    assert await store.known_fields("sub-noah", "never.used") == []
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_records_store.py -m integration -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.records'`.

- [ ] **Step 3: Write the store**

Create `src/eve/records/__init__.py` (empty file).

Create `src/eve/records/store.py`:

```python
"""Every eve_record SQL statement. Same discipline as `eve/computer/store.py`
and `eve/wardrobe/store.py`: one module owns the table.

Nothing here knows what a collection means. `collection` is an opaque string
supplied at runtime, and `payload` is opaque jsonb. That is the whole design:
a new domain is a new string, never a schema change.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool

MAX_LIMIT = 2000


async def append(
    member_sub: str,
    collection: str,
    payload: dict,
    occurred_at: datetime | None = None,
    key: str | None = None,
) -> dict:
    """Insert one entry. With a `key`, a repeat is recognised and the FIRST
    write is kept.

    First-write-wins rather than upsert: `key` exists for retry idempotency,
    and a retry carrying different values is a client bug, not an edit. An
    edit is a new entry, so the history stays append-only and a chart cannot
    change shape retroactively.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO eve_record"
                " (member_sub, collection, key, payload, occurred_at)"
                " VALUES (%s, %s, %s, %s, COALESCE(%s, now()))"
                " ON CONFLICT (member_sub, collection, key)"
                "   WHERE key IS NOT NULL DO NOTHING"
                " RETURNING id",
                (member_sub, collection, key, Jsonb(payload), occurred_at),
            )
            inserted = await cur.fetchone()
            if inserted is not None:
                return {"id": str(inserted["id"]), "deduped": False}

            # DO NOTHING returned no row, so the key already existed.
            await cur.execute(
                "SELECT id FROM eve_record"
                " WHERE member_sub = %s AND collection = %s AND key = %s",
                (member_sub, collection, key),
            )
            existing = await cur.fetchone()
            return {"id": str(existing["id"]), "deduped": True}


async def query(
    member_sub: str,
    collection: str,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 500,
) -> list[dict]:
    """This member's entries in one collection, newest first."""
    pool = await get_pool()
    bounded = max(1, min(limit, MAX_LIMIT))
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, collection, key, payload, occurred_at"
                " FROM eve_record"
                " WHERE member_sub = %s AND collection = %s"
                "   AND (%s::timestamptz IS NULL OR occurred_at >= %s)"
                "   AND (%s::timestamptz IS NULL OR occurred_at <= %s)"
                " ORDER BY occurred_at DESC"
                " LIMIT %s",
                (member_sub, collection, since, since, until, until, bounded),
            )
            return [dict(row) for row in await cur.fetchall()]


async def known_fields(member_sub: str, collection: str) -> list[str]:
    """Payload keys this collection has actually used.

    Advisory, not a schema. It is returned to the model on append so later
    entries match earlier ones, which is what makes a chart over the
    collection possible without ever declaring one.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT jsonb_object_keys(payload) FROM eve_record"
                " WHERE member_sub = %s AND collection = %s",
                (member_sub, collection),
            )
            return sorted(row[0] for row in await cur.fetchall())


async def collections(member_sub: str) -> list[str]:
    """Every collection this member has recorded into."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT collection FROM eve_record WHERE member_sub = %s"
                " ORDER BY collection",
                (member_sub,),
            )
            return [row[0] for row in await cur.fetchall()]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_records_store.py -m integration -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/eve/records/ tests/test_records_store.py
git commit -m "feat(records): member-scoped generic record store"
```

---

## Task 3: The record tools and the skill

Two tools and one skill replace every per-domain logging tool that would otherwise be written. The skill carries judgement; the tools carry no domain knowledge at all.

**Files:**
- Create: `src/eve/records/tools.py`
- Create: `skills/track-anything/SKILL.md`
- Create: `tests/test_records_tools.py`
- Modify: `src/eve/graph.py`

**Interfaces:**
- Consumes: `eve.records.store.append/query/known_fields`.
- Produces: `record_append` and `record_query` LangChain tools, both reading `config["configurable"]["member"]["sub"]`.

- [ ] **Step 1: Write the failing tool tests**

Create `tests/test_records_tools.py`:

```python
"""The two generic record tools. No test here names a domain: if one did,
the tools would have learned something they must not know."""
from __future__ import annotations

import pytest

CONFIG = {"configurable": {"member": {"sub": "sub-noah"}}}


def _call(tool, args):
    return tool.ainvoke(
        {"type": "tool_call", "name": tool.name, "args": args, "id": "t1"},
        config=CONFIG,
    )


async def test_append_writes_for_the_authenticated_member(monkeypatch):
    from eve.records import tools

    seen = {}

    async def fake_append(member_sub, collection, payload, occurred_at=None, key=None):
        seen.update(
            member_sub=member_sub, collection=collection,
            payload=payload, key=key,
        )
        return {"id": "r1", "deduped": False}

    async def fake_known_fields(member_sub, collection):
        return ["weight"]

    monkeypatch.setattr(tools.store, "append", fake_append)
    monkeypatch.setattr(tools.store, "known_fields", fake_known_fields)

    result = await _call(
        tools.record_append,
        {"collection": "alpha.thing", "payload": {"weight": 100}},
    )

    assert seen["member_sub"] == "sub-noah"
    assert seen["collection"] == "alpha.thing"
    assert "weight" in result.content


async def test_append_ignores_a_model_supplied_member(monkeypatch):
    """Identity is the authenticated principal's, never an argument."""
    from eve.records import tools

    seen = {}

    async def fake_append(member_sub, collection, payload, occurred_at=None, key=None):
        seen["member_sub"] = member_sub
        return {"id": "r1", "deduped": False}

    monkeypatch.setattr(tools.store, "append", fake_append)
    monkeypatch.setattr(tools.store, "known_fields", lambda *a: _none())

    result = await _call(
        tools.record_append,
        {
            "collection": "alpha.thing",
            "payload": {"member_sub": "sub-kendra", "weight": 100},
        },
    )

    assert seen["member_sub"] == "sub-noah"
    assert "sub-kendra" not in str(seen.get("member_sub"))


async def _none():
    return []


async def test_append_reports_the_collections_known_fields(monkeypatch):
    """The learned shape steers the next write; it never rejects this one."""
    from eve.records import tools

    async def fake_append(member_sub, collection, payload, occurred_at=None, key=None):
        return {"id": "r1", "deduped": False}

    async def fake_known_fields(member_sub, collection):
        return ["duration", "label"]

    monkeypatch.setattr(tools.store, "append", fake_append)
    monkeypatch.setattr(tools.store, "known_fields", fake_known_fields)

    result = await _call(
        tools.record_append,
        {"collection": "alpha.thing", "payload": {"totally": "different"}},
    )

    assert "duration" in result.content
    assert "label" in result.content


async def test_a_divergent_payload_is_still_stored(monkeypatch):
    """Member data is never lost to a shape disagreement."""
    from eve.records import tools

    stored = []

    async def fake_append(member_sub, collection, payload, occurred_at=None, key=None):
        stored.append(payload)
        return {"id": "r1", "deduped": False}

    monkeypatch.setattr(tools.store, "append", fake_append)
    monkeypatch.setattr(tools.store, "known_fields", lambda *a: _none())

    await _call(
        tools.record_append,
        {"collection": "alpha.thing", "payload": {"unexpected": True}},
    )

    assert stored == [{"unexpected": True}]


async def test_append_degrades_to_a_string_on_failure(monkeypatch):
    from eve.records import tools

    async def boom(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(tools.store, "append", boom)

    result = await _call(
        tools.record_append,
        {"collection": "alpha.thing", "payload": {"a": 1}},
    )

    assert "error" in result.content.lower()


async def test_query_returns_this_members_entries(monkeypatch):
    from eve.records import tools

    async def fake_query(member_sub, collection, since=None, until=None, limit=500):
        assert member_sub == "sub-noah"
        return [{"occurred_at": "2026-09-01T00:00:00Z", "payload": {"a": 1}}]

    monkeypatch.setattr(tools.store, "query", fake_query)

    result = await _call(
        tools.record_query, {"collection": "alpha.thing", "days": 7}
    )

    assert "a" in result.content


async def test_query_with_nothing_recorded_says_so_plainly(monkeypatch):
    """An empty collection must read as empty, not as a failure."""
    from eve.records import tools

    async def fake_query(member_sub, collection, since=None, until=None, limit=500):
        return []

    monkeypatch.setattr(tools.store, "query", fake_query)

    result = await _call(
        tools.record_query, {"collection": "alpha.thing", "days": 7}
    )

    assert "nothing recorded" in result.content.lower()
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_records_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.records.tools'`.

- [ ] **Step 3: Write the tools**

Create `src/eve/records/tools.py`:

```python
"""Two tools, no domains.

Every per-domain logging tool anyone might want - log a workout, log a book,
log a chore - is this one tool with a different `collection` string. What
each domain MEANS lives in a skill, which is prose and costs no code.

`known_fields` comes back on every append so the model can match the shape
it used last time. It is advisory: a divergent payload is stored, not
rejected, because member-recorded data must never be lost to a schema
disagreement it cannot see.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.records import store

logger = logging.getLogger(__name__)

MAX_DAYS = 3650


def _member(config: RunnableConfig) -> str:
    return config["configurable"]["member"]["sub"]


@tool
async def record_append(
    collection: str,
    payload: dict,
    config: RunnableConfig,
    occurred_at: str | None = None,
    key: str | None = None,
) -> str:
    """Record one entry the member wants kept and later charted.

    `collection` is a stable dotted name you choose for this kind of entry
    (for example `reading.session`). Reuse the SAME name and the same payload
    keys for the same kind of thing, or a chart over it will miss entries.
    `occurred_at` is an ISO-8601 timestamp; omit it for now.
    """
    member_sub = _member(config)
    try:
        when = datetime.fromisoformat(occurred_at) if occurred_at else None
    except ValueError:
        return f"error: occurred_at must be ISO-8601, got {occurred_at!r}"

    try:
        result = await store.append(
            member_sub, collection, payload, occurred_at=when, key=key
        )
        fields = await store.known_fields(member_sub, collection)
    except Exception as exc:
        logger.warning("record_append failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    note = "already recorded" if result["deduped"] else "recorded"
    if fields:
        return (
            f"{note} in {collection}. Fields used in this collection so far: "
            f"{', '.join(fields)}. Reuse these names for consistency."
        )
    return f"{note} in {collection}. This is the first entry in it."


@tool
async def record_query(
    collection: str,
    config: RunnableConfig,
    days: int = 30,
) -> str:
    """Read back this member's recorded entries in one collection."""
    member_sub = _member(config)
    window = max(1, min(days, MAX_DAYS))
    since = datetime.now(timezone.utc) - timedelta(days=window)

    try:
        rows = await store.query(member_sub, collection, since=since)
    except Exception as exc:
        logger.warning("record_query failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    if not rows:
        return (
            f"Nothing recorded in {collection} in the last {window} days. "
            "Check the collection name if you expected entries."
        )
    lines = [
        f"- {row['occurred_at']}: {json.dumps(row['payload'], default=str)}"
        for row in rows
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_records_tools.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Write the skill**

Create `skills/track-anything/SKILL.md`:

```markdown
---
name: track-anything
description: How to record things a member wants kept over time - workouts, reading, chores, habits, moods - and how to keep them consistent enough to chart later.
---
Use `record_append` for anything the member wants kept and looked at later.
There is no per-domain tool and there never will be: a workout set, a book
finished and a chore done are all the same call with a different
`collection`.

## Choosing a collection name

A stable dotted name, singular, for the kind of entry: `workout.set`,
`reading.session`, `chore.done`, `mood.check`. Reuse the exact name every
time. `workout.set` and `workouts` are two different collections, and a chart
reading one will silently miss everything in the other.

Before inventing a name, use `record_query` on the name you are about to use.
If entries come back, you already have the right name. If the member has
tracked something similar before, reuse that collection rather than starting
a parallel one.

## Keeping the payload chartable

`record_append` tells you which fields the collection has used so far. Match
them. A chart sums or counts a numeric field by name, so `weight` in one
entry and `load` in the next produces a chart that undercounts without
reporting anything wrong.

Put numbers in as numbers, not strings. One entry is one event: three sets of
an exercise is three appends, not one entry with a list inside it, because a
list cannot be aggregated over a date range.

Set `occurred_at` when the member is recording something from earlier ("I ran
yesterday"). Omit it for now.

## When to record at all

Record when the member is logging something that accumulates - a set, a
session, a completion. Answer in prose when they are asking a question. A
recorded entry nobody will ever chart is noise in a collection somebody else
will chart later.

If several entries arrive in one sentence ("I did 3x5 at 225"), append each
one, and say how many you recorded.
```

- [ ] **Step 6: Bind the tools in the graph**

In `src/eve/graph.py`, add to the imports near the other tool imports:

```python
from eve.records.tools import record_append, record_query
```

Then add both to `_BASE_TOOLS` so they are always available (find the `_BASE_TOOLS` list and append these two entries to it). They need no setting and no permission: a member recording their own data and reading it back is the least privileged thing in the system.

- [ ] **Step 7: Run the full unit suite**

Run: `uv run pytest`
Expected: PASS. Existing graph tests that assert on tool counts may need their expected numbers updated; update them to include the two new tools.

- [ ] **Step 8: Commit**

```bash
git add src/eve/records/tools.py skills/track-anything/SKILL.md tests/test_records_tools.py src/eve/graph.py tests/test_graph.py
git commit -m "feat(records): one generic append/query pair plus the judgement skill"
```

---

## Task 4: The widget resource table and store

**Files:**
- Create: `alembic/versions/0010_eve_widget_resource.py`
- Create: `src/eve/widgets/__init__.py`
- Create: `src/eve/widgets/store.py`
- Create: `tests/test_widgets_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `async def create(member_sub: str, kind: str, title: str, recipe: dict, filters: dict) -> dict`
  - `async def get(member_sub: str, resource_id: str) -> dict | None`
  - `async def list_for(member_sub: str) -> list[dict]`
  - `async def update_filters(member_sub: str, resource_id: str, filters: dict, expected_revision: int) -> dict | None`
  - `async def delete(member_sub: str, resource_id: str) -> bool`
  - Rows carry `{"id","kind","title","recipe","filters","revision","updated_at"}`.

- [ ] **Step 1: Write the failing store tests**

Create `tests/test_widgets_store.py`:

```python
"""tests/test_widgets_store.py"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve")
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_widget_resource")
    yield p
    await db.close_pool()


RECIPE = {"sources": [{"type": "records", "collection": "alpha.thing"}]}


async def test_create_and_get_round_trip(pool):
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {"days": 30})
    fetched = await store.get("sub-noah", created["id"])

    assert fetched["kind"] == "chart"
    assert fetched["title"] == "Alpha"
    assert fetched["recipe"] == RECIPE
    assert fetched["revision"] == 1


async def test_a_foreign_resource_is_invisible(pool):
    """A resource id is a locator, never authorization."""
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {})

    assert await store.get("sub-kendra", created["id"]) is None
    assert await store.list_for("sub-kendra") == []


async def test_updating_filters_bumps_the_revision(pool):
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {"days": 30})
    updated = await store.update_filters(
        "sub-noah", created["id"], {"days": 7}, expected_revision=1
    )

    assert updated["filters"] == {"days": 7}
    assert updated["revision"] == 2


async def test_a_stale_revision_is_refused(pool):
    """Two devices editing filters must not silently clobber each other."""
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {"days": 30})
    await store.update_filters("sub-noah", created["id"], {"days": 7}, expected_revision=1)

    stale = await store.update_filters(
        "sub-noah", created["id"], {"days": 90}, expected_revision=1
    )

    assert stale is None
    current = await store.get("sub-noah", created["id"])
    assert current["filters"] == {"days": 7}


async def test_a_foreign_member_cannot_update_or_delete(pool):
    from eve.widgets import store

    created = await store.create("sub-noah", "chart", "Alpha", RECIPE, {})

    assert await store.update_filters(
        "sub-kendra", created["id"], {"days": 1}, expected_revision=1
    ) is None
    assert await store.delete("sub-kendra", created["id"]) is False
    assert await store.get("sub-noah", created["id"]) is not None


async def test_delete_removes_only_the_named_resource(pool):
    from eve.widgets import store

    first = await store.create("sub-noah", "chart", "A", RECIPE, {})
    await store.create("sub-noah", "chart", "B", RECIPE, {})

    assert await store.delete("sub-noah", first["id"]) is True
    assert [r["title"] for r in await store.list_for("sub-noah")] == ["B"]
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_widgets_store.py -m integration -v`
Expected: FAIL. The table does not exist.

- [ ] **Step 3: Write the migration**

Create `alembic/versions/0010_eve_widget_resource.py`:

```python
"""One row per saved, refreshable widget.

Revision ID: 0010_eve_widget_resource
Revises: 0009_eve_record

`recipe` is the declarative read the snapshot route executes: which sources
to read and how to shape them. It is jsonb and validated in Python
(`eve.widgets.recipe`) rather than by columns, because the set of legal
sources grows and a migration per source is the per-domain cost this whole
feature exists to avoid.

`revision` is optimistic concurrency. Two devices can edit the same widget's
filters, and a slow refresh must not be able to overwrite a newer choice.

`kind` is the renderable family (`chart`), NOT a domain. Nothing here knows
what the recipe reads.
"""
from alembic import op

revision = "0010_eve_widget_resource"
down_revision = "0009_eve_record"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_widget_resource (
          id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub  text        NOT NULL,
          kind        text        NOT NULL,
          title       text        NOT NULL,
          recipe      jsonb       NOT NULL,
          filters     jsonb       NOT NULL DEFAULT '{}'::jsonb,
          revision    bigint      NOT NULL DEFAULT 1,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX eve_widget_resource_member"
        " ON eve_widget_resource (member_sub, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_widget_resource")
```

- [ ] **Step 4: Write the store**

Create `src/eve/widgets/__init__.py` (empty file).

Create `src/eve/widgets/store.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_widgets_store.py -m integration -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0010_eve_widget_resource.py src/eve/widgets/ tests/test_widgets_store.py
git commit -m "feat(widgets): owner-scoped resource store with optimistic revisions"
```

---

## Task 5: Recipe validation

A recipe is the only thing a model authors that later executes without a model. It is therefore the security boundary, and it is pure so it can be tested exhaustively.

**Files:**
- Create: `src/eve/widgets/recipe.py`
- Create: `tests/test_widgets_recipe.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `KINDS: frozenset[str]` containing exactly `{"chart"}`
  - `SOURCE_TYPES: frozenset[str]` containing exactly `{"records", "health"}`
  - `SOURCE_PERMISSIONS: dict[str, str | None]` mapping source type to a required permission or `None`
  - `def validate(recipe: object) -> str | None` returning a diagnostic code or `None`
  - `def validate_filters(filters: object) -> str | None`
  - `def required_permissions(recipe: dict) -> list[str]`

- [ ] **Step 1: Write the failing validation tests**

Create `tests/test_widgets_recipe.py`:

```python
"""A recipe is executed later with no model in the loop, so everything it is
allowed to say has to be decided here."""
from __future__ import annotations


def _recipe(**overrides) -> dict:
    recipe = {
        "sources": [{"type": "records", "collection": "alpha.thing"}],
        "metric": {"op": "count"},
    }
    recipe.update(overrides)
    return recipe


def test_a_minimal_records_recipe_is_valid():
    from eve.widgets import recipe

    assert recipe.validate(_recipe()) is None


def test_a_health_source_is_valid():
    from eve.widgets import recipe

    assert recipe.validate(
        _recipe(sources=[{"type": "health", "metric": "activity"}])
    ) is None


def test_an_unknown_source_type_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate(
        _recipe(sources=[{"type": "http", "url": "https://example.com"}])
    ) == "source-type"


def test_a_recipe_cannot_name_a_member():
    """Identity comes from the authenticated principal, never the recipe."""
    from eve.widgets import recipe

    assert recipe.validate(
        _recipe(sources=[{
            "type": "records",
            "collection": "alpha.thing",
            "member_sub": "sub-kendra",
        }])
    ) == "source-schema"


def test_a_records_source_needs_a_collection():
    from eve.widgets import recipe

    assert recipe.validate(
        _recipe(sources=[{"type": "records"}])
    ) == "source-schema"


def test_a_recipe_with_no_sources_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate(_recipe(sources=[])) == "sources"


def test_too_many_sources_are_rejected():
    from eve.widgets import recipe

    sources = [
        {"type": "records", "collection": f"c{i}"} for i in range(recipe.MAX_SOURCES + 1)
    ]
    assert recipe.validate(_recipe(sources=sources)) == "sources"


def test_an_unknown_metric_op_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate(_recipe(metric={"op": "exfiltrate"})) == "metric"


def test_a_sum_metric_needs_a_field():
    from eve.widgets import recipe

    assert recipe.validate(_recipe(metric={"op": "sum"})) == "metric"
    assert recipe.validate(_recipe(metric={"op": "sum", "field": "weight"})) is None


def test_a_non_dict_recipe_is_rejected():
    from eve.widgets import recipe

    assert recipe.validate("nonsense") == "recipe"
    assert recipe.validate(None) == "recipe"


def test_filters_accept_a_bounded_window():
    from eve.widgets import recipe

    assert recipe.validate_filters({"days": 30}) is None
    assert recipe.validate_filters({"days": 0}) == "filters"
    assert recipe.validate_filters({"days": recipe.MAX_DAYS + 1}) == "filters"
    assert recipe.validate_filters({"days": "thirty"}) == "filters"


def test_filters_reject_unknown_keys():
    from eve.widgets import recipe

    assert recipe.validate_filters({"exec": "rm -rf"}) == "filters"


def test_required_permissions_are_per_source():
    """A recipe reading only the member's own records needs nothing extra."""
    from eve.widgets import recipe

    assert recipe.required_permissions(_recipe()) == []
    assert recipe.required_permissions(
        _recipe(sources=[{"type": "health", "metric": "activity"}])
    ) == ["health"]


def test_no_kind_is_domain_specific():
    from eve.widgets import recipe

    assert recipe.KINDS == frozenset({"chart"})
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_widgets_recipe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.widgets.recipe'`.

- [ ] **Step 3: Write the validator**

Create `src/eve/widgets/recipe.py`:

```python
"""What a saved widget is allowed to say.

A recipe is authored once, by a model, and then executed on every refresh
with no model in the loop and no human reading it. That makes this module the
security boundary for the whole feature: anything it accepts runs
indefinitely.

So the vocabulary is closed and small. A source names a KIND of read
(`records`, `health`), never a URL, a tool name, a SQL fragment, or a
member. Identity is supplied by the authenticated route at execution time,
which is why a recipe that tries to name a member is rejected outright rather
than having the field ignored.

Pure module: no I/O, no database, no LangGraph.
"""

from __future__ import annotations

KINDS = frozenset({"chart"})

# A source type maps to one audited reader in `eve.widgets.resolve`. Adding an
# external system means adding a reader here and in that module - deliberately
# a code change with a review, because credentials and normalisation cannot be
# authored by a model. Adding a new WIDGET costs nothing.
SOURCE_TYPES = frozenset({"records", "health"})

# Permission required per source type, per the spec: reading the member's own
# records needs nothing beyond being that member.
SOURCE_PERMISSIONS: dict[str, str | None] = {
    "records": None,
    "health": "health",
}

HEALTH_METRICS = frozenset({"recovery", "sleep", "activity"})
METRIC_OPS = frozenset({"count", "sum", "avg", "max"})
FILTER_KEYS = frozenset({"days", "sources", "field", "groupBy"})

MAX_SOURCES = 4
MAX_DAYS = 3650
MAX_NAME = 128


def validate(candidate: object) -> str | None:
    """`None` when `candidate` is a legal recipe, else a diagnostic code."""
    if not isinstance(candidate, dict):
        return "recipe"

    sources = candidate.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        return "sources"
    for source in sources:
        error = _validate_source(source)
        if error:
            return error

    return _validate_metric(candidate.get("metric"))


def _validate_source(source: object) -> str | None:
    if not isinstance(source, dict):
        return "source-schema"
    kind = source.get("type")
    if kind not in SOURCE_TYPES:
        return "source-type"

    allowed = {"type", "collection"} if kind == "records" else {"type", "metric"}
    if set(source) - allowed:
        # Catches `member_sub`, `url`, `token` and every other smuggled key.
        return "source-schema"

    if kind == "records":
        collection = source.get("collection")
        if not isinstance(collection, str) or not 0 < len(collection) <= MAX_NAME:
            return "source-schema"
    else:
        if source.get("metric") not in HEALTH_METRICS:
            return "source-schema"
    return None


def _validate_metric(metric: object) -> str | None:
    if not isinstance(metric, dict):
        return "metric"
    op = metric.get("op")
    if op not in METRIC_OPS:
        return "metric"
    if set(metric) - {"op", "field"}:
        return "metric"
    if op == "count":
        return None
    field = metric.get("field")
    if not isinstance(field, str) or not 0 < len(field) <= MAX_NAME:
        return "metric"
    return None


def validate_filters(candidate: object) -> str | None:
    """Filters are member-supplied on every refresh, so they are bounded
    independently of the recipe that was authored once."""
    if not isinstance(candidate, dict):
        return "filters"
    if set(candidate) - FILTER_KEYS:
        return "filters"

    if "days" in candidate:
        days = candidate["days"]
        if isinstance(days, bool) or not isinstance(days, int):
            return "filters"
        if not 1 <= days <= MAX_DAYS:
            return "filters"

    if "sources" in candidate:
        chosen = candidate["sources"]
        if not isinstance(chosen, list):
            return "filters"
        if any(entry not in SOURCE_TYPES for entry in chosen):
            return "filters"

    for key in ("field", "groupBy"):
        if key in candidate:
            value = candidate[key]
            if not isinstance(value, str) or not 0 < len(value) <= MAX_NAME:
                return "filters"
    return None


def required_permissions(recipe: dict) -> list[str]:
    """Permissions this recipe needs, derived from the sources it reads.

    Per source rather than per widget kind: two charts can need different
    permissions, and the kind says nothing about what is being read.
    """
    needed = {
        SOURCE_PERMISSIONS.get(source.get("type"))
        for source in recipe.get("sources", [])
        if isinstance(source, dict)
    }
    return sorted(permission for permission in needed if permission)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_widgets_recipe.py -v`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add src/eve/widgets/recipe.py tests/test_widgets_recipe.py
git commit -m "feat(widgets): closed recipe vocabulary with per-source permissions"
```

---

## Task 6: The resolver

Turns a recipe plus filters into a snapshot. Pure over injected readers, so every branch is unit-testable without a database or a network.

**Files:**
- Create: `src/eve/widgets/resolve.py`
- Create: `tests/test_widgets_resolve.py`

**Interfaces:**
- Consumes: `eve.widgets.recipe` constants; `eve.records.store.query`; `eve.tools_client.invoke`.
- Produces: `async def snapshot(resource: dict, member_sub: str, *, read_records=None, read_health=None) -> dict` returning `{"resourceId","kind","revision","generatedAt","filters","view":{"components","data"},"sources":{"partial":bool,"errors":[...]}}`.

- [ ] **Step 1: Write the failing resolver tests**

Create `tests/test_widgets_resolve.py`:

```python
"""The resolver is what makes a refresh cost no model call. It is pure over
injected readers so every partial-failure branch is reachable in a unit test."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

RESOURCE = {
    "id": "res-1",
    "kind": "chart",
    "title": "Alpha",
    "recipe": {
        "sources": [{"type": "records", "collection": "alpha.thing"}],
        "metric": {"op": "count"},
    },
    "filters": {"days": 30},
    "revision": 3,
}


def _records(count: int):
    now = datetime.now(timezone.utc)

    async def read(member_sub, collection, since=None, until=None, limit=500):
        return [
            {"occurred_at": now - timedelta(days=i), "payload": {"weight": 100 + i}}
            for i in range(count)
        ]

    return read


async def _no_health(member_sub, metric, days):
    raise AssertionError("health must not be read for a records-only recipe")


async def test_a_records_recipe_produces_points(monkeypatch):
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(3), read_health=_no_health
    )

    assert snapshot["resourceId"] == "res-1"
    assert snapshot["revision"] == 3
    assert snapshot["sources"]["partial"] is False
    assert len(snapshot["view"]["data"]["points"]) == 3


async def test_the_snapshot_view_is_a_valid_surface_tree(monkeypatch):
    """The client validates the whole tree before swapping it in, so an
    invalid tree here is an invisible widget."""
    from eve.ui import protocol
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(2), read_health=_no_health
    )

    operation = {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": "sf-1",
            "catalogId": "column",
            "catalogVersion": protocol.CATALOG_VERSION,
            "components": snapshot["view"]["components"],
            "data": snapshot["view"]["data"],
            "localState": {},
        },
    }
    assert protocol.validate_operation(operation) is None


async def test_an_empty_collection_says_so_rather_than_charting_nothing():
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(0), read_health=_no_health
    )

    rendered = str(snapshot["view"]["components"])
    assert "Nothing recorded" in rendered
    assert snapshot["view"]["data"]["points"] == []


async def test_a_failing_source_is_partial_not_fatal():
    from eve.widgets import resolve

    async def boom(*args, **kwargs):
        raise RuntimeError("upstream exploded")

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=boom, read_health=_no_health
    )

    assert snapshot["sources"]["partial"] is True
    assert snapshot["sources"]["errors"] == [{"source": "records", "reason": "unavailable"}]


async def test_a_failing_source_never_leaks_the_upstream_message():
    """Public errors are sanitized; the raw string could carry anything."""
    from eve.widgets import resolve

    async def boom(*args, **kwargs):
        raise RuntimeError("psql://user:hunter2@db/eve exploded")

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=boom, read_health=_no_health
    )

    assert "hunter2" not in str(snapshot)


async def test_the_reader_is_called_with_the_authenticated_member():
    from eve.widgets import resolve

    seen = {}

    async def read(member_sub, collection, since=None, until=None, limit=500):
        seen["member_sub"] = member_sub
        seen["collection"] = collection
        return []

    await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=read, read_health=_no_health
    )

    assert seen == {"member_sub": "sub-noah", "collection": "alpha.thing"}


async def test_a_sum_metric_sums_the_named_field():
    from eve.widgets import resolve

    resource = {**RESOURCE, "recipe": {
        **RESOURCE["recipe"], "metric": {"op": "sum", "field": "weight"},
    }}

    snapshot = await resolve.snapshot(
        resource, "sub-noah", read_records=_records(2), read_health=_no_health
    )

    # 100 + 101, bucketed by day then summed.
    assert sum(p["value"] for p in snapshot["view"]["data"]["points"]) == 201


async def test_a_missing_numeric_field_is_skipped_not_zeroed():
    """An unsupported value is null, never zero (spec)."""
    from eve.widgets import resolve

    async def read(member_sub, collection, since=None, until=None, limit=500):
        return [{"occurred_at": datetime.now(timezone.utc), "payload": {"other": 1}}]

    resource = {**RESOURCE, "recipe": {
        **RESOURCE["recipe"], "metric": {"op": "sum", "field": "weight"},
    }}

    snapshot = await resolve.snapshot(
        resource, "sub-noah", read_records=read, read_health=_no_health
    )

    assert snapshot["view"]["data"]["points"] == []


async def test_a_health_recipe_reads_health():
    from eve.widgets import resolve

    resource = {**RESOURCE, "recipe": {
        "sources": [{"type": "health", "metric": "activity"}],
        "metric": {"op": "sum", "field": "active_calories"},
    }}

    async def read_health(member_sub, metric, days):
        assert member_sub == "sub-noah"
        assert metric == "activity"
        return [{"date": "2026-09-01", "active_calories": 500}]

    snapshot = await resolve.snapshot(
        resource, "sub-noah", read_records=_records(0), read_health=read_health
    )

    assert snapshot["view"]["data"]["points"] == [
        {"label": "2026-09-01", "value": 500, "source": "health"}
    ]


async def test_filters_narrow_the_window():
    from eve.widgets import resolve

    seen = {}

    async def read(member_sub, collection, since=None, until=None, limit=500):
        seen["since"] = since
        return []

    resource = {**RESOURCE, "filters": {"days": 7}}
    await resolve.snapshot(
        resource, "sub-noah", read_records=read, read_health=_no_health
    )

    age = datetime.now(timezone.utc) - seen["since"]
    assert 6 <= age.days <= 7


async def test_the_snapshot_carries_an_inline_range_control():
    """The spec's inline filters: the control ships IN the snapshot, so a
    widget the model authored once stays adjustable without re-authoring."""
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(1), read_health=_no_health
    )

    root = snapshot["view"]["components"][0]
    control = next(c for c in root["children"] if c["type"] == "segmentedSelection")
    assert control["properties"]["actionId"] == "widget.setRange"


async def test_the_range_control_shows_the_persisted_selection():
    """It reflects server state, not a local guess that can drift from it."""
    from eve.widgets import resolve

    resource = {**RESOURCE, "filters": {"days": 7}}
    snapshot = await resolve.snapshot(
        resource, "sub-noah", read_records=_records(1), read_health=_no_health
    )

    root = snapshot["view"]["components"][0]
    control = next(c for c in root["children"] if c["type"] == "segmentedSelection")
    assert control["properties"]["selected"] == "7"


async def test_an_empty_snapshot_still_carries_the_control():
    """Otherwise a widget whose collection is empty can never be widened to a
    range that would have found something."""
    from eve.widgets import resolve

    snapshot = await resolve.snapshot(
        RESOURCE, "sub-noah", read_records=_records(0), read_health=_no_health
    )

    root = snapshot["view"]["components"][0]
    assert any(c["type"] == "segmentedSelection" for c in root["children"])
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_widgets_resolve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.widgets.resolve'`.

- [ ] **Step 3: Write the resolver**

Create `src/eve/widgets/resolve.py`:

```python
"""Recipe plus filters to a rendered snapshot, with no model in the loop.

This is the module that makes "refresh without spending a model call" true.
It reads only through the two injected readers, which is what lets every
partial-failure branch be a unit test rather than a live-service test.

The emitted `components` tree uses only `assistant-ui/1.0` catalog types, so
the client validates and renders it with the same code path a chat surface
takes. `data.points` is the chart's bound series.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from eve.records import store as record_store
from eve.tools_client import invoke
from eve.widgets import recipe as recipe_rules

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
MAX_POINTS = 180


async def _default_read_records(
    member_sub: str, collection: str, since=None, until=None, limit=500
):
    return await record_store.query(
        member_sub, collection, since=since, until=until, limit=limit
    )


async def _default_read_health(member_sub: str, metric: str, days: int):
    result = await invoke(
        f"health.get_{metric}", {"member_sub": member_sub, "days": days}
    )
    return result if isinstance(result, list) else []


async def snapshot(
    resource: dict,
    member_sub: str,
    *,
    read_records=None,
    read_health=None,
) -> dict:
    """The full resource-level snapshot the client renders.

    Never raises. A source that fails contributes nothing and is reported in
    `sources.errors`, because a widget showing three of four series is more
    useful than one showing an error, and the client labels the gap.
    """
    read_records = read_records or _default_read_records
    read_health = read_health or _default_read_health

    spec = resource.get("recipe") or {}
    filters = resource.get("filters") or {}
    days = filters.get("days", DEFAULT_DAYS)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    metric = spec.get("metric") or {"op": "count"}

    points: list[dict] = []
    errors: list[dict] = []

    for source in spec.get("sources", []):
        kind = source.get("type")
        try:
            if kind == "records":
                rows = await read_records(
                    member_sub, source["collection"], since=since
                )
                points.extend(_points_from_records(rows, metric))
            elif kind == "health":
                rows = await read_health(member_sub, source["metric"], days)
                points.extend(_points_from_health(rows, metric))
        except Exception:
            # Structural diagnostics only. The upstream message can carry a
            # DSN, a token, or a member's data, and this snapshot is returned
            # over HTTP to a client.
            logger.warning("widget source %s failed", kind, exc_info=True)
            errors.append({"source": kind, "reason": "unavailable"})

    points.sort(key=lambda point: point["label"])
    points = points[-MAX_POINTS:]

    return {
        "resourceId": resource["id"],
        "kind": resource["kind"],
        "revision": resource["revision"],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "filters": filters,
        "view": {
            "components": _components(resource, points, errors),
            "data": {"points": points, "title": resource.get("title", "")},
        },
        "sources": {"partial": bool(errors), "errors": errors},
    }


def _points_from_records(rows: list[dict], metric: dict) -> list[dict]:
    """Bucket by local day, then apply the metric.

    A value that cannot be read as a number is SKIPPED, never coerced to
    zero: a missing measurement and a measured zero are different facts, and
    a chart that conflates them is quietly wrong.
    """
    buckets: dict[str, list[float]] = {}
    for row in rows:
        occurred = row.get("occurred_at")
        label = occurred.date().isoformat() if hasattr(occurred, "date") else str(occurred)
        if metric["op"] == "count":
            buckets.setdefault(label, []).append(1.0)
            continue
        raw = (row.get("payload") or {}).get(metric.get("field"))
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        buckets.setdefault(label, []).append(float(raw))

    return [
        {"label": label, "value": _apply(metric["op"], values), "source": "records"}
        for label, values in buckets.items()
        if values
    ]


def _points_from_health(rows: list[dict], metric: dict) -> list[dict]:
    field = metric.get("field")
    points = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("date", ""))
        raw = row.get(field) if field else 1
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        points.append({"label": label, "value": float(raw), "source": "health"})
    return points


def _apply(op: str, values: list[float]) -> float:
    if op == "count":
        return float(len(values))
    if op == "sum":
        return float(sum(values))
    if op == "avg":
        return float(sum(values) / len(values))
    return float(max(values))


def _components(resource: dict, points: list[dict], errors: list[dict]) -> list[dict]:
    """The rendered tree. Catalog types only.

    An empty series renders a sentence rather than a blank chart: a chart with
    no bars looks broken, and the most likely cause is a collection name that
    never matched anything, which the member can act on.
    """
    children: list[dict] = [_range_control(resource)]
    if points:
        children.append({"id": "chart", "type": "chart", "properties": {
            "points": "$data.points",
        }})
    else:
        children.append({"id": "empty", "type": "text", "properties": {
            "text": "Nothing recorded yet for this widget.",
        }})
    if errors:
        children.append({"id": "partial", "type": "badge", "properties": {
            "label": "Some data unavailable",
        }})

    return [{
        "id": "root",
        "type": "card",
        "properties": {"title": resource.get("title", "")},
        "children": children,
    }]


def _range_control(resource: dict) -> dict:
    """The inline filter, rendered as part of the snapshot.

    It is a `segmentedSelection` whose `actionId` is `widget.setRange`, an id
    the CHAT protocol does not allow - `assistant-ui/1.0` allowlists only
    `surface.submit`. That is deliberate and it is why the widget host
    intercepts this id before the generic renderer ever dispatches it: a
    widget filter is a resource action, not a chat turn, and the two must not
    share a channel.

    The selected option comes from the resource's own filters, so the control
    always shows the state the server actually holds rather than a local guess
    that can drift from it.
    """
    filters = resource.get("filters") or {}
    selected = str(filters.get("days", DEFAULT_DAYS))
    return {
        "id": "range",
        "type": "segmentedSelection",
        "properties": {
            "options": ["7", "30", "90"],
            "selected": selected,
            "actionId": "widget.setRange",
        },
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_widgets_resolve.py -v`
Expected: PASS (13 tests). If `test_the_snapshot_view_is_a_valid_surface_tree` fails on the `chart` type, that is Task 7's job; run it again after Task 7.

- [ ] **Step 5: Commit**

```bash
git add src/eve/widgets/resolve.py tests/test_widgets_resolve.py
git commit -m "feat(widgets): deterministic recipe resolver with partial-failure reporting"
```

---

## Task 7: The chart component in the protocol

The catalog is declared in five places by design. This task adds `chart` to the two server copies; the client plan adds the other three.

**Files:**
- Modify: `src/eve/ui/protocol.py`
- Modify: `skills/build-a-ui/SKILL.md`
- Modify: `tests/test_ui_protocol.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `"chart"` in `protocol.CATALOG_IDS`; `_ALLOWED_PROPERTIES["chart"] == frozenset({"points", "label"})`.

- [ ] **Step 1: Write the failing protocol tests**

Append to `tests/test_ui_protocol.py`:

```python
def test_chart_is_in_the_catalog():
    assert "chart" in protocol.CATALOG_IDS


def test_a_chart_binds_its_points_from_data():
    components = [{
        "id": "c", "type": "chart", "properties": {"points": "$data.points"},
    }]
    assert protocol.validate_operation(_surface(components=components)) is None


def test_a_chart_rejects_an_undeclared_property():
    components = [{
        "id": "c", "type": "chart",
        "properties": {"points": "$data.points", "onTap": "evil"},
    }]
    assert protocol.validate_operation(
        _surface(components=components)
    ) == "component-schema"


def test_a_chart_rejects_a_malformed_binding():
    components = [{
        "id": "c", "type": "chart", "properties": {"points": "$data"},
    }]
    assert protocol.validate_operation(_surface(components=components)) == "binding"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_ui_protocol.py -k chart -v`
Expected: FAIL. `chart` is not in `CATALOG_IDS`.

- [ ] **Step 3: Add `chart` to the protocol**

In `src/eve/ui/protocol.py`, add `"chart"` to the `CATALOG_IDS` frozenset, and add this entry to `_ALLOWED_PROPERTIES`:

```python
    # `points` is always a `$data.` binding to a list of
    # {label, value, source} objects; a literal series would put the whole
    # dataset in the component tree, which the 48KiB definition ceiling and
    # the patch path both assume it is not.
    "chart": frozenset({"points", "label"}),
```

Then, in `_validate_property`, add `points` handling before the final `return "component-schema"`:

```python
    if key == "points":
        # Binding-only: see the catalog comment above.
        if not isinstance(value, str):
            return "component-schema"
        return "binding" if not _BINDING.match(value) else None
```

Add `"label"` to `_STRING_PROPERTIES` only if it is not already there (it is, via `badge`/`expandable`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_protocol.py -v`
Expected: PASS, including the four new tests.

- [ ] **Step 5: Update the UI skill's catalog list**

In `skills/build-a-ui/SKILL.md`, add one line to the catalog list, after `badge`:

```markdown
- `chart`: label, points
```

And add this paragraph after the catalog list:

```markdown
`chart.points` must be a `$data.` binding to a list of `{label, value}`
objects, never a literal list: the series belongs in the surface's data, not
in its component tree.
```

- [ ] **Step 6: Run the whole unit suite and the resolver test**

Run: `uv run pytest`
Expected: PASS, including `tests/test_widgets_resolve.py::test_the_snapshot_view_is_a_valid_surface_tree`.

- [ ] **Step 7: Commit**

```bash
git add src/eve/ui/protocol.py skills/build-a-ui/SKILL.md tests/test_ui_protocol.py
git commit -m "feat(ui): add the chart component to the catalog"
```

---

## Task 8: The authoring tool

The one place a model creates a widget. It injects identity, validates the recipe, and checks permissions before anything is stored.

**Files:**
- Create: `src/eve/widgets/tools.py`
- Create: `tests/test_widgets_tools.py`
- Modify: `src/eve/graph.py`

**Interfaces:**
- Consumes: `eve.widgets.recipe.validate/validate_filters/required_permissions`, `eve.widgets.store.create`, `eve.specialists.permissions.permission_denial`.
- Produces: `save_widget` LangChain tool returning a string that names the created resource id.

- [ ] **Step 1: Write the failing tool tests**

Create `tests/test_widgets_tools.py`:

```python
"""`save_widget` is the only way a widget is created, so every guard that
matters is here."""
from __future__ import annotations

CONFIG = {
    "configurable": {
        "member": {"sub": "sub-noah", "permissions": ["health"]},
    }
}
NO_PERMS = {"configurable": {"member": {"sub": "sub-noah", "permissions": []}}}

RECIPE = {
    "sources": [{"type": "records", "collection": "alpha.thing"}],
    "metric": {"op": "count"},
}


def _call(tool, args, config=CONFIG):
    return tool.ainvoke(
        {"type": "tool_call", "name": tool.name, "args": args, "id": "t1"},
        config=config,
    )


async def test_saving_a_widget_stores_it_for_the_authenticated_member(monkeypatch):
    from eve.widgets import tools

    seen = {}

    async def fake_create(member_sub, kind, title, recipe, filters):
        seen.update(member_sub=member_sub, kind=kind, title=title, recipe=recipe)
        return {"id": "res-1", "revision": 1}

    monkeypatch.setattr(tools.store, "create", fake_create)

    result = await _call(
        tools.save_widget,
        {"title": "Alpha", "kind": "chart", "recipe": RECIPE},
    )

    assert seen["member_sub"] == "sub-noah"
    assert seen["kind"] == "chart"
    assert "res-1" in result.content


async def test_an_invalid_recipe_is_refused_with_a_diagnostic(monkeypatch):
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store an invalid recipe")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.save_widget,
        {
            "title": "Bad",
            "kind": "chart",
            "recipe": {"sources": [{"type": "http", "url": "https://x"}]},
        },
    )

    assert "source-type" in result.content


async def test_an_unknown_kind_is_refused(monkeypatch):
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store an unknown kind")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.save_widget, {"title": "X", "kind": "dashboard", "recipe": RECIPE}
    )

    assert "kind" in result.content.lower()


async def test_a_health_recipe_requires_the_health_permission(monkeypatch):
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store without permission")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.save_widget,
        {
            "title": "H",
            "kind": "chart",
            "recipe": {
                "sources": [{"type": "health", "metric": "activity"}],
                "metric": {"op": "count"},
            },
        },
        config=NO_PERMS,
    )

    assert "permission" in result.content.lower()


async def test_a_records_only_recipe_needs_no_extra_permission(monkeypatch):
    """Reading your own records is the least privileged thing there is."""
    from eve.widgets import tools

    async def fake_create(member_sub, kind, title, recipe, filters):
        return {"id": "res-2", "revision": 1}

    monkeypatch.setattr(tools.store, "create", fake_create)

    result = await _call(
        tools.save_widget,
        {"title": "Alpha", "kind": "chart", "recipe": RECIPE},
        config=NO_PERMS,
    )

    assert "res-2" in result.content


async def test_an_ambient_turn_cannot_save_a_widget(monkeypatch):
    """The ambient token can impersonate any member (spec risk)."""
    from eve.widgets import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("ambient turns must not author widgets")

    monkeypatch.setattr(tools.store, "create", unreachable)

    ambient = {
        "configurable": {
            "member": {"sub": "sub-noah", "permissions": []},
            "is_ambient": True,
        }
    }
    result = await _call(
        tools.save_widget,
        {"title": "X", "kind": "chart", "recipe": RECIPE},
        config=ambient,
    )

    assert "cannot" in result.content.lower()


async def test_storage_failure_degrades_to_a_string(monkeypatch):
    from eve.widgets import tools

    async def boom(*args, **kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(tools.store, "create", boom)

    result = await _call(
        tools.save_widget, {"title": "A", "kind": "chart", "recipe": RECIPE}
    )

    assert "error" in result.content.lower()
    assert "postgres" not in result.content
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_widgets_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.widgets.tools'`.

- [ ] **Step 3: Write the tool**

Create `src/eve/widgets/tools.py`:

```python
"""The one way a widget is created.

Everything the model supplies is checked here, and everything it must NOT
supply - the owner, the credentials, the endpoints - is injected or absent by
construction. A recipe that passes this gate runs on every later refresh with
no model and no human in the loop, which is why the checks are in this order:
kind, then recipe shape, then permissions, then storage.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.specialists.permissions import permission_denial
from eve.widgets import recipe as recipe_rules, store

logger = logging.getLogger(__name__)

_DESCRIPTION = """Save a reusable widget the member can open later from Widgets.

The widget refreshes its own data every time it is opened, without asking you
again, so the `recipe` must say WHERE the data comes from rather than
containing the data itself.

`recipe` is {"sources": [...], "metric": {...}}. A source is either
{"type": "records", "collection": "<a collection you have recorded into>"} or
{"type": "health", "metric": "recovery"|"sleep"|"activity"}. A metric is
{"op": "count"} or {"op": "sum"|"avg"|"max", "field": "<payload field>"}.

Save a widget when the member wants to keep looking at something. Answer in
prose for a one-off question."""


@tool(description=_DESCRIPTION)
async def save_widget(
    title: str,
    kind: str,
    recipe: dict,
    config: RunnableConfig,
    filters: dict | None = None,
) -> str:
    configurable = config.get("configurable") or {}
    member = configurable.get("member") or {}
    member_sub = member["sub"]

    if configurable.get("is_ambient"):
        # An ambient turn is composed from a webhook payload, not spoken by
        # the member, and the ambient credential can impersonate anyone. It
        # cannot create a durable resource in someone's account.
        return "A widget cannot be created from an ambient turn."

    if kind not in recipe_rules.KINDS:
        legal = ", ".join(sorted(recipe_rules.KINDS))
        return f"Unknown widget kind {kind!r}. Legal kinds: {legal}."

    error = recipe_rules.validate(recipe)
    if error is not None:
        return (
            f"The widget recipe was rejected: {error}. "
            "A source is {\"type\": \"records\", \"collection\": ...} or "
            "{\"type\": \"health\", \"metric\": ...}; nothing else is legal."
        )

    chosen_filters = filters or {}
    filter_error = recipe_rules.validate_filters(chosen_filters)
    if filter_error is not None:
        return f"The widget filters were rejected: {filter_error}."

    for required in recipe_rules.required_permissions(recipe):
        denial = permission_denial(member.get("permissions", []), required)
        if denial:
            return denial

    try:
        created = await store.create(
            member_sub, kind, title, recipe, chosen_filters
        )
    except Exception as exc:
        logger.warning("save_widget failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    return (
        f"Saved the widget {title!r} (id {created['id']}). "
        "It appears under Widgets and refreshes itself when opened."
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_widgets_tools.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Bind the tool in the graph**

In `src/eve/graph.py`, add the import:

```python
from eve.widgets.tools import save_widget
```

Add `save_widget` to `_BASE_TOOLS` alongside the record tools from Task 3.

- [ ] **Step 6: Run the unit suite**

Run: `uv run pytest`
Expected: PASS. Update any graph test asserting an exact tool count.

- [ ] **Step 7: Commit**

```bash
git add src/eve/widgets/tools.py tests/test_widgets_tools.py src/eve/graph.py tests/test_graph.py
git commit -m "feat(widgets): the authoring tool, with every guard before storage"
```

---

## Task 9: The resource API

The routes that make refresh direct. Authentication comes from Aegra; ownership is enforced here.

**Files:**
- Create: `src/eve/widgets/app.py`
- Create: `tests/test_widgets_app.py`
- Modify: `aegra.json`

**Interfaces:**
- Consumes: `eve.widgets.store`, `eve.widgets.resolve.snapshot`, `eve.widgets.recipe`.
- Produces: FastAPI `app` exposing `GET /provider-resources/v1/capabilities`, `GET /provider-resources/v1/resources`, `GET /provider-resources/v1/resources/{resource_id}/snapshot`, `POST /provider-resources/v1/resources/{resource_id}/actions`, `DELETE /provider-resources/v1/resources/{resource_id}`.

- [ ] **Step 1: Write the failing route tests**

Create `tests/test_widgets_app.py`:

```python
"""Routes. Authentication is Aegra's; ownership is ours, and the difference
is the whole point of these tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from eve.widgets import app as widgets_app

    # Stand in for Aegra's require_auth, which is what production uses.
    widgets_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": ["health"]}
    )
    yield TestClient(widgets_app.app)
    widgets_app.app.dependency_overrides.clear()


def test_capabilities_names_the_supported_kinds(client):
    response = client.get("/provider-resources/v1/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["protocol"] == "provider-resource/1.0"
    assert "chart" in body["kinds"]


def test_listing_returns_only_this_members_resources(client, monkeypatch):
    from eve.widgets import app as widgets_app

    seen = {}

    async def fake_list_for(member_sub):
        seen["member_sub"] = member_sub
        return [{"id": "res-1", "kind": "chart", "title": "A", "recipe": {},
                 "filters": {}, "revision": 1, "updated_at": None}]

    monkeypatch.setattr(widgets_app.store, "list_for", fake_list_for)

    response = client.get("/provider-resources/v1/resources")

    assert response.status_code == 200
    assert seen["member_sub"] == "sub-noah"


def test_a_foreign_resource_is_404_not_403(client, monkeypatch):
    """403 would confirm the id exists; 404 tells a prober nothing."""
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return None

    monkeypatch.setattr(widgets_app.store, "get", fake_get)

    response = client.get("/provider-resources/v1/resources/res-x/snapshot")

    assert response.status_code == 404


def test_a_snapshot_never_runs_the_graph(client, monkeypatch):
    """The whole point of the feature: refresh costs no model call."""
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [], "metric": {"op": "count"}},
                "filters": {}, "revision": 1}

    async def fake_snapshot(resource, member_sub, **kwargs):
        return {"resourceId": "res-1", "revision": 1, "view": {}, "sources": {}}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)

    response = client.get("/provider-resources/v1/resources/res-1/snapshot")

    assert response.status_code == 200
    assert response.json()["resourceId"] == "res-1"


def test_a_filters_action_persists_and_returns_a_fresh_snapshot(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [], "metric": {"op": "count"}},
                "filters": {"days": 30}, "revision": 1}

    async def fake_update(member_sub, resource_id, filters, expected_revision):
        assert filters == {"days": 7}
        return {**(await fake_get(member_sub, resource_id)),
                "filters": filters, "revision": 2}

    async def fake_snapshot(resource, member_sub, **kwargs):
        return {"resourceId": "res-1", "revision": resource["revision"],
                "filters": resource["filters"], "view": {}, "sources": {}}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.store, "update_filters", fake_update)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {"days": 7},
              "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert response.json()["revision"] == 2


def test_a_stale_revision_returns_409_with_the_current_snapshot(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [], "metric": {"op": "count"}},
                "filters": {"days": 7}, "revision": 2}

    async def fake_update(member_sub, resource_id, filters, expected_revision):
        return None

    async def fake_snapshot(resource, member_sub, **kwargs):
        return {"resourceId": "res-1", "revision": 2, "view": {}, "sources": {}}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.store, "update_filters", fake_update)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {"days": 90},
              "expectedRevision": 1},
    )

    assert response.status_code == 409
    assert response.json()["revision"] == 2


def test_an_unknown_action_type_is_rejected(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A", "recipe": {},
                "filters": {}, "revision": 1}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "resource.exfiltrate", "input": {}, "expectedRevision": 1},
    )

    assert response.status_code == 400


def test_invalid_filters_are_rejected_before_storage(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A", "recipe": {},
                "filters": {}, "revision": 1}

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not persist invalid filters")

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.store, "update_filters", unreachable)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {"days": 99999},
              "expectedRevision": 1},
    )

    assert response.status_code == 400


def test_a_snapshot_requires_the_recipes_permissions(client, monkeypatch):
    """Checked at the route too, not only at authoring time: permissions can
    be revoked after a widget was saved."""
    from eve.widgets import app as widgets_app

    widgets_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": []}
    )

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [{"type": "health", "metric": "activity"}],
                           "metric": {"op": "count"}},
                "filters": {}, "revision": 1}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)

    response = client.get("/provider-resources/v1/resources/res-1/snapshot")

    assert response.status_code == 403


def test_deleting_a_foreign_resource_is_404(client, monkeypatch):
    from eve.widgets import app as widgets_app

    async def fake_delete(member_sub, resource_id):
        return False

    monkeypatch.setattr(widgets_app.store, "delete", fake_delete)

    response = client.delete("/provider-resources/v1/resources/res-x")

    assert response.status_code == 404


def test_a_body_supplied_member_is_ignored(client, monkeypatch):
    """Identity is the authenticated principal's, never the request's."""
    from eve.widgets import app as widgets_app

    seen = {}

    async def fake_get(member_sub, resource_id):
        seen["member_sub"] = member_sub
        return None

    monkeypatch.setattr(widgets_app.store, "get", fake_get)

    client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {"days": 7},
              "expectedRevision": 1, "member_sub": "sub-kendra"},
    )

    assert seen["member_sub"] == "sub-noah"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest tests/test_widgets_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.widgets.app'`.

- [ ] **Step 3: Write the routes**

Create `src/eve/widgets/app.py`:

```python
"""The direct resource API: read a widget's data without a model call.

Mounted into Aegra through `aegra.json`'s `http.app`, with
`enable_custom_route_auth` so Aegra's own `require_auth` runs first and the
client reuses exactly the origin and bearer it already holds for LangGraph.

Authentication is Aegra's. **Authorization is ours**: Aegra's `@auth.on`
handlers scope threads and its store API, and they do not reach a custom
route, so every handler below resolves the member from the authenticated
principal and passes it into an owner-scoped query. A resource id is a
locator and never a capability.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel

from eve.specialists.permissions import permission_denial
from eve.widgets import recipe as recipe_rules, resolve, store

logger = logging.getLogger(__name__)

app = FastAPI(title="eve-provider-resources")

PREFIX = "/provider-resources/v1"
PROTOCOL = "provider-resource/1.0"

# Risk classes, per the spec. `safe` executes on tap; a `confirm` action needs
# the client's confirmation sheet first. Nothing outside this map is legal, so
# an action cannot be invented by an authored recipe.
ACTION_RISK = {"filters.replace": "safe"}

# The surface-level action id the inline range control carries
# (`eve.widgets.resolve._range_control`). The client maps it onto a
# `filters.replace` action rather than sending it verbatim, so the route's
# vocabulary stays one entry long; this constant exists so the two modules
# cannot drift on the spelling.
RANGE_ACTION_ID = "widget.setRange"


def current_member(request: Request) -> dict:
    """The authenticated principal, as Aegra's `require_auth` left it.

    Overridden in tests. In production `enable_custom_route_auth` has already
    rejected an unauthenticated request before this runs; the guard here is
    for a misconfiguration, where failing closed is the only safe answer.
    """
    user = request.scope.get("user")
    if user is None or not getattr(user, "identity", None):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {
        "sub": user.identity,
        "permissions": list(getattr(user, "permissions", []) or []),
    }


class ActionRequest(BaseModel):
    type: str
    input: dict = {}
    expectedRevision: int
    idempotencyKey: str | None = None

    # No `member_sub` field, deliberately: pydantic drops unknown keys, so a
    # body that carries one is ignored rather than trusted.


def _require_permissions(member: dict, resource: dict) -> None:
    for required in recipe_rules.required_permissions(resource.get("recipe") or {}):
        if permission_denial(member["permissions"], required):
            raise HTTPException(status_code=403, detail="forbidden")


async def _load(member: dict, resource_id: str) -> dict:
    resource = await store.get(member["sub"], resource_id)
    if resource is None:
        # Absent and foreign are the same answer: a 403 here would confirm
        # that someone else's id exists.
        raise HTTPException(status_code=404, detail="not found")
    return resource


@app.get(f"{PREFIX}/capabilities")
async def capabilities(member: dict = Depends(current_member)) -> dict:
    """What this deployment supports. A client that 404s here concludes the
    provider has no widget support at all; a transport failure means
    temporarily unavailable, which is a different thing entirely."""
    return {
        "protocol": PROTOCOL,
        "kinds": sorted(recipe_rules.KINDS),
        "sourceTypes": sorted(recipe_rules.SOURCE_TYPES),
        "actions": [
            {"type": name, "risk": risk} for name, risk in sorted(ACTION_RISK.items())
        ],
        "limits": {"maxDays": recipe_rules.MAX_DAYS},
    }


@app.get(f"{PREFIX}/resources")
async def list_resources(member: dict = Depends(current_member)) -> dict:
    resources = await store.list_for(member["sub"])
    return {"resources": [
        {
            "resourceId": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "revision": row["revision"],
        }
        for row in resources
    ]}


@app.get(f"{PREFIX}/resources/{{resource_id}}/snapshot")
async def snapshot(
    resource_id: str, member: dict = Depends(current_member)
) -> dict:
    resource = await _load(member, resource_id)
    # Re-checked here rather than trusted from authoring time: a permission
    # can be revoked after a widget was saved.
    _require_permissions(member, resource)
    return await resolve.snapshot(resource, member["sub"])


@app.post(f"{PREFIX}/resources/{{resource_id}}/actions")
async def run_action(
    resource_id: str,
    body: ActionRequest,
    member: dict = Depends(current_member),
) -> dict:
    resource = await _load(member, resource_id)

    if body.type not in ACTION_RISK:
        raise HTTPException(status_code=400, detail="unknown action")

    _require_permissions(member, resource)

    error = recipe_rules.validate_filters(body.input)
    if error is not None:
        raise HTTPException(status_code=400, detail=f"invalid filters: {error}")

    updated = await store.update_filters(
        member["sub"], resource_id, body.input, body.expectedRevision
    )
    if updated is None:
        # Somebody else moved first. Answer with the current snapshot so the
        # client can show fresh data instead of an error it cannot act on.
        current = await _load(member, resource_id)
        fresh = await resolve.snapshot(current, member["sub"])
        raise HTTPException(status_code=409, detail=fresh)

    return await resolve.snapshot(updated, member["sub"])


@app.delete(f"{PREFIX}/resources/{{resource_id}}", status_code=204)
async def delete_resource(
    resource_id: str, member: dict = Depends(current_member)
) -> None:
    if not await store.delete(member["sub"], resource_id):
        raise HTTPException(status_code=404, detail="not found")
```

Note on the 409 body: `HTTPException(detail=...)` nests the snapshot under
`detail`. The test asserts `response.json()["revision"]`, so add a small
exception handler at the bottom of the module to flatten it:

```python
from fastapi.responses import JSONResponse


@app.exception_handler(HTTPException)
async def _flatten(request: Request, exc: HTTPException) -> JSONResponse:
    """A 409 carries a whole snapshot, not a message. Returning it nested
    under `detail` would make the client unwrap conflicts differently from
    every other response."""
    if exc.status_code == 409 and isinstance(exc.detail, dict):
        return JSONResponse(status_code=409, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_widgets_app.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Mount the app in Aegra**

Replace `aegra.json` with:

```json
{
  "graphs": {
    "eve": "./src/eve/graph.py:graph"
  },
  "auth": {
    "path": "./src/eve/auth.py:auth"
  },
  "http": {
    "app": "./src/eve/widgets/app.py:app",
    "enable_custom_route_auth": true
  }
}
```

- [ ] **Step 6: Commit**

```bash
git add src/eve/widgets/app.py tests/test_widgets_app.py aegra.json
git commit -m "feat(widgets): authenticated resource API with owner-scoped lookups"
```

---

## Task 10: The live mount test

`enable_custom_route_auth` is version-specific and applies its dependency by walking routes after the core routers are added. This task proves the mount works and that health probes still answer, which is the spec's named Aegra-coupling risk.

**Files:**
- Create: `tests/test_widgets_integration.py`

**Interfaces:**
- Consumes: the mounted app from Task 9.
- Produces: nothing importable.

- [ ] **Step 1: Read the existing live-server fixture**

Open `tests/conftest.py` and find the fixture that starts `aegra serve` (used by `tests/test_integration.py`). Reuse it by name rather than starting a second server.

- [ ] **Step 2: Write the failing integration test**

Create `tests/test_widgets_integration.py`:

```python
"""The custom-route mount, against a real `aegra serve`.

Aegra's `enable_custom_route_auth` is version-specific and applies its auth
dependency by walking routes after the core routers are included, so both
halves of this need proving on the real server rather than a TestClient:
the widget routes demand a credential, and the health probes still answer
without one.
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


async def test_the_resource_api_requires_a_credential(aegra_server):
    async with httpx.AsyncClient(base_url=aegra_server) as client:
        response = await client.get("/provider-resources/v1/capabilities")

    assert response.status_code == 401


async def test_an_authenticated_member_reads_capabilities(aegra_server, dev_token):
    async with httpx.AsyncClient(base_url=aegra_server) as client:
        response = await client.get(
            "/provider-resources/v1/capabilities",
            headers={"Authorization": f"Bearer {dev_token}"},
        )

    assert response.status_code == 200
    assert "chart" in response.json()["kinds"]


async def test_health_probes_still_answer_without_a_credential(aegra_server):
    """`enable_custom_route_auth` walks every route, so this is the exact
    regression it can cause."""
    async with httpx.AsyncClient(base_url=aegra_server) as client:
        for path in ("/health", "/ok", "/healthz"):
            response = await client.get(path)
            if response.status_code != 404:
                assert response.status_code == 200, f"{path} needs a credential"


async def test_the_graph_still_serves_runs(aegra_server, dev_token):
    """The mount must not displace the Agent Protocol routes."""
    async with httpx.AsyncClient(base_url=aegra_server) as client:
        response = await client.get(
            "/assistants/eve", headers={"Authorization": f"Bearer {dev_token}"}
        )

    assert response.status_code == 200
```

If `conftest.py` has no `dev_token` fixture, add one that returns a token from `EVE_DEV_TOKENS`, matching how `tests/test_integration.py` authenticates.

- [ ] **Step 3: Run it**

Run: `docker compose -f docker-compose.test.yml up -d && uv run pytest tests/test_widgets_integration.py -m integration -v`
Expected: PASS (4 tests). If the health-probe test fails, set `enable_custom_route_auth` to `false` and apply `Depends(require_auth)` to the widget router explicitly instead, then re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/test_widgets_integration.py tests/conftest.py
git commit -m "test(widgets): pin the Aegra custom-route mount and its auth boundary"
```

---

## Task 11: Documentation

**Files:**
- Create: `docs/adr/0019-one-generic-record-store.md`
- Modify: `docs/architecture.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing importable.

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0019-one-generic-record-store.md`:

```markdown
# 19. One generic record store, and widgets are recipes over it

**Status:** Accepted
**Date:** 2026-09-14
**Relates to:** [ADR 0008](0008-authored-behaviour-is-memory.md), [ADR 0016](0016-eve-tools-owns-a-credential-table.md)

## Context

Reusable widgets need somewhere to read from. The obvious design gives each
domain its own table: workout sets here, reading sessions there, chores
somewhere else. That makes every new thing a member wants to track a
migration, a store module, a tool, and a release, which is precisely the
per-domain cost widgets exist to remove.

ADR 0008 already refused this shape once, storing authored rules and
procedures as `eve_memory` layers rather than building a store per kind.

## Decision

**One table, `eve_record`**: member, a free-form `collection` name, an opaque
jsonb `payload`, and `occurred_at`. A workout set, a book finished and a chore
done are the same row with a different collection string. Two tools,
`record_append` and `record_query`, are the only writers and readers; what a
domain MEANS lives in a skill, which is prose.

**A widget is a recipe over that store**, not code. `eve_widget_resource`
holds a validated declarative recipe naming allowlisted sources; the snapshot
route executes it with no model and no graph run. Adding a widget costs a
recipe. Adding an external SYSTEM still costs an audited reader, because
credentials and normalisation cannot be authored by a model.

Not `eve_memory`, despite the precedent: memory is prose with embeddings,
decay and salience, and reconstructing a numeric series over ninety days from
embedded sentences is lexical guesswork rather than aggregation. Not the
Aegra Store either: it is genuinely identity-namespaced but answers key
lookups, so every range query would fetch and filter in application code.
`occurred_at` as a real indexed column is the whole difference.

## Consequences

A new tracked domain is a collection name and a skill paragraph. No DDL, no
deploy, no client release.

The cost is that collection names are unconstrained, so a model can invent a
near-duplicate (`workout.set` versus `workouts`) and split a member's history
in two. `record_append` returns the collection's previously-seen field names
to steer consistency, and the skill tells the model to query before inventing
a name, but neither is enforcement: a divergent write is stored and visible
rather than rejected, because member-recorded data must never be lost to a
schema disagreement it cannot see.

The recipe validator (`eve.widgets.recipe`) becomes a security boundary of
the same weight as the sandbox AST check was NOT (ADR 0010): unlike that
checker, this one IS load-bearing, because a recipe executes forever after
with no human and no model in the loop. Its vocabulary is closed and its
tests assume a hostile author.
```

- [ ] **Step 2: Update the architecture doc**

In `docs/architecture.md`, add `records/` and `widgets/` to the module map with their internal dependency order, and add a "Widget resources" section covering: the two generic tables, the recipe as a closed vocabulary, the Aegra custom-route mount and why authorization is enforced per query, and the rule that a snapshot never invokes the graph. Add ADR 0019 to the decision-record list at the bottom.

- [ ] **Step 3: Update the README**

In `README.md`, add a paragraph to the feature list: Eve can save a reusable widget that refreshes its own data with no model call, member-recorded data lives in one generic collection-addressed store, and a new tracked domain costs a skill rather than a schema.

- [ ] **Step 4: Run everything**

Run: `uv run pytest` then `uv run pytest -m integration`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0019-one-generic-record-store.md docs/architecture.md README.md
git commit -m "docs(widgets): ADR 0019 and the architecture/README updates"
```

---

## Handoff to the client plan

After Task 11 the server is complete and harmless to an un-updated client:
the routes exist but nothing calls them, and `chart` is bound into the tool
schema only for a client that declares it. The client plan
(`open-assistant/docs/superpowers/plans/2026-09-14-reusable-widgets-client.md`)
consumes exactly this contract:

- `GET /provider-resources/v1/capabilities` returns `{protocol, kinds, sourceTypes, actions[{type,risk}], limits}`; a 404 or 405 means unsupported.
- `GET /provider-resources/v1/resources` returns `{resources: [{resourceId, kind, title, revision}]}`.
- `GET /provider-resources/v1/resources/{id}/snapshot` returns `{resourceId, kind, revision, generatedAt, filters, view: {components, data}, sources: {partial, errors}}`.
- `POST /provider-resources/v1/resources/{id}/actions` takes `{type, input, expectedRevision, idempotencyKey?}` and returns a fresh snapshot, or 409 carrying the current snapshot.
- `DELETE /provider-resources/v1/resources/{id}` returns 204, or 404 for absent and foreign ids alike.

One more contract detail the client depends on: every snapshot's component
tree carries an inline `segmentedSelection` whose `actionId` is
`widget.setRange` and whose `selected` is the persisted range. That id is
**not** legal in `assistant-ui/1.0`, which allowlists only `surface.submit`,
so the widget host must intercept it before the generic renderer dispatches
it and turn it into a `filters.replace` action. A chat surface can therefore
never carry a widget filter, which is the point.
