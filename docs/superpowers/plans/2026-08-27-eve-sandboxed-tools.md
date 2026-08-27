# Eve Phase 5c — Gated Executable Tool Code — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eve proposes executable Python tool code, a human approves the exact bytes, and the code runs as a pure function in a sandbox holding no network, no credentials, and no filesystem.

**Architecture:** The proposal is a tool call; the gate is LangGraph's `interrupt()`, which Aegra already persists and resumes; approval binds to a sha256 of the source; discovery reuses the registry arm Phase 5a added; binding reuses `materialize.py` unchanged; dispatch reuses `tools_client.py` with one new parameter. The only new component is `eve-sandbox`, a second isolated service with the opposite polarity to `eve-tools` — it runs machine-written code and holds nothing.

**Tech Stack:** Python 3.12, LangGraph `interrupt`, Alembic (introduced here), FastAPI, `ast` and `resource` from the stdlib, pytest with `asyncio_mode = "auto"`.

**Spec:** [`docs/superpowers/specs/2026-08-27-eve-sandboxed-tools-design.md`](../specs/2026-08-27-eve-sandboxed-tools-design.md)

## Global Constraints

- **Phases 5a and 5b must be merged first.** Task 8 extends the registry arm 5a added; Task 1 assumes 5b left `MIGRATIONS` at 5 entries.
- **A sandbox tool is a pure function.** No network, no filesystem beyond a per-call tmpfs, no environment variables, no credentials, no cluster identity. Not an allowlist — none.
- **The AST check is NOT the security boundary.** Every guarantee must hold with the checker assumed defeated; Task 6 tests from that assumption. The boundary is the pod: default-deny egress, no ServiceAccount token, no secret mounts, read-only root filesystem.
- **Approval binds to `source_sha256`.** Changing the source produces a new proposal needing a new approval. There is no "minor edit" path, and the old approved version keeps serving until the new one is approved.
- **Only an approver may propose.** `propose_tool` requires the `tools.author` permission. If you cannot approve, you cannot propose — there is no queue.
- **`src/eve_sandbox/` imports nothing from `eve`.** Not `eve.settings`, not `eve.memory`. Task 12 asserts it. Every import is a line of code that could be tricked into reading something.
- **`EVE_SANDBOX_ENABLED` defaults to `false`,** and must fail closed even for a sandbox spec already sitting in a checkpointed thread's `dynamic_tools`.
- **Execution limits:** 5s wall clock, 5s `RLIMIT_CPU`, 256 MiB `RLIMIT_AS`, 64 KiB output, 4 concurrent.
- **`interrupt()` needs a checkpointer.** `graph.py` compiles without one because Aegra attaches its own, so interrupt tests must compile with a `MemorySaver`.
- **Every external call degrades to a string, never raises.**

---

## File Structure

**Created:**
- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_baseline.py`, `alembic/versions/0002_eve_tool.py`
- `src/eve/tools_authoring/__init__.py`, `types.py`, `inspect.py`, `store.py`, `propose.py`, `registry.py`, `cli.py`
- `src/eve_sandbox/__init__.py`, `settings.py`, `runner.py`, `execute.py`, `app.py`
- `Dockerfile.eve-sandbox`
- `tests/test_tools_inspect.py`, `test_tools_propose.py`, `test_tools_store.py`, `test_sandbox_execute.py`, `test_sandbox_app.py`, `test_tools_integration.py`, `test_sandbox_live.py`
- `docs/adr/0010-sandboxed-tools-are-pure-functions.md`, `docs/adr/0011-alembic-with-a-private-version-table.md`

**Modified:**
- `src/eve/memory/db.py` — `main()` runs Alembic; `MIGRATIONS` retired to a baseline reference
- `src/eve/tools_client.py` — `target` parameter
- `src/eve/skills/materialize.py` — route `server_id == "sandbox"`
- `src/eve/skills/registry.py`, `src/eve/skills/search.py` — approved tools as a source
- `src/eve/graph.py` — bind `propose_tool` when enabled
- `src/eve/settings.py`, `pyproject.toml`, `.env.example`, `README.md`, `docs/architecture.md`, `docker-compose.test.yml`
- `family.yaml` — the `tools.author` permission for Noah

---

## Task 1: Move migrations to Alembic

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_baseline.py`
- Modify: `src/eve/memory/db.py`, `pyproject.toml`
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Produces: `db.migrate()` runs `alembic upgrade head` against `eve_alembic_version`. `eve-migrate` keeps its name and contract.

Task one, before `eve_tool` exists. `db.py:11` names ~5 entries as the switch
point and 5b left it at exactly 5; `eve_tool` is the sixth. The constraint that
made hand-rolled migrations right in Phase 2 stands: Aegra runs its own Alembic
at startup and Eve's must not interleave, so Eve's gets a **private version
table**.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_memory_store.py`:

```python
async def test_alembic_uses_a_private_version_table(pool):
    """Sharing Aegra's alembic_version is how two independent migration
    histories corrupt each other."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT to_regclass('public.eve_alembic_version')")
        assert (await cur.fetchone())[0] == "eve_alembic_version"


async def test_migrate_is_a_no_op_on_an_already_migrated_database(pool):
    """A rolling restart runs this on a live database. If revision one is not
    idempotent, the deploy is an outage."""
    from eve.memory import db

    await db.migrate()
    await db.migrate()
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_memory")
        assert (await cur.fetchone())[0] >= 0  # no exception is the assertion


async def test_every_phase_1_to_5b_object_still_exists(pool):
    async with pool.connection() as conn:
        for table in (
            "eve_memory", "eve_ambient_seen", "eve_ambient_notice", "eve_pat",
            "eve_ambient_decision", "eve_eval_run",
        ):
            cur = await conn.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            assert (await cur.fetchone())[0] == table, table
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest tests/test_memory_store.py -m integration -k alembic -v
```
Expected: FAIL — `to_regclass` returns `None` for `eve_alembic_version`.

- [ ] **Step 3: Add the dependency and config**

Add `"alembic>=1.14.0",` to `dependencies` in `pyproject.toml`, then `uv sync`.

Create `alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = src

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

Create `alembic/env.py`:

```python
"""Alembic environment for Eve's own schema.

Two things here are load-bearing and neither is default:

- `version_table="eve_alembic_version"`. Aegra runs its own Alembic
  migrations at startup against the same database and the default
  `alembic_version` table. Sharing it would let two independent histories
  stamp over each other.
- The URL comes from eve.settings, not from alembic.ini, so there is one
  source of truth for the connection string and no credential in a file.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from eve.settings import get_settings

VERSION_TABLE = "eve_alembic_version"


def _url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("EVE_DATABASE_URL (or DATABASE_URL) is unset")
    # psycopg 3 driver, matching the runtime pool.
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=None,
        literal_binds=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from alembic import op
import sqlalchemy as sa

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Write the baseline revision**

Create `alembic/versions/0001_baseline.py`. Copy the DDL bodies from the five
`MIGRATIONS` entries in `db.py` verbatim, in order, into one `op.execute` per
entry. Every statement is already `CREATE ... IF NOT EXISTS` or
`ADD COLUMN IF NOT EXISTS`, which is what makes this a no-op against an
existing database.

```python
"""Baseline: Phases 1-5b, reproduced idempotently.

Revision ID: 0001_baseline
Revises: None

A no-op against a database already carrying eve_schema_version's five
entries, because every statement below is IF NOT EXISTS. On a fresh database
it creates everything. That is what lets one image serve both.

`eve_schema_version` is deliberately left in place and unused: dropping it
would make a rollback to the previous image fail on a table it expects.
"""
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # --- 0001_memory ---
    op.execute(
        """
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
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_memory_tsv"
        " ON eve_memory USING gin (content_tsv)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_memory_embedding"
        " ON eve_memory USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_memory_scope"
        " ON eve_memory (scope_kind, scope_id, layer)"
        " WHERE superseded_why IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_memory_subject"
        " ON eve_memory (subject) WHERE superseded_why IS NULL"
    )
    # --- 0002_ambient, 0003_ambient_notice_window, 0004_pat, 0005_eval ---
    # Copy each remaining DDL block from db.MIGRATIONS verbatim here, in
    # order, one op.execute per statement.


def downgrade() -> None:
    raise NotImplementedError("the baseline is not reversible")
```

> **Fidelity check before moving on.** The baseline must reproduce every object
> the five entries create. Run this and confirm an empty diff:
> ```bash
> uv run python - <<'PY'
> import re
> from eve.memory.db import MIGRATIONS
> ddl = "\n".join(d for _, d in MIGRATIONS)
> want = set(re.findall(r"(?:TABLE|INDEX)(?: IF NOT EXISTS)? (\w+)", ddl))
> have = set(re.findall(r"(?:TABLE|INDEX)(?: IF NOT EXISTS)? (\w+)",
>                       open("alembic/versions/0001_baseline.py").read()))
> print("missing from baseline:", sorted(want - have))
> PY
> ```

- [ ] **Step 5: Point `db.migrate()` at Alembic**

Replace `migrate()` in `src/eve/memory/db.py` and retire `MIGRATIONS` to a
reference. Keep the advisory lock: two pods starting at once must not both run
Alembic.

```python
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
```

Above it, replace the `MIGRATIONS` list with:

```python
# Retired in Phase 5c. The five entries that used to live here are reproduced
# in alembic/versions/0001_baseline.py; schema changes are Alembic revisions
# now. Kept as an empty list so the idempotency test's old assertion fails
# loudly rather than importing nothing.
MIGRATIONS: list[tuple[str, str]] = []
```

Delete the now-unused `dict_row` import if nothing else in the file uses it.

- [ ] **Step 6: Run to verify pass**

```bash
uv run pytest tests/test_memory_store.py -m integration -v
uv run pytest
```
Expected: PASS. Delete or rewrite `test_migrate_is_idempotent`'s assertion
about `len(db.MIGRATIONS)` — it now asserts against an empty list, and Step 1's
`test_migrate_is_a_no_op_on_an_already_migrated_database` replaces it.

- [ ] **Step 7: Commit**

```bash
git add alembic.ini alembic/ src/eve/memory/db.py pyproject.toml uv.lock tests/test_memory_store.py
git commit -m "refactor(5c): move Eve's migrations to Alembic with a private version table"
```

---

## Task 2: The `eve_tool` table and its store

**Files:**
- Create: `alembic/versions/0002_eve_tool.py`, `src/eve/tools_authoring/__init__.py`, `types.py`, `store.py`
- Test: `tests/test_tools_store.py`

**Interfaces:**
- Produces: `ToolProposal` dataclass; `store.propose(...) -> str`, `store.approve(tool_id, approver) -> bool`, `store.reject(tool_id, why) -> None`, `store.revoke(name, why) -> int`, `store.revoke_all(why) -> int`, `store.live_tools() -> list[dict]`, `store.by_id(tool_id) -> dict | None`, `store.record_invocation(tool_id) -> None`.

A real table, not a memory layer — the opposite of Phase 5a's call, because
this row is executable, approval-bound to a hash, uniqueness-constrained, and
must never be reachable by semantic recall into a prompt.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_store.py`:

```python
import pytest

pytestmark = pytest.mark.integration

SOURCE = "def run(arguments):\n    return {'ok': True}\n"


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_tool")
    yield p
    await db.close_pool()


async def test_propose_stores_the_source_and_its_hash(pool):
    from eve.tools_authoring.store import by_id, propose

    tool_id = await propose(
        name="amortise", description="d", args_schema={"properties": {}},
        source=SOURCE, proposed_by="sub-noah", thread_id="t1", run_id="r1",
    )
    row = await by_id(tool_id)
    assert row["source"] == SOURCE
    assert len(row["source_sha256"]) == 64
    assert row["approved_at"] is None


async def test_approve_then_live(pool):
    from eve.tools_authoring.store import approve, live_tools, propose

    tool_id = await propose(
        name="amortise", description="d", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    assert await live_tools() == []
    assert await approve(tool_id, "sub-noah") is True
    assert [t["name"] for t in await live_tools()] == ["amortise"]


async def test_only_one_live_approved_version_per_name(pool):
    """The partial unique index IS the approval invariant in the schema."""
    import psycopg

    from eve.tools_authoring.store import approve, propose

    first = await propose(
        name="amortise", description="d", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    second = await propose(
        name="amortise", description="d2", args_schema={},
        source=SOURCE + "# v2\n", proposed_by="sub-noah",
        thread_id=None, run_id=None,
    )
    await approve(first, "sub-noah")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await approve(second, "sub-noah")


async def test_the_old_version_keeps_serving_until_the_new_one_is_approved(pool):
    from eve.tools_authoring.store import approve, live_tools, propose

    first = await propose(
        name="amortise", description="v1", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await approve(first, "sub-noah")
    await propose(
        name="amortise", description="v2", args_schema={},
        source=SOURCE + "# v2\n", proposed_by="sub-noah",
        thread_id=None, run_id=None,
    )
    live = await live_tools()
    assert len(live) == 1 and live[0]["description"] == "v1"


async def test_reject_records_why_and_never_approves(pool):
    from eve.tools_authoring.store import by_id, live_tools, propose, reject

    tool_id = await propose(
        name="amortise", description="d", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await reject(tool_id, "reads a file")
    row = await by_id(tool_id)
    assert row["rejected_why"] == "reads a file"
    assert row["approved_at"] is None
    assert await live_tools() == []


async def test_revoke_frees_the_name_for_a_replacement(pool):
    from eve.tools_authoring.store import approve, live_tools, propose, revoke

    first = await propose(
        name="amortise", description="v1", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await approve(first, "sub-noah")
    assert await revoke("amortise", "wrong") == 1
    assert await live_tools() == []

    second = await propose(
        name="amortise", description="v2", args_schema={},
        source=SOURCE + "# v2\n", proposed_by="sub-noah",
        thread_id=None, run_id=None,
    )
    assert await approve(second, "sub-noah") is True


async def test_revoke_all(pool):
    from eve.tools_authoring.store import approve, live_tools, propose, revoke_all

    for name in ("a", "b"):
        tool_id = await propose(
            name=name, description="d", args_schema={},
            source=SOURCE + f"# {name}\n", proposed_by="sub-noah",
            thread_id=None, run_id=None,
        )
        await approve(tool_id, "sub-noah")
    assert await revoke_all("incident") == 2
    assert await live_tools() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tools_store.py -m integration -v`
Expected: FAIL — no module `eve.tools_authoring`.

- [ ] **Step 3: Write the revision**

Create `alembic/versions/0002_eve_tool.py`:

```python
"""Eve-authored executable tools.

Revision ID: 0002_eve_tool
Revises: 0001_baseline

A real table rather than a memory layer, unlike Phase 5a's rules and
procedures: this row is executable, its approval binds to a hash, it needs a
uniqueness constraint, and it must never be reachable by semantic recall into
a prompt. A text `content` column with an embedding is the wrong shape in
every one of those respects.
"""
from alembic import op

revision = "0002_eve_tool"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_tool (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name           text        NOT NULL,
          description    text        NOT NULL,
          args_schema    jsonb       NOT NULL,
          source         text        NOT NULL,
          source_sha256  text        NOT NULL,
          proposed_by    text        NOT NULL,
          proposed_at    timestamptz NOT NULL DEFAULT now(),
          source_thread  text,
          source_run     text,
          approved_by    text,
          approved_at    timestamptz,
          rejected_why   text,
          revoked_at     timestamptz,
          revoked_why    text,
          invocations    bigint      NOT NULL DEFAULT 0,
          last_used_at   timestamptz
        )
        """
    )
    # One live approved version per name, while unapproved proposals and
    # revoked history accumulate freely. A revoked name can be reused by a
    # replacement - the same pattern eve_pat_active_label uses for tokens.
    op.execute(
        "CREATE UNIQUE INDEX eve_tool_live_name ON eve_tool (name)"
        " WHERE approved_at IS NOT NULL AND revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_tool")
```

- [ ] **Step 4: Write the shapes and the store**

Create `src/eve/tools_authoring/__init__.py`:

```python
"""Phase 5c: Eve proposing executable tool code behind a human approval.

Storage and the approval gate live here, in Eve's own container. Execution
lives in src/eve_sandbox/, which imports nothing from this package or from
eve at all.
"""
```

Create `src/eve/tools_authoring/types.py`:

```python
"""Shapes only. No behaviour, no I/O, no internal imports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ToolProposal:
    name: str
    description: str
    args_schema: dict
    source: str
    # Populated by inspect.check: which allowlisted modules the source
    # imports. Rendered in the interrupt payload so the approver's read is
    # short.
    imports: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    imports: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
```

Create `src/eve/tools_authoring/store.py`:

```python
"""Every eve_tool SQL statement."""

from __future__ import annotations

import hashlib

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool


def source_hash(source: str) -> str:
    """What an approval binds to. The sandbox recomputes this and refuses on a
    mismatch, so approved bytes cannot change underneath the approval."""
    return hashlib.sha256(source.encode()).hexdigest()


async def propose(
    *,
    name: str,
    description: str,
    args_schema: dict,
    source: str,
    proposed_by: str,
    thread_id: str | None,
    run_id: str | None,
) -> str:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO eve_tool
              (name, description, args_schema, source, source_sha256,
               proposed_by, source_thread, source_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                name, description, Jsonb(args_schema), source,
                source_hash(source), proposed_by, thread_id, run_id,
            ),
        )
        return str((await cur.fetchone())[0])


async def approve(tool_id: str, approver: str) -> bool:
    """Stamp approval. The partial unique index raises UniqueViolation if a
    live approved version of this name already exists - deliberately not
    caught here: the caller decides whether that is an error or a signal to
    revoke first."""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE eve_tool SET approved_by = %s, approved_at = now()"
            " WHERE id = %s AND approved_at IS NULL AND rejected_why IS NULL",
            (approver, tool_id),
        )
        return cur.rowcount == 1


async def reject(tool_id: str, why: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_tool SET rejected_why = %s"
            " WHERE id = %s AND approved_at IS NULL",
            (why, tool_id),
        )


async def revoke(name: str, why: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE eve_tool SET revoked_at = now(), revoked_why = %s"
            " WHERE name = %s AND approved_at IS NOT NULL AND revoked_at IS NULL",
            (why, name),
        )
        return cur.rowcount


async def revoke_all(why: str) -> int:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE eve_tool SET revoked_at = now(), revoked_why = %s"
            " WHERE approved_at IS NOT NULL AND revoked_at IS NULL",
            (why,),
        )
        return cur.rowcount


async def live_tools() -> list[dict]:
    """Approved and not revoked. Read on every search_skills call."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, name, description, args_schema, source,"
                " source_sha256, invocations, last_used_at FROM eve_tool"
                " WHERE approved_at IS NOT NULL AND revoked_at IS NULL"
                " ORDER BY name"
            )
            return list(await cur.fetchall())


async def by_id(tool_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM eve_tool WHERE id = %s", (tool_id,))
            return await cur.fetchone()


async def all_tools() -> list[dict]:
    """Everything, for `eve-tool list`: pending, approved, rejected, revoked."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM eve_tool ORDER BY proposed_at DESC")
            return list(await cur.fetchall())


async def record_invocation(tool_id: str) -> None:
    """A tool used once was a wasted approval; Eve should have just done the
    arithmetic. This is how that is visible (design section 11)."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_tool SET invocations = invocations + 1,"
            " last_used_at = now() WHERE id = %s",
            (tool_id,),
        )
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_tools_store.py -m integration -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/0002_eve_tool.py src/eve/tools_authoring/ tests/test_tools_store.py
git commit -m "feat(5c): eve_tool table with a per-name live-approval invariant"
```

---

## Task 3: The AST inspector

**Files:**
- Create: `src/eve/tools_authoring/inspect.py`
- Test: `tests/test_tools_inspect.py`

**Interfaces:**
- Produces: `ALLOWED_IMPORTS: frozenset[str]`, `check(source: str) -> CheckResult`.

**This is an accident guard and a feedback mechanism, not a security boundary.**
Its jobs are to give Eve a specific, actionable error so she can revise before
bothering a human, and to make the approver's read short. Containment is the
pod (Task 6).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_inspect.py`:

```python
import pytest

from eve.tools_authoring.inspect import ALLOWED_IMPORTS, check

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


def test_a_pure_function_passes():
    result = check(PURE)
    assert result.ok and result.problems == []


@pytest.mark.parametrize("module", sorted(ALLOWED_IMPORTS))
def test_every_allowlisted_import_is_accepted(module):
    source = f"import {module}\n\ndef run(arguments):\n    return {{}}\n"
    assert check(source).ok, check(source).problems


@pytest.mark.parametrize(
    "module", ["os", "sys", "subprocess", "socket", "http", "importlib", "shutil"]
)
def test_denied_imports_are_rejected(module):
    source = f"import {module}\n\ndef run(arguments):\n    return {{}}\n"
    result = check(source)
    assert not result.ok
    assert any(module in p for p in result.problems)


def test_urllib_parse_is_allowed_and_urllib_request_is_not():
    """Parsing a URL is computation; fetching one is not."""
    assert check(
        "from urllib.parse import urlparse\n\ndef run(arguments):\n    return {}\n"
    ).ok
    assert not check(
        "from urllib.request import urlopen\n\ndef run(arguments):\n    return {}\n"
    ).ok


@pytest.mark.parametrize(
    "name", ["eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars"]
)
def test_denied_builtins_are_rejected(name):
    source = f"def run(arguments):\n    return {name}('x')\n"
    result = check(source)
    assert not result.ok
    assert any(name in p for p in result.problems)


def test_dunder_attribute_access_is_rejected():
    source = "def run(arguments):\n    return {}.__class__.__bases__\n"
    result = check(source)
    assert not result.ok
    assert any("__class__" in p or "dunder" in p.lower() for p in result.problems)


def test_a_syntax_error_is_a_problem_not_a_crash():
    result = check("def run(:\n")
    assert not result.ok
    assert any("syntax" in p.lower() for p in result.problems)


def test_a_missing_run_function_is_rejected():
    result = check("def other(arguments):\n    return {}\n")
    assert not result.ok
    assert any("run" in p for p in result.problems)


def test_run_must_take_exactly_one_parameter():
    result = check("def run(a, b):\n    return {}\n")
    assert not result.ok


def test_imports_are_reported_for_the_approver():
    source = "import json\nimport math\n\ndef run(arguments):\n    return {}\n"
    assert sorted(check(source).imports) == ["json", "math"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tools_inspect.py -v`
Expected: FAIL — no module `eve.tools_authoring.inspect`.

- [ ] **Step 3: Write the inspector**

Create `src/eve/tools_authoring/inspect.py`:

```python
"""The AST allowlist.

    NOT A SECURITY BOUNDARY. A determined bypass of an AST allowlist exists,
    and treating this as containment is a well-travelled way to get owned.

Its real jobs: give Eve a specific, actionable error so she can revise before
bothering a human, and make the approver's read short. Containment is the pod -
default-deny egress, no ServiceAccount token, no secret mounts, read-only root
filesystem (design section 6.3). Every guarantee in this phase must hold with
this module assumed defeated, and tests/test_sandbox_execute.py tests from that
assumption.

Pure: no I/O, so it is cheap to call at propose time and again at approve time.
"""

from __future__ import annotations

import ast

from eve.tools_authoring.types import CheckResult

# Parsing a URL is computation. Fetching one is not, which is why
# urllib.parse is here and urllib.request is not.
ALLOWED_IMPORTS = frozenset(
    {
        "json", "re", "math", "decimal", "statistics", "datetime", "zoneinfo",
        "itertools", "functools", "collections", "textwrap", "string",
        "dataclasses", "typing", "base64", "hashlib", "urllib.parse", "uuid",
    }
)

DENIED_NAMES = frozenset(
    {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars"}
)


def _import_allowed(dotted: str) -> bool:
    """`json.decoder` is fine if `json` is allowed; `urllib.request` is not
    allowed by `urllib.parse` being allowed."""
    if dotted in ALLOWED_IMPORTS:
        return True
    return any(
        dotted.startswith(f"{allowed}.")
        for allowed in ALLOWED_IMPORTS
        if "." not in allowed
    )


def check(source: str) -> CheckResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return CheckResult(ok=False, problems=[f"syntax error: {exc}"])

    problems: list[str] = []
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
                if not _import_allowed(alias.name):
                    problems.append(
                        f"import of {alias.name!r} is not allowed; a sandbox tool "
                        "is a pure function over data it is handed"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)
            if not _import_allowed(module):
                problems.append(
                    f"import from {module!r} is not allowed; a sandbox tool is a "
                    "pure function over data it is handed"
                )
        elif isinstance(node, ast.Name) and node.id in DENIED_NAMES:
            problems.append(f"use of {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(
                f"attribute access to {node.attr!r} is not allowed (dunder access)"
            )

    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    run = next((f for f in functions if f.name == "run"), None)
    if run is None:
        problems.append(
            "the source must define exactly one module-level function named "
            "'run' taking a single `arguments` dict"
        )
    elif len(run.args.args) != 1:
        problems.append(
            f"'run' must take exactly one parameter, not {len(run.args.args)}"
        )

    return CheckResult(
        ok=not problems, imports=sorted(set(imports)), problems=problems
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_tools_inspect.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/eve/tools_authoring/inspect.py tests/test_tools_inspect.py
git commit -m "feat(5c): AST allowlist as a feedback guard, not a boundary"
```

---

## Task 4: `propose_tool` and the interrupt

**Files:**
- Create: `src/eve/tools_authoring/propose.py`
- Modify: `src/eve/settings.py`, `family.yaml`
- Test: `tests/test_tools_propose.py`

**Interfaces:**
- Consumes: Tasks 2 and 3.
- Produces: `propose_tool` tool; settings `sandbox_enabled`, `sandbox_base_url`, `sandbox_api_key`, `sandbox_timeout_seconds`, `sandbox_memory_mb`, `sandbox_max_output_bytes`, `sandbox_max_concurrency`.

Two existing facts make the interrupt work, and both look like they would break
it: the graph compiles **without** a checkpointer (Aegra attaches its own, so
tests need a `MemorySaver`), and `ToolNode`'s `_handle_tool_error` would swallow
any exception — but `GraphBubbleUp`, which interrupts raise through, is
re-raised before reaching it, as `graph.py`'s own comment records.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools_propose.py`:

```python
import pytest

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"
IMPURE = "import os\n\ndef run(arguments):\n    return {'p': os.getcwd()}\n"

MEMBER_AUTHOR = {
    "sub": "sub-noah", "name": "Noah", "role": "adult",
    "timezone": "America/Toronto", "permissions": ["tools.author"],
    "local_time": "2026-08-27 09:00 EDT",
}
MEMBER_PLAIN = {**MEMBER_AUTHOR, "sub": "sub-kid", "permissions": []}


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()


async def test_without_the_permission_it_never_interrupts(monkeypatch):
    """If you cannot approve, you cannot propose. There is no queue."""
    from eve.tools_authoring import propose as propose_mod

    def boom(payload):
        raise AssertionError("must not interrupt without tools.author")

    monkeypatch.setattr(propose_mod, "interrupt", boom)

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d",
            "args_schema": {"properties": {"a": {"type": "integer"}}},
            "source": PURE,
            "state": {"member": MEMBER_PLAIN},
        },
        config={"configurable": {}},
    )
    assert "Permission denied" in result


async def test_a_failing_ast_check_returns_feedback_without_interrupting(monkeypatch):
    """Eve revises before a human is bothered."""
    from eve.tools_authoring import propose as propose_mod

    def boom(payload):
        raise AssertionError("must not interrupt on a failed check")

    monkeypatch.setattr(propose_mod, "interrupt", boom)

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d", "args_schema": {},
            "source": IMPURE,
            "state": {"member": MEMBER_AUTHOR},
        },
        config={"configurable": {}},
    )
    assert "os" in result and "not allowed" in result


async def test_an_unmapped_schema_type_is_rejected(monkeypatch):
    """materialize.py maps only string/integer/number/boolean and silently
    falls back to str for anything else. Inheriting that wrong validation is
    a bug; refusing is not."""
    from eve.tools_authoring import propose as propose_mod

    monkeypatch.setattr(propose_mod, "interrupt", lambda p: {"approved": True})

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d",
            "args_schema": {"properties": {"rows": {"type": "array"}}},
            "source": PURE,
            "state": {"member": MEMBER_AUTHOR},
        },
        config={"configurable": {}},
    )
    assert "array" in result


async def test_approval_persists_the_tool(monkeypatch):
    from eve.tools_authoring import propose as propose_mod

    stored = {}

    async def propose(**kw):
        stored.update(kw)
        return "tool-1"

    async def approve(tool_id, approver):
        stored["approved_by"] = approver
        return True

    monkeypatch.setattr(propose_mod, "store_propose", propose)
    monkeypatch.setattr(propose_mod, "store_approve", approve)
    monkeypatch.setattr(
        propose_mod, "interrupt", lambda payload: {"approved": True, "why": "fine"}
    )

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d",
            "args_schema": {"properties": {"a": {"type": "integer"}}},
            "source": PURE,
            "state": {"member": MEMBER_AUTHOR},
        },
        config={"configurable": {"thread_id": "t1", "run_id": "r1"}},
    )
    assert stored["name"] == "amortise"
    assert stored["approved_by"] == "sub-noah"
    assert "approved" in result.lower()


async def test_rejection_records_why_and_does_not_approve(monkeypatch):
    from eve.tools_authoring import propose as propose_mod

    calls = {}

    async def propose(**kw):
        return "tool-1"

    async def approve(tool_id, approver):
        raise AssertionError("a rejected proposal must not be approved")

    async def reject(tool_id, why):
        calls["why"] = why

    monkeypatch.setattr(propose_mod, "store_propose", propose)
    monkeypatch.setattr(propose_mod, "store_approve", approve)
    monkeypatch.setattr(propose_mod, "store_reject", reject)
    monkeypatch.setattr(
        propose_mod, "interrupt",
        lambda payload: {"approved": False, "why": "reads a file"},
    )

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "d",
            "args_schema": {"properties": {}}, "source": PURE,
            "state": {"member": MEMBER_AUTHOR},
        },
        config={"configurable": {}},
    )
    assert calls["why"] == "reads a file"
    assert "not approved" in result.lower()


async def test_the_interrupt_payload_shows_the_approver_everything(monkeypatch):
    from eve.tools_authoring import propose as propose_mod

    seen = {}

    async def propose(**kw):
        return "tool-1"

    def capture(payload):
        seen.update(payload)
        return {"approved": False, "why": "no"}

    monkeypatch.setattr(propose_mod, "store_propose", propose)
    monkeypatch.setattr(propose_mod, "store_reject", lambda *a: _noop())
    monkeypatch.setattr(propose_mod, "interrupt", capture)

    await propose_mod.propose_tool.ainvoke(
        {
            "name": "amortise", "description": "amortise a loan",
            "args_schema": {"properties": {"a": {"type": "integer"}}},
            "source": "import math\n" + PURE,
            "state": {"member": MEMBER_AUTHOR},
        },
        config={"configurable": {"thread_id": "t1"}},
    )
    assert seen["name"] == "amortise"
    assert seen["source"].startswith("import math")
    assert "math" in seen["imports"]
    assert seen["requested_by"] == "sub-noah"


async def _noop():
    return None


async def test_disabled_refuses_before_anything_else(monkeypatch):
    from eve.tools_authoring import propose as propose_mod
    from eve.settings import get_settings

    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "false")
    get_settings.cache_clear()

    def boom(payload):
        raise AssertionError("must not interrupt when disabled")

    monkeypatch.setattr(propose_mod, "interrupt", boom)

    result = await propose_mod.propose_tool.ainvoke(
        {
            "name": "x", "description": "d", "args_schema": {}, "source": PURE,
            "state": {"member": MEMBER_AUTHOR},
        },
        config={"configurable": {}},
    )
    assert result.startswith("error:")


async def test_an_interrupt_from_a_tool_is_not_swallowed_by_the_error_handler():
    """THE test that matters most. _handle_tool_error degrades every tool
    exception to a string; if a refactor makes it catch GraphBubbleUp too, the
    approval gate silently becomes an auto-approver."""
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.prebuilt import ToolNode
    from langgraph.types import interrupt

    from eve.graph import _handle_tool_error

    @tool
    def needs_approval(x: int) -> str:
        """Ask for approval."""
        decision = interrupt({"x": x})
        return f"decided: {decision}"

    class S(dict):
        pass

    builder = StateGraph(dict)
    builder.add_node(
        "tools", ToolNode([needs_approval], handle_tool_errors=_handle_tool_error)
    )
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    app = builder.compile(checkpointer=MemorySaver())

    config = {"configurable": {"thread_id": "t-interrupt"}}
    result = await app.ainvoke(
        {
            "messages": [
                AIMessage(
                    "",
                    tool_calls=[
                        {"name": "needs_approval", "args": {"x": 1}, "id": "c1"}
                    ],
                )
            ]
        },
        config,
    )
    assert "__interrupt__" in result, (
        "the interrupt was swallowed - the approval gate is not a gate"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tools_propose.py -v`
Expected: FAIL — no module `eve.tools_authoring.propose`.

- [ ] **Step 3: Add the settings**

In `src/eve/settings.py`, after the Phase 5b block:

```python
    # Phase 5c (Gated tool code). See docs/superpowers/specs/
    # 2026-08-27-eve-sandboxed-tools-design.md section 10.
    sandbox_enabled: bool = False
    sandbox_base_url: str = "http://eve-sandbox:8091"
    sandbox_api_key: str = ""
    sandbox_timeout_seconds: int = 5
    sandbox_memory_mb: int = 256
    sandbox_max_output_bytes: int = 65536
    sandbox_max_concurrency: int = 4
```

And in `model_post_init`, after the ambient checks:

```python
        if self.sandbox_api_key and len(self.sandbox_api_key) < 32:
            raise ValueError(
                "EVE_SANDBOX_API_KEY must be at least 32 characters: it "
                "authenticates a service that executes code, so a guessable "
                "value fails open"
            )
        if self.sandbox_enabled and not self.sandbox_api_key:
            raise ValueError(
                "EVE_SANDBOX_API_KEY is required when EVE_SANDBOX_ENABLED=true"
            )
```

In `family.yaml`, add `tools.author` to Noah's permissions (and to
`tests/fixtures/family.yaml` for `sub-noah`, leaving `sub-kid` without it).

- [ ] **Step 4: Write `propose_tool`**

Create `src/eve/tools_authoring/propose.py`:

```python
"""Proposing a tool, and the one human gate in the program.

The gate is LangGraph's `interrupt()`: the run pauses, Aegra checkpoints it,
and the operator resumes with Command(resume={"approved": bool, "why": str})
from the Agent Chat UI or the SDK. No approval UI is built here.

`tools.author` is required to propose, which collapses proposer and approver
into one person deliberately (design section 5.1): the alternative is a
notification-and-queue workflow system for a household of five, serving a case
- someone who cannot approve code wanting code written - that is not worth it.
One consequence matters: the interrupt always surfaces in a thread owned by
someone entitled to answer it.
"""

from __future__ import annotations

import logging
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.types import interrupt
from opentelemetry import trace

from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.state import EveState
from eve.tools_authoring.inspect import check
from eve.tools_authoring.store import approve as store_approve
from eve.tools_authoring.store import propose as store_propose
from eve.tools_authoring.store import reject as store_reject

logger = logging.getLogger(__name__)

# materialize.py maps only these and silently falls back to str for anything
# else. Refusing beats inheriting that wrong validation.
_MAPPED_TYPES = frozenset({"string", "integer", "number", "boolean"})


def _unmapped_types(args_schema: dict) -> list[str]:
    return sorted(
        {
            info.get("type", "string")
            for info in (args_schema.get("properties") or {}).values()
            if info.get("type", "string") not in _MAPPED_TYPES
        }
    )


@tool
async def propose_tool(
    name: str,
    description: str,
    args_schema: dict,
    source: str,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """Propose a small Python tool for a calculation or parse you keep doing
    by hand. `source` must define exactly one function,
    `def run(arguments: dict) -> dict`. It runs with NO network, NO file
    access and NO credentials, so it can only compute over what it is handed.
    A human must approve it before it can ever run."""
    settings = get_settings()
    if not settings.sandbox_enabled:
        return "error: proposing tools is disabled in this deployment."

    member = state["member"]
    denial = permission_denial(member.get("permissions") or [], "tools.author")
    if denial is not None:
        return denial

    span = trace.get_current_span()
    result = check(source)
    if not result.ok:
        span.set_attribute("eve.sandbox.ast_rejected", 1)
        problems = "\n".join(f"- {p}" for p in result.problems)
        return (
            "That source cannot be accepted as written:\n"
            f"{problems}\n"
            "A sandbox tool is a pure function over the data it is handed."
        )

    unmapped = _unmapped_types(args_schema)
    if unmapped:
        return (
            f"Argument types {unmapped} are not supported. Use only string, "
            "integer, number, or boolean properties."
        )

    configurable = config.get("configurable", {}) if config else {}
    try:
        tool_id = await store_propose(
            name=name,
            description=description,
            args_schema=args_schema,
            source=source,
            proposed_by=member["sub"],
            thread_id=configurable.get("thread_id"),
            run_id=configurable.get("run_id"),
        )
    except Exception as exc:
        logger.warning("could not record the proposal %r", name, exc_info=True)
        return f"error: could not record that proposal ({exc.__class__.__name__})"

    span.set_attribute("eve.sandbox.proposed", 1)

    # The gate. Everything the approver needs is in this payload: reading the
    # source elsewhere is how a wrong version gets approved.
    decision = interrupt(
        {
            "kind": "tool_approval",
            "tool_id": tool_id,
            "name": name,
            "description": description,
            "args_schema": args_schema,
            "source": source,
            "imports": result.imports,
            "requested_by": member["sub"],
            "thread_id": configurable.get("thread_id"),
        }
    )

    approved = bool((decision or {}).get("approved"))
    why = (decision or {}).get("why") or "unspecified"
    try:
        if approved:
            await store_approve(tool_id, member["sub"])
        else:
            await store_reject(tool_id, why)
    except Exception as exc:
        logger.warning("could not record the decision for %r", name, exc_info=True)
        return f"error: could not record that decision ({exc.__class__.__name__})"

    span.set_attribute("eve.sandbox.approved" if approved else "eve.sandbox.rejected", 1)
    if approved:
        return f"{name!r} was approved and is now available."
    return f"{name!r} was not approved ({why})."
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_tools_propose.py tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/tools_authoring/propose.py src/eve/settings.py family.yaml tests/fixtures/family.yaml tests/test_tools_propose.py
git commit -m "feat(5c): propose_tool behind a LangGraph interrupt and tools.author"
```

---

## Task 5: The sandbox runner and executor

**Files:**
- Create: `src/eve_sandbox/__init__.py`, `settings.py`, `runner.py`, `execute.py`
- Test: `tests/test_sandbox_execute.py`

**Interfaces:**
- Produces: `execute.run_tool(source, source_sha256, arguments) -> dict` returning `{"result": ...}` or `{"error": ...}`.

Limits are set **inside the child** by `runner.py` rather than via
`preexec_fn`: `asyncio.create_subprocess_exec` does not support `preexec_fn`,
and a child that limits itself needs no unsafe hook.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sandbox_execute.py`:

```python
import pytest

from eve_sandbox.execute import run_tool

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


def _sha(source: str) -> str:
    import hashlib

    return hashlib.sha256(source.encode()).hexdigest()


async def test_a_pure_function_returns_its_result():
    out = await run_tool(PURE, _sha(PURE), {"a": 41})
    assert out == {"result": {"n": 42}}


async def test_a_hash_mismatch_is_refused():
    """The database and the caller disagree about approved bytes. A tampering
    signal, not a bug to retry."""
    out = await run_tool(PURE, "0" * 64, {"a": 1})
    assert "error" in out and "hash" in out["error"].lower()


async def test_a_raising_tool_returns_an_error_not_an_exception():
    source = "def run(arguments):\n    raise ValueError('nope')\n"
    out = await run_tool(source, _sha(source), {})
    assert "error" in out and "nope" in out["error"]


async def test_a_wall_clock_timeout_is_killed():
    source = "import time\n\ndef run(arguments):\n    time.sleep(30)\n    return {}\n"
    out = await run_tool(source, _sha(source), {}, timeout=1)
    assert "error" in out and "time" in out["error"].lower()


async def test_a_busy_loop_is_killed():
    """A wall clock and RLIMIT_CPU catch different failures: a sleep burns no
    CPU, a busy loop burns no wall clock advantage."""
    source = "def run(arguments):\n    x = 0\n    while True:\n        x += 1\n"
    out = await run_tool(source, _sha(source), {}, timeout=2)
    assert "error" in out


async def test_a_memory_hog_is_refused():
    source = "def run(arguments):\n    return {'x': [0] * (10 ** 9)}\n"
    out = await run_tool(source, _sha(source), {}, memory_mb=64)
    assert "error" in out


async def test_oversized_output_is_truncated_or_refused():
    source = "def run(arguments):\n    return {'x': 'y' * 200000}\n"
    out = await run_tool(source, _sha(source), {}, max_output_bytes=1024)
    assert "error" in out


async def test_a_non_serialisable_result_is_an_error():
    source = "def run(arguments):\n    return {'x': object()}\n"
    out = await run_tool(source, _sha(source), {})
    assert "error" in out


async def test_the_child_cannot_read_the_environment():
    """No environment variables cross the boundary. A tool that could read
    them could read a credential if one were ever mounted by mistake."""
    source = (
        "def run(arguments):\n"
        "    import os\n"  # deliberately bypasses the AST checker
        "    return {'keys': sorted(k for k in os.environ if k.startswith('EVE_'))}\n"
    )
    out = await run_tool(source, _sha(source), {})
    assert out.get("result", {}).get("keys") == [] or "error" in out


async def test_source_bypassing_the_ast_checker_still_cannot_reach_the_network():
    """THE assumption test. §6.3 claims the AST check is not what holds the
    line; this executes code the checker would have rejected and asserts the
    process-level constraints still stop it.

    Marked integration: it depends on the host having no route, so it is only
    fully meaningful in the deployed pod (see tests/test_sandbox_live.py).
    """
    source = (
        "def run(arguments):\n"
        "    import socket\n"
        "    s = socket.create_connection(('example.com', 80), timeout=2)\n"
        "    return {'connected': True}\n"
    )
    out = await run_tool(source, _sha(source), {}, timeout=3)
    # On a developer machine with a route this WILL connect. The assertion is
    # only that it cannot do so silently in the deployed pod, which
    # test_sandbox_live.py checks. Here, assert we at least got a structured
    # answer rather than a crash of the service.
    assert "result" in out or "error" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sandbox_execute.py -v`
Expected: FAIL — no module `eve_sandbox`.

- [ ] **Step 3: Write the sandbox settings and child runner**

Create `src/eve_sandbox/__init__.py`:

```python
"""eve-sandbox: executes Eve-authored tool code and holds nothing.

The opposite polarity to eve-tools (ADR 0006): that service holds every
third-party credential and runs only human-written code; this one runs
machine-written code and holds no credential, no cluster identity, and no
network route.

This package imports NOTHING from `eve`. eve_tools reaches into eve.settings
for its own reasons; this must not, because every import is a line of code
that could be tricked into reading something.
"""
```

Create `src/eve_sandbox/settings.py`:

```python
"""eve-sandbox's own configuration: one API key and the limits. Nothing else,
deliberately - there is no database URL, no model key, and no third-party
credential for this service to leak."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVE_SANDBOX_", extra="ignore")

    api_key: str = ""
    timeout_seconds: int = 5
    memory_mb: int = 256
    max_output_bytes: int = 65536
    max_concurrency: int = 4


@lru_cache(maxsize=1)
def get_sandbox_settings() -> SandboxSettings:
    return SandboxSettings()
```

Create `src/eve_sandbox/runner.py`:

```python
"""The child process. Reads one job on stdin, writes one JSON line on stdout.

Limits are set here, by the child on itself, rather than through
subprocess(preexec_fn=...): asyncio.create_subprocess_exec does not support
preexec_fn, and a child that limits itself needs no unsafe hook.

Run as `python -I -m eve_sandbox.runner`: -I is isolated mode, so no
PYTHONPATH, no user site-packages, and no current directory on sys.path.
"""

from __future__ import annotations

import json
import resource
import sys


def _limit(memory_mb: int, cpu_seconds: int) -> None:
    # Address space: catches an allocation blow-up. CPU: catches a busy loop
    # that the parent's wall clock would also catch, but sooner and from
    # inside, so the parent's kill is a backstop rather than the only bound.
    resource.setrlimit(
        resource.RLIMIT_AS, (memory_mb * 1024 * 1024, memory_mb * 1024 * 1024)
    )
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    # No core dumps: a dump of this process is the one artefact that could
    # persist tool data outside the call.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main() -> None:
    job = json.loads(sys.stdin.read())
    _limit(int(job["memory_mb"]), int(job["cpu_seconds"]))

    namespace: dict = {}
    try:
        exec(compile(job["source"], "<tool>", "exec"), namespace)  # noqa: S102
        run = namespace.get("run")
        if run is None:
            raise RuntimeError("the source defines no 'run' function")
        result = run(job["arguments"])
        payload = json.dumps({"result": result})
    except MemoryError:
        payload = json.dumps({"error": "the tool exceeded its memory limit"})
    except Exception as exc:  # noqa: BLE001
        payload = json.dumps({"error": f"{exc.__class__.__name__}: {exc}"})
    except BaseException as exc:  # SystemExit, KeyboardInterrupt from rlimits
        payload = json.dumps({"error": f"the tool was stopped ({exc.__class__.__name__})"})

    sys.stdout.write(payload)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
```

Create `src/eve_sandbox/execute.py`:

```python
"""One subprocess per call. No reuse, no warm pool.

No pool because process startup is milliseconds against a VOICE model call,
and a reused interpreter is state shared between two tools - the one thing a
sandbox tool does not get.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import tempfile

from eve_sandbox.settings import get_sandbox_settings

logger = logging.getLogger(__name__)


async def run_tool(
    source: str,
    source_sha256: str,
    arguments: dict,
    *,
    timeout: int | None = None,
    memory_mb: int | None = None,
    max_output_bytes: int | None = None,
) -> dict:
    settings = get_sandbox_settings()
    timeout = timeout or settings.timeout_seconds
    memory_mb = memory_mb or settings.memory_mb
    max_output_bytes = max_output_bytes or settings.max_output_bytes

    actual = hashlib.sha256(source.encode()).hexdigest()
    if actual != source_sha256:
        # The database and the caller disagree about approved bytes. A
        # tampering signal, not a bug to retry.
        logger.error("source hash mismatch: refusing to execute")
        return {"error": "source hash mismatch; refusing to execute"}

    job = json.dumps(
        {
            "source": source,
            "arguments": arguments,
            "memory_mb": memory_mb,
            "cpu_seconds": timeout,
        }
    )

    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-m", "eve_sandbox.runner",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=workdir,
                # Empty but for the import path the child needs to find
                # itself. No EVE_*, no PATH-derived credentials, nothing.
                env={"PYTHONPATH": _package_root()},
                start_new_session=True,
            )
        except Exception as exc:
            logger.warning("could not start the sandbox child", exc_info=True)
            return {"error": f"could not start the sandbox ({exc.__class__.__name__})"}

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(job.encode()), timeout=timeout + 1
            )
        except asyncio.TimeoutError:
            _kill_group(proc)
            await proc.wait()
            return {"error": f"the tool exceeded its {timeout}s time limit"}

    if len(stdout) > max_output_bytes:
        return {
            "error": f"the tool returned more than {max_output_bytes} bytes"
        }
    if not stdout:
        # Killed by RLIMIT_CPU or RLIMIT_AS before it could write.
        return {"error": "the tool was stopped by a resource limit"}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"error": "the tool returned something that is not JSON"}


def _package_root() -> str:
    """The directory containing the eve_sandbox package, so `-I` mode can
    still import the runner."""
    import eve_sandbox

    return os.path.dirname(os.path.dirname(os.path.abspath(eve_sandbox.__file__)))


def _kill_group(proc) -> None:
    """start_new_session puts the child in its own process group, so a tool
    that spawned anything is killed with it."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_sandbox_execute.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/eve_sandbox/ tests/test_sandbox_execute.py
git commit -m "feat(5c): the sandbox executor - one limited subprocess per call"
```

---

## Task 6: The `eve-sandbox` service

**Files:**
- Create: `src/eve_sandbox/app.py`, `Dockerfile.eve-sandbox`
- Modify: `docker-compose.test.yml`
- Test: `tests/test_sandbox_app.py`

**Interfaces:**
- Produces: `POST /invoke` accepting `{"tool": name, "arguments": {...}, "source": ..., "source_sha256": ...}` and returning `{"result": ...}` or `{"error": ...}`; `GET /healthz`.

Identical in shape to `eve-tools`' surface, so `tools_client` works against it
with one added parameter and no new failure handling.

Eve's container sends `source` with each call rather than the sandbox reading
the database — so the sandbox needs no database credential, the last one it
might otherwise have held.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sandbox_app.py`:

```python
import pytest
from fastapi.testclient import TestClient

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


def _sha(source: str) -> str:
    import hashlib

    return hashlib.sha256(source.encode()).hexdigest()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    from eve_sandbox.settings import get_sandbox_settings

    get_sandbox_settings.cache_clear()
    from eve_sandbox.app import app

    yield TestClient(app)
    get_sandbox_settings.cache_clear()


def test_healthz_needs_no_auth(client):
    assert client.get("/healthz").status_code == 200


def test_invoke_requires_the_bearer_token(client):
    response = client.post(
        "/invoke",
        json={"tool": "t", "arguments": {}, "source": PURE,
              "source_sha256": _sha(PURE)},
    )
    assert response.status_code == 401


def test_invoke_runs_the_tool(client):
    response = client.post(
        "/invoke",
        headers={"Authorization": "Bearer " + "k" * 32},
        json={"tool": "t", "arguments": {"a": 41}, "source": PURE,
              "source_sha256": _sha(PURE)},
    )
    assert response.status_code == 200
    assert response.json() == {"result": {"n": 42}}


def test_a_hash_mismatch_answers_with_an_error_body_not_a_500(client):
    response = client.post(
        "/invoke",
        headers={"Authorization": "Bearer " + "k" * 32},
        json={"tool": "t", "arguments": {}, "source": PURE,
              "source_sha256": "0" * 64},
    )
    assert response.status_code == 200
    assert "error" in response.json()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_sandbox_app.py -v`
Expected: FAIL — no module `eve_sandbox.app`.

- [ ] **Step 3: Write the service**

Create `src/eve_sandbox/app.py`:

```python
"""The eve-sandbox HTTP surface: one route that runs one pure function.

Same contract shape as eve-tools' /invoke, so eve.tools_client works against
it with one added parameter. Holds no credential beyond the shared bearer
token that authenticates Eve to it.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from eve_sandbox.execute import run_tool
from eve_sandbox.settings import get_sandbox_settings

app = FastAPI(title="eve-sandbox")

# Bounded here rather than by the process count: four concurrent subprocesses
# at 256 MiB each is the memory ceiling this pod is sized for.
_semaphore: asyncio.Semaphore | None = None


def _gate() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_sandbox_settings().max_concurrency)
    return _semaphore


class InvokeBody(BaseModel):
    tool: str
    arguments: dict = {}
    source: str
    source_sha256: str


def _check_auth(authorization: str | None) -> None:
    settings = get_sandbox_settings()
    if not settings.api_key or authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/invoke")
async def invoke(
    body: InvokeBody, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    async with _gate():
        return await run_tool(body.source, body.source_sha256, body.arguments)
```

Create `Dockerfile.eve-sandbox`, following `Dockerfile.eve-tools`:

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv

WORKDIR /app

# --no-install-project: eve-sandbox is never "the project" uv_build packages
# (that's "eve", src/eve). PYTHONPATH below makes src/eve_sandbox importable.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ONLY eve_sandbox. Not src/eve, not src/eve_tools: this image must not
# contain a module that knows how to reach the database or a credential.
COPY src/eve_sandbox ./src/eve_sandbox

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PORT=8091

EXPOSE 8091

RUN useradd --system --uid 10003 --no-create-home eve-sandbox \
    && chown -R eve-sandbox:eve-sandbox /app
USER 10003

CMD ["uvicorn", "eve_sandbox.app:app", "--host", "0.0.0.0", "--port", "8091"]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_sandbox_app.py -v && docker build -f Dockerfile.eve-sandbox -t eve-sandbox:test .`
Expected: PASS and a successful build.

- [ ] **Step 5: Verify the image holds nothing it should not**

```bash
docker run --rm --entrypoint sh eve-sandbox:test -c \
  'ls /app/src && python -c "import eve" 2>&1 | tail -1'
```
Expected: `eve_sandbox` only, and `ModuleNotFoundError: No module named 'eve'`.

- [ ] **Step 6: Commit**

```bash
git add src/eve_sandbox/app.py Dockerfile.eve-sandbox tests/test_sandbox_app.py
git commit -m "feat(5c): the eve-sandbox service and its credential-free image"
```

---

## Task 7: Dispatch through `tools_client` and `materialize`

**Files:**
- Modify: `src/eve/tools_client.py`, `src/eve/skills/materialize.py`
- Test: `tests/test_tools_client.py`, `tests/test_skills_materialize.py`

**Interfaces:**
- Produces: `invoke(tool, arguments, timeout=15.0, *, target="tools", extra=None)`; `materialize` routes `server_id == "sandbox"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tools_client.py`:

```python
import respx


@respx.mock
async def test_invoke_targets_the_sandbox_when_asked(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_BASE_URL", "http://sandbox:8091")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "s" * 32)
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", "http://tools:8090")
    from eve.settings import get_settings

    get_settings.cache_clear()

    route = respx.post("http://sandbox:8091/invoke").respond(
        json={"result": {"n": 42}}
    )
    from eve.tools_client import invoke

    out = await invoke("amortise", {"a": 41}, target="sandbox")

    assert route.called
    assert "42" in out
    assert route.calls[0].request.headers["authorization"] == "Bearer " + "s" * 32


@respx.mock
async def test_invoke_still_defaults_to_eve_tools(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", "http://tools:8090")
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "t" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()

    route = respx.post("http://tools:8090/invoke").respond(json={"result": 1})
    from eve.tools_client import invoke

    await invoke("home.get_state", {"entity_id": "x"})
    assert route.called


@respx.mock
async def test_a_dead_sandbox_returns_an_error_string(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_BASE_URL", "http://sandbox:8091")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "s" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()

    respx.post("http://sandbox:8091/invoke").mock(side_effect=ConnectionError)
    from eve.tools_client import invoke

    out = await invoke("amortise", {}, target="sandbox")
    assert out.startswith("error:")
```

Add to `tests/test_skills_materialize.py`:

```python
async def test_a_sandbox_spec_dispatches_to_the_sandbox(monkeypatch):
    from eve.skills import materialize as materialize_mod

    seen = {}

    async def invoke(tool, arguments, timeout=15.0, *, target="tools", extra=None):
        seen["target"] = target
        seen["extra"] = extra
        return '{"n": 42}'

    monkeypatch.setattr(materialize_mod, "invoke", invoke)

    spec = {
        "server_id": "sandbox",
        "tool_name": "amortise",
        "description": "d",
        "schema": {"properties": {"a": {"type": "integer"}}},
        "source": "def run(arguments):\n    return {}\n",
        "source_sha256": "a" * 64,
    }
    built = materialize_mod.materialize(spec)
    await built.ainvoke({"a": 1})

    assert seen["target"] == "sandbox"
    assert seen["extra"]["source_sha256"] == "a" * 64


async def test_a_sandbox_call_counts_the_invocation(monkeypatch):
    """A tool approved and then used once was a wasted approval. That is only
    visible in `eve-tool list` if dispatch records the use."""
    import sys
    import types as pytypes

    from eve.skills import materialize as materialize_mod

    counted = []

    async def invoke(tool, arguments, timeout=15.0, *, target="tools", extra=None):
        return "{}"

    async def record_invocation(tool_id):
        counted.append(tool_id)

    fake = pytypes.ModuleType("eve.tools_authoring.store")
    fake.record_invocation = record_invocation
    monkeypatch.setitem(sys.modules, "eve.tools_authoring.store", fake)
    monkeypatch.setattr(materialize_mod, "invoke", invoke)

    built = materialize_mod.materialize(
        {
            "server_id": "sandbox", "tool_name": "amortise", "description": "d",
            "schema": {"properties": {}}, "source": "x",
            "source_sha256": "a" * 64, "tool_id": "tool-1",
        }
    )
    await built.ainvoke({})

    assert counted == ["tool-1"]


async def test_a_counting_failure_does_not_fail_the_call(monkeypatch):
    """The result is already computed. Losing a counter must not lose it."""
    import sys
    import types as pytypes

    from eve.skills import materialize as materialize_mod

    async def invoke(tool, arguments, timeout=15.0, *, target="tools", extra=None):
        return '{"n": 42}'

    async def record_invocation(tool_id):
        raise RuntimeError("postgres is down")

    fake = pytypes.ModuleType("eve.tools_authoring.store")
    fake.record_invocation = record_invocation
    monkeypatch.setitem(sys.modules, "eve.tools_authoring.store", fake)
    monkeypatch.setattr(materialize_mod, "invoke", invoke)

    built = materialize_mod.materialize(
        {
            "server_id": "sandbox", "tool_name": "amortise", "description": "d",
            "schema": {"properties": {}}, "source": "x",
            "source_sha256": "a" * 64, "tool_id": "tool-1",
        }
    )
    assert "42" in await built.ainvoke({})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tools_client.py tests/test_skills_materialize.py -v`
Expected: FAIL — `invoke()` got an unexpected keyword argument `target`.

- [ ] **Step 3: Add the target parameter**

Replace `invoke` in `src/eve/tools_client.py`:

```python
_TARGETS = {
    "tools": ("tools_base_url", "tools_api_key"),
    "sandbox": ("sandbox_base_url", "sandbox_api_key"),
}


async def invoke(
    tool: str,
    arguments: dict,
    timeout: float = 15.0,
    *,
    target: str = "tools",
    extra: dict | None = None,
) -> str:
    """One door to eve-tools, and since Phase 5c one to eve-sandbox.

    Two targets rather than two modules: the /invoke contract is identical, and
    so is the failure posture that matters - every failure degrades to a
    returned error string, because the caller is always a tool whose result
    goes straight to a model.
    """
    settings = get_settings()
    url_attr, key_attr = _TARGETS.get(target, _TARGETS["tools"])
    base_url = getattr(settings, url_attr)
    api_key = getattr(settings, key_attr)
    payload = {"tool": tool, "arguments": arguments, **(extra or {})}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/invoke",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            body = response.json()

        if "error" in body:
            return f"error: {body['error']}"
        return json.dumps(body["result"])
    except Exception as exc:
        logger.warning("eve-%s call to %r failed", target, tool, exc_info=True)
        return f"error: eve-{target} unavailable ({exc.__class__.__name__})"
```

- [ ] **Step 4: Route sandbox specs in `materialize`**

In `src/eve/skills/materialize.py`, replace the `_call` closure inside
`materialize`:

```python
def materialize(spec: DynamicToolSpec) -> StructuredTool:
    args_model = _args_model(spec["tool_name"], spec["schema"])
    is_sandbox = spec.get("server_id") == "sandbox"

    async def _call(**kwargs) -> str:
        if is_sandbox:
            started = perf_counter()
            # The source travels with the request, so eve-sandbox needs no
            # database credential - the last one it might otherwise hold.
            result = await invoke(
                spec["tool_name"],
                kwargs,
                target="sandbox",
                extra={
                    "source": spec.get("source", ""),
                    "source_sha256": spec.get("source_sha256", ""),
                },
            )
            # Observability on THIS side of the hop. eve-sandbox emits no
            # spans - it holds no Langfuse credential and should not - so the
            # numbers design section 11 asks for are recorded by the caller,
            # which is also the only side that knows the round trip's cost.
            span = trace.get_current_span()
            span.set_attribute(
                "eve.sandbox.duration_ms", round((perf_counter() - started) * 1000, 1)
            )
            if result.startswith("error:"):
                lowered = result.lower()
                if "time limit" in lowered:
                    span.set_attribute("eve.sandbox.timeouts", 1)
                if "hash mismatch" in lowered:
                    # Should be zero forever. Non-zero is an incident: the
                    # database and the caller disagree about approved bytes.
                    span.set_attribute("eve.sandbox.hash_mismatch", 1)
                    logger.error(
                        "sandbox refused %r on a source hash mismatch",
                        spec["tool_name"],
                    )

            # Count the use, best-effort. A tool approved and then used once
            # was a wasted approval; `eve-tool list` is where that shows up,
            # and it only shows up if dispatch records it.
            tool_id = spec.get("tool_id")
            if tool_id:
                try:
                    from eve.tools_authoring.store import record_invocation

                    await record_invocation(tool_id)
                except Exception:
                    logger.debug("could not count the invocation", exc_info=True)
            return result
        return await invoke(
            "mcp.invoke",
            {
                "server_id": spec["server_id"],
                "tool_name": spec["tool_name"],
                "arguments": kwargs,
            },
        )

    return StructuredTool.from_function(
        coroutine=_call,
        name=f"{spec['server_id']}_{spec['tool_name']}",
        description=spec["description"],
        args_schema=args_model,
    )
```

In `src/eve/skills/types.py`, widen `DynamicToolSpec` with the two optional
sandbox keys:

```python
class DynamicToolSpec(TypedDict, total=False):
    server_id: str
    tool_name: str
    description: str
    schema: dict  # JSON schema for the tool's arguments
    # Phase 5c, sandbox specs only. The source travels in state and in the
    # request so eve-sandbox needs no database credential.
    source: str
    source_sha256: str
    tool_id: str
```

`materialize.py` needs three new imports at the top:

```python
import logging
from time import perf_counter

from opentelemetry import trace

logger = logging.getLogger(__name__)
```

The lazy import of `record_invocation` inside `_call` is deliberate.
`eve.tools_authoring.registry` imports `eve.skills.types`, so a module-level
import here would put the two packages in a visible import loop even though
the individual modules do not actually cycle. Importing at call time keeps the
dependency direction legible.

> `total=False` makes every key optional, which is looser than before. If the
> stricter shape matters, split into two TypedDicts and a union instead; this
> plan takes the simpler route because `materialize` already tolerates missing
> keys via `.get`.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_tools_client.py tests/test_skills_materialize.py tests/test_skills_search.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/tools_client.py src/eve/skills/materialize.py src/eve/skills/types.py tests/
git commit -m "feat(5c): route sandbox specs through tools_client's new target"
```

---

## Task 8: Approved tools become discoverable

**Files:**
- Create: `src/eve/tools_authoring/registry.py`
- Modify: `src/eve/skills/registry.py`, `src/eve/skills/search.py`
- Test: `tests/test_skills_search.py`

**Interfaces:**
- Consumes: Task 2's `live_tools`, Phase 5a's registry arm.
- Produces: `tools_authoring.registry.sandbox_specs() -> list[DynamicToolSpec]`; `load_skills(..., sandbox_tools=None)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_skills_search.py`:

```python
async def test_search_skills_binds_an_approved_sandbox_tool(monkeypatch, tmp_path):
    from eve.skills import search as search_mod

    async def load_procedures(sub):
        return []

    async def sandbox_specs():
        return [
            {
                "server_id": "sandbox", "tool_name": "amortise",
                "description": "Amortise a loan.",
                "schema": {"properties": {"a": {"type": "integer"}}},
                "source": "def run(arguments):\n    return {}\n",
                "source_sha256": "a" * 64,
            }
        ]

    async def embed_query(text):
        return [1.0] + [0.0] * 1535

    monkeypatch.setattr(search_mod, "load_procedures", load_procedures)
    monkeypatch.setattr(search_mod, "sandbox_specs", sandbox_specs)
    monkeypatch.setattr(search_mod, "embed_query", embed_query)
    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()

    command = await search_mod.search_skills.ainvoke(
        {
            "query": "amortise a loan",
            "state": {"member": {"sub": "sub-noah"}, "dynamic_tools": []},
            "tool_call_id": "c1",
        }
    )
    specs = command.update["dynamic_tools"]
    assert any(s["server_id"] == "sandbox" for s in specs)
    assert "amortise" in command.update["messages"][0].content


async def test_no_sandbox_specs_are_offered_when_disabled(monkeypatch, tmp_path):
    """Fail closed: the kill switch must hold even against a spec the DB
    still lists as approved."""
    from eve.skills import search as search_mod

    async def load_procedures(sub):
        return []

    async def sandbox_specs():
        raise AssertionError("must not be consulted when disabled")

    async def embed_query(text):
        return [1.0] + [0.0] * 1535

    monkeypatch.setattr(search_mod, "load_procedures", load_procedures)
    monkeypatch.setattr(search_mod, "sandbox_specs", sandbox_specs)
    monkeypatch.setattr(search_mod, "embed_query", embed_query)
    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    command = await search_mod.search_skills.ainvoke(
        {
            "query": "amortise",
            "state": {"member": {"sub": "sub-noah"}, "dynamic_tools": []},
            "tool_call_id": "c1",
        }
    )
    assert command.update["dynamic_tools"] == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_skills_search.py -k sandbox -v`
Expected: FAIL — `search_mod` has no attribute `sandbox_specs`.

- [ ] **Step 3: Write the spec source**

Create `src/eve/tools_authoring/registry.py`:

```python
"""Approved tools as DynamicToolSpecs, so the Phase 3 dispatch path carries
them unchanged: search_skills ranks and binds, materialize builds the
callable, tools_client posts to eve-sandbox.

Never called when EVE_SANDBOX_ENABLED is false - the check lives at the call
site in search_skills so the kill switch holds even for a name the database
still lists as approved.
"""

from __future__ import annotations

from eve.skills.types import DynamicToolSpec
from eve.tools_authoring.store import live_tools


async def sandbox_specs() -> list[DynamicToolSpec]:
    return [
        DynamicToolSpec(
            server_id="sandbox",
            tool_name=row["name"],
            description=row["description"],
            schema=row["args_schema"] or {},
            source=row["source"],
            source_sha256=row["source_sha256"],
            # Carried so materialize can count the invocation. A tool used
            # once was a wasted approval, and that is only visible if
            # dispatch records it (design section 11).
            tool_id=str(row["id"]),
        )
        for row in await live_tools()
    ]
```

- [ ] **Step 4: Offer them in `search_skills`**

In `src/eve/skills/search.py`, add the import and consult the specs:

```python
from eve.tools_authoring.registry import sandbox_specs
```

Inside `search_skills`, after the `load_procedures` block:

```python
    # Fail closed on the kill switch: a thread can carry an approved spec in
    # state from before the flag flipped, and a switch a stale checkpoint can
    # route around is not a switch (design section 9).
    sandbox: list = []
    if get_settings().sandbox_enabled:
        try:
            sandbox = await sandbox_specs()
        except Exception:
            sandbox = []
    skills = load_skills(
        mcp_tools=[*registered_mcp_tools(), *sandbox], authored=authored
    )
```

Registering sandbox specs through the `mcp_tools` argument reuses the existing
`mcp_tool`-kind ranking and the `Command(update={"dynamic_tools": ...})` path
verbatim, so no new branch is needed in `search_skills`' body.

- [ ] **Step 5: Guard materialized specs at the tool node**

`dynamic_tools` in a checkpointed thread can still hold a sandbox spec after
the flag flips. In `src/eve/graph.py`, filter when materializing — in **both**
`eve` and `tools_node`:

```python
def _live_specs(state: EveState) -> list:
    """Drop sandbox specs when the kill switch is off, wherever they came
    from - including a thread checkpointed while it was on."""
    enabled = get_settings().sandbox_enabled
    return [
        spec
        for spec in state.get("dynamic_tools", [])
        if enabled or spec.get("server_id") != "sandbox"
    ]
```

and replace both `dynamic = [materialize(spec) for spec in state.get("dynamic_tools", [])]`
lines with `dynamic = [materialize(spec) for spec in _live_specs(state)]`.

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_skills_search.py tests/test_graph.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/eve/tools_authoring/registry.py src/eve/skills/search.py src/eve/graph.py tests/
git commit -m "feat(5c): discover approved sandbox tools, failing closed on the switch"
```

---

## Task 9: Bind `propose_tool` and the `eve-tool` CLI

**Files:**
- Create: `src/eve/tools_authoring/cli.py`
- Modify: `src/eve/graph.py`, `pyproject.toml`
- Test: `tests/test_graph.py`, `tests/test_tools_store.py`

**Interfaces:**
- Produces: `eve-tool list | approve <id> | reject <id> | revoke <name> | revoke --all`; `graph._static_tools()` includes `propose_tool` when `sandbox_enabled`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_graph.py`:

```python
def test_propose_tool_is_bound_when_the_sandbox_is_enabled(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "propose_tool" in {t.name for t in graph_mod._static_tools()}


def test_propose_tool_is_unbound_by_default(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "propose_tool" not in {t.name for t in graph_mod._static_tools()}
```

Add to `tests/test_tools_store.py`:

```python
async def test_cli_approve_and_revoke_round_trip(pool):
    from eve.tools_authoring import cli
    from eve.tools_authoring.store import live_tools, propose

    tool_id = await propose(
        name="amortise", description="d", args_schema={}, source=SOURCE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    assert await cli.approve_one(tool_id, "sub-noah") is True
    assert [t["name"] for t in await live_tools()] == ["amortise"]

    assert await cli.revoke_one("amortise", "no longer needed") == 1
    assert await live_tools() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_graph.py -k propose_tool -v`
Expected: FAIL — `propose_tool` is not in the static tool list.

- [ ] **Step 3: Bind it in the graph**

In `src/eve/graph.py`, add the import and extend `_static_tools` (which Phase
5a introduced):

```python
from eve.tools_authoring.propose import propose_tool
```

```python
def _static_tools() -> list:
    """Rebuilt per call rather than fixed at import: two settings gate two
    tools, and both `eve` and `tools_node` need the same answer within one
    turn. Settings are lru_cached, so this is a dict lookup."""
    settings = get_settings()
    tools = list(_BASE_TOOLS)
    if settings.self_authoring_enabled:
        tools.append(write_skill)
    if settings.sandbox_enabled:
        tools.append(propose_tool)
    return tools
```

- [ ] **Step 4: Write the CLI**

Create `src/eve/tools_authoring/cli.py`:

```python
"""`eve-tool`: review, approve, reject and revoke Eve-authored tool code.

Approving in a terminal rather than only in a chat thread matters for the
cases the interrupt does not cover: a proposal whose thread was abandoned, and
a revocation that has to happen now.
"""

from __future__ import annotations

import argparse
import asyncio

from eve.memory.db import close_pool
from eve.tools_authoring.inspect import check
from eve.tools_authoring.store import (
    all_tools,
    approve,
    by_id,
    reject,
    revoke,
    revoke_all,
)


async def approve_one(tool_id: str, approver: str) -> bool:
    """Re-check the source at approval time. The propose-time check already
    ran, but an approval is a statement about these bytes, so it is re-made
    against these bytes."""
    row = await by_id(tool_id)
    if row is None:
        raise SystemExit(f"no such tool: {tool_id}")
    result = check(row["source"])
    if not result.ok:
        raise SystemExit(
            "refusing to approve; the source fails its checks:\n"
            + "\n".join(f"  - {p}" for p in result.problems)
        )
    return await approve(tool_id, approver)


async def revoke_one(name: str, why: str) -> int:
    return await revoke(name, why)


def _status(row: dict) -> str:
    if row["revoked_at"]:
        return "revoked"
    if row["approved_at"]:
        return "live"
    if row["rejected_why"]:
        return "rejected"
    return "pending"


def _render(rows: list[dict]) -> str:
    if not rows:
        return "No tools proposed yet."
    lines = []
    for row in rows:
        lines.append(
            f"{row['id']}  {_status(row):<8}  {row['name']:<20} "
            f"invocations={row['invocations']}\n"
            f"    {row['description'][:90]}\n"
            f"    sha256={row['source_sha256'][:16]}...  by={row['proposed_by']}"
            f"  thread={row['source_thread'] or '-'}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    lister = sub.add_parser("list", help="show every proposal and its status")
    lister.add_argument("--source", help="also print the full source of this id")

    approver = sub.add_parser("approve", help="approve a proposal by id")
    approver.add_argument("id")
    approver.add_argument("--as", dest="approver", required=True)

    rejecter = sub.add_parser("reject", help="reject a proposal by id")
    rejecter.add_argument("id")
    rejecter.add_argument("--why", required=True)

    revoker = sub.add_parser("revoke", help="retire a live tool by name")
    revoker.add_argument("name", nargs="?")
    revoker.add_argument("--all", action="store_true")
    revoker.add_argument("--why", default="unspecified")

    args = parser.parse_args()

    async def _run() -> None:
        try:
            if args.command == "list":
                print(_render(await all_tools()))
                if args.source:
                    row = await by_id(args.source)
                    if row:
                        print(f"\n--- {row['name']} ---\n{row['source']}")
            elif args.command == "approve":
                ok = await approve_one(args.id, args.approver)
                print("approved" if ok else "not approved (already decided?)")
            elif args.command == "reject":
                await reject(args.id, args.why)
                print("rejected")
            else:
                if args.all:
                    print(f"revoked {await revoke_all(args.why)} tools")
                elif args.name:
                    print(f"revoked {await revoke_one(args.name, args.why)} tools")
                else:
                    raise SystemExit("give a name or --all")
        finally:
            await close_pool()

    asyncio.run(_run())
```

Add to `pyproject.toml`:

```toml
eve-tool = "eve.tools_authoring.cli:main"
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_graph.py -v && uv run pytest tests/test_tools_store.py -m integration -v && uv sync --quiet && uv run eve-tool --help`
Expected: PASS and help listing the four subcommands.

- [ ] **Step 6: Commit**

```bash
git add src/eve/tools_authoring/cli.py src/eve/graph.py pyproject.toml tests/
git commit -m "feat(5c): bind propose_tool and add the eve-tool review CLI"
```

---

## Task 10: Integration, the isolation assumption, and live checks

**Files:**
- Create: `tests/test_tools_integration.py`, `tests/test_sandbox_live.py`
- Modify: `tests/conftest.py`, `docker-compose.test.yml`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the tests**

Create `tests/test_tools_integration.py`:

```python
import pytest

pytestmark = pytest.mark.integration

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    monkeypatch.setenv("EVE_SANDBOX_ENABLED", "true")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_tool")
    yield p
    await db.close_pool()


async def test_eve_sandbox_imports_nothing_from_eve():
    """DoD 12. The sandbox is the one package that must be unable to reach
    anything: every import is a line that could be tricked into reading."""
    import pathlib

    offenders = []
    for path in pathlib.Path("src/eve_sandbox").rglob("*.py"):
        text = path.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import eve", "from eve")) and not stripped.startswith(
                ("import eve_sandbox", "from eve_sandbox")
            ):
                offenders.append(f"{path}: {stripped}")
    assert offenders == [], offenders


async def test_propose_approve_discover_invoke_end_to_end(pool, monkeypatch):
    """DoD 1, 3: the whole path, with the interrupt resolved by the CLI."""
    from eve.skills.materialize import materialize
    from eve.tools_authoring.cli import approve_one
    from eve.tools_authoring.registry import sandbox_specs
    from eve.tools_authoring.store import propose

    tool_id = await propose(
        name="amortise", description="Amortise a loan.",
        args_schema={"properties": {"a": {"type": "integer"}}},
        source=PURE, proposed_by="sub-noah", thread_id="t1", run_id="r1",
    )
    assert await approve_one(tool_id, "sub-noah") is True

    specs = await sandbox_specs()
    assert len(specs) == 1

    # Dispatch straight through the real executor rather than over HTTP: the
    # HTTP hop is covered by tests/test_sandbox_app.py.
    from eve_sandbox.execute import run_tool

    out = await run_tool(specs[0]["source"], specs[0]["source_sha256"], {"a": 41})
    assert out == {"result": {"n": 42}}

    built = materialize(specs[0])
    assert built.name == "sandbox_amortise"


async def test_a_changed_source_needs_a_fresh_approval(pool):
    """DoD 4: the old version keeps serving until the new one is approved."""
    from eve.tools_authoring.cli import approve_one
    from eve.tools_authoring.registry import sandbox_specs
    from eve.tools_authoring.store import propose

    first = await propose(
        name="amortise", description="v1", args_schema={}, source=PURE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await approve_one(first, "sub-noah")
    await propose(
        name="amortise", description="v2", args_schema={},
        source=PURE + "# v2\n", proposed_by="sub-noah",
        thread_id=None, run_id=None,
    )
    specs = await sandbox_specs()
    assert len(specs) == 1 and specs[0]["description"] == "v1"


async def test_approve_refuses_source_that_fails_its_checks(pool):
    """DoD 7's first half: the checker runs again at approval time, so a row
    edited between propose and approve cannot slip through."""
    from eve.tools_authoring.cli import approve_one
    from eve.tools_authoring.store import propose

    tool_id = await propose(
        name="reader", description="d", args_schema={},
        source="import os\n\ndef run(arguments):\n    return {'x': os.getcwd()}\n",
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    with pytest.raises(SystemExit):
        await approve_one(tool_id, "sub-noah")


async def test_impure_source_fails_on_process_constraints_not_the_checker(pool):
    """DoD 7's second half, and the claim §6.3 rests on: with the AST checker
    bypassed entirely, the process-level constraints still hold.

    The environment is empty, so a tool reading EVE_* finds nothing even
    though `import os` succeeded.
    """
    import hashlib

    from eve_sandbox.execute import run_tool

    source = (
        "def run(arguments):\n"
        "    import os\n"
        "    return {'eve_vars': sorted(k for k in os.environ if k.startswith('EVE_'))}\n"
    )
    out = await run_tool(source, hashlib.sha256(source.encode()).hexdigest(), {})
    assert out["result"]["eve_vars"] == []


async def test_revoke_takes_effect_with_no_restart(pool):
    """DoD 9: load_skills is rebuilt per call, so a revoke lands immediately."""
    from eve.tools_authoring.cli import approve_one, revoke_one
    from eve.tools_authoring.registry import sandbox_specs
    from eve.tools_authoring.store import propose

    tool_id = await propose(
        name="amortise", description="d", args_schema={}, source=PURE,
        proposed_by="sub-noah", thread_id=None, run_id=None,
    )
    await approve_one(tool_id, "sub-noah")
    assert len(await sandbox_specs()) == 1

    await revoke_one("amortise", "not needed")
    assert await sandbox_specs() == []
```

Create `tests/test_sandbox_live.py`:

```python
"""Checks that are only meaningful against the deployed pod.

Run by hand: `EVE_LIVE_TESTS=1 uv run pytest tests/test_sandbox_live.py -m live`
with kubectl pointed at the cluster. These verify the claims §6.2 makes about
the pod, which no in-process test can.
"""

import os
import subprocess

import pytest

pytestmark = pytest.mark.live

POD = "deploy/eve-sandbox"


def _exec(*command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "exec", POD, "--", *command],
        capture_output=True, text=True, timeout=60,
    )


@pytest.fixture(autouse=True)
def _require_live():
    if os.environ.get("EVE_LIVE_TESTS") != "1":
        pytest.skip("EVE_LIVE_TESTS is not 1")


def test_the_pod_has_no_service_account_token():
    """DoD 10. A token here is a path to the cluster API."""
    result = _exec("ls", "/var/run/secrets/kubernetes.io/serviceaccount")
    assert result.returncode != 0, result.stdout


def test_the_pod_cannot_reach_any_external_host():
    """DoD 10. Default-deny egress is the boundary, not a mitigation."""
    result = _exec(
        "python", "-c",
        "import socket;"
        "socket.setdefaulttimeout(5);"
        "socket.create_connection(('example.com', 80))",
    )
    assert result.returncode != 0
    assert "Errno" in result.stderr or "timed out" in result.stderr


def test_the_root_filesystem_is_read_only_except_tmp():
    """DoD 10."""
    assert _exec("sh", "-c", "touch /app/x").returncode != 0
    assert _exec("sh", "-c", "touch /tmp/x && rm /tmp/x").returncode == 0


def test_no_eve_environment_variables_are_present_beyond_the_api_key():
    result = _exec("printenv")
    leaked = [
        line for line in result.stdout.splitlines()
        if line.startswith("EVE_") and not line.startswith("EVE_SANDBOX_")
    ]
    assert leaked == [], leaked
```

- [ ] **Step 2: Add the sandbox to the test compose stack**

In `docker-compose.test.yml`, add an `eve-sandbox` service built from
`Dockerfile.eve-sandbox` on host port 18091 with
`EVE_SANDBOX_API_KEY=test-key-0123456789abcdef0123456789ab`, and a session
fixture in `tests/conftest.py` modelled on `eve_tools_server` that starts it
with `uv run uvicorn eve_sandbox.app:app --port 18091`, `start_new_session=True`,
and process-group teardown.

- [ ] **Step 3: Run**

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest tests/test_tools_integration.py -m integration -v
uv run pytest
```
Expected: PASS both. The `live` tests stay skipped.

- [ ] **Step 4: Commit**

```bash
git add tests/test_tools_integration.py tests/test_sandbox_live.py tests/conftest.py docker-compose.test.yml
git commit -m "test(5c): end-to-end approval path and the isolation assumption"
```

---

## Task 11: Documentation and the ADRs

**Files:**
- Create: `docs/adr/0010-sandboxed-tools-are-pure-functions.md`, `docs/adr/0011-alembic-with-a-private-version-table.md`
- Modify: `README.md`, `docs/architecture.md`, `.env.example`

- [ ] **Step 1: Write ADR 0010**

Create `docs/adr/0010-sandboxed-tools-are-pure-functions.md`:

```markdown
# 10. Sandboxed tools are pure functions, and the pod is the boundary

**Status:** Accepted
**Date:** 2026-08-27

## Context

Phase 5c lets Eve author executable code. A human approves it, but approvers
are sometimes wrong, so the interesting question is not "will the gate ever
fail" — it will — but "what does a wrong approval get you."

The obvious design gives tool code a network allowlist and a scoped
credential. That makes the approval gate load-bearing: every guarantee then
rests on a human reading a diff correctly at 11pm.

## Decision

**A sandbox tool is a pure function.** No network, no filesystem beyond a
per-call tmpfs, no environment variables, no credentials, no cluster identity.
Not an allowlist — none. A tool needing a credential is an `eve-tools`
handler, written by a human in a pull request.

Enforcement is layered, and the layers are not equal:

1. **The pod** — default-deny egress `NetworkPolicy`, no ServiceAccount token,
   no secret mounts, read-only root filesystem, non-root UID. **This is the
   security boundary.**
2. **The process** — subprocess in isolated mode with an empty environment,
   a tmpfs cwd, and rlimits on CPU, address space and core dumps.
3. **The AST allowlist** — explicitly *not* a security boundary. It is an
   accident guard and a feedback mechanism: it gives Eve an actionable error
   so she can revise before bothering a human, and it makes the approver's
   read short.

Every guarantee must hold with layer 3 assumed defeated, and the tests are
written from that assumption.

## Consequences

The remaining hostile capability of a maximally-malicious approved tool is:
burn one CPU second, allocate some memory, return a wrong answer. The first
two are bounded; the third is a correctness problem, which is what the
approval gate is actually for.

The cost is a narrower capability than "Eve writes her own tools" suggests.
A sandbox tool computes over data Eve already has: parse this iCal blob,
amortise these numbers, reformat this. Eve fetches with `eve-tools` and
computes with `eve-sandbox`. That is still a real gain — she currently does
arithmetic and parsing inside a language model, badly and unverifiably.

This extends ADR 0006 by symmetry rather than amending it: `eve-tools` holds
every credential and runs only human-written code; `eve-sandbox` runs
machine-written code and holds nothing. Two services because their invariants
are exact opposites, and one service satisfying both satisfies neither.
```

- [ ] **Step 2: Write ADR 0011**

Create `docs/adr/0011-alembic-with-a-private-version-table.md`:

```markdown
# 11. Eve's migrations use Alembic with a private version table

**Status:** Accepted
**Date:** 2026-08-27

## Context

Phase 2 hand-rolled migrations as an ordered list in `db.py`, for two stated
reasons: Aegra already runs its own Alembic migrations at startup and ours
must not interleave with them, and there were only four tables. The module's
own comment set the review trigger: "Move to Alembic if MIGRATIONS exceeds ~5
entries."

Phase 5b brought it to exactly 5. `eve_tool` is the sixth.

## Decision

Eve's schema moves to Alembic with its own `script_location` and, critically,
`version_table="eve_alembic_version"`. Revision one reproduces the five
hand-rolled entries idempotently — every statement is `IF NOT EXISTS` — so it
is a no-op against an already-migrated database and a full create against a
fresh one. Revision two adds `eve_tool`.

`eve-migrate` keeps its name and its contract: run before `aegra serve`, fail
the pod loudly on a schema problem. It now shells out to `alembic upgrade
head` under the same advisory lock the ordered list used, so two pods starting
at once still cannot race.

`eve_schema_version` is left in place and unused. Dropping it would make a
rollback to the previous image fail on a table it expects.

## Consequences

The original constraint is preserved, not abandoned: two independent migration
histories against one database, each with its own version table, cannot stamp
over each other. What changes is that Eve gains ordering, autogeneration, and
downgrades for the schema changes past this point.

The risk this introduces is baseline drift — revision one must reproduce every
object the five entries created, or a fresh deployment differs from an upgraded
one. The plan includes an object-set diff against `db.MIGRATIONS` for exactly
that reason, and the integration test asserts every Phase 1–5b table exists
after a migrate from empty.
```

- [ ] **Step 3: Update `.env.example`**

```bash
# Phase 5c (Gated tool code). Off by default. See docs/superpowers/specs/
# 2026-08-27-eve-sandboxed-tools-design.md section 10
EVE_SANDBOX_ENABLED=false
EVE_SANDBOX_BASE_URL=http://eve-sandbox:8091
EVE_SANDBOX_API_KEY=
EVE_SANDBOX_TIMEOUT_SECONDS=5
EVE_SANDBOX_MEMORY_MB=256
EVE_SANDBOX_MAX_OUTPUT_BYTES=65536
EVE_SANDBOX_MAX_CONCURRENCY=4
```

- [ ] **Step 4: Update `docs/architecture.md`**

1. The opening line becomes Phase 5c, and notes the program is complete.
2. Module map gains `src/eve/tools_authoring/` and `src/eve_sandbox/` blocks.
3. The import-graph paragraph: `eve_sandbox` imports nothing from `eve`, and
   `eve/graph.py` now depends on `eve.tools_authoring.propose`.
4. Replace the migrations paragraph: Alembic, private version table, and
   `eve-migrate`'s unchanged contract.
5. A "Sandboxed tools" section: the propose → interrupt → approve → dispatch
   path, the three enforcement layers with the AST check explicitly named as
   *not* the boundary, and the `eve-tool` CLI.
6. The deployment section gains `eve-sandbox` (no Ingress; reachable only from
   `eve`) and `Dockerfile.eve-sandbox`.

- [ ] **Step 5: Update `README.md`**

Mark 5a/5b/5c delivered; state the program is complete. Add the four permanent
boundaries from the spec's §15 (Eve does not approve her own code, does not
author credentialed capability, does not rewrite her persona, does not learn
unsupervised).

- [ ] **Step 6: Verify**

```bash
uv run pytest
uv run python -c "import eve_sandbox.app; print('sandbox imports clean')"
grep -c "eve-sandbox\|tools_authoring" docs/architecture.md
```
Expected: PASS, the import line, and a non-zero count.

- [ ] **Step 7: Commit**

```bash
git add docs/adr/0010-sandboxed-tools-are-pure-functions.md docs/adr/0011-alembic-with-a-private-version-table.md README.md docs/architecture.md .env.example
git commit -m "docs(5c): ADRs 0010 and 0011, architecture, README and env"
```

---

## Definition of Done Traceability

| Spec criterion | Task |
|---|---|
| 1. Propose → pause → approver sees everything → approve or reject | 4, 10 |
| 2. A rejected proposal is recorded and does not execute; no auto-retry | 4 |
| 3. Approved tool found, bound, invoked, correct result | 8, 7, 10 |
| 4. Editing needs fresh approval; old version serves until then | 2, 10 |
| 5. A source-hash mismatch is refused and logged | 5, 6 |
| 6. No `tools.author` → cannot propose | 4 |
| 7. `os` / socket / file access fails on pod constraints with the checker bypassed | 5, 10 (in-process), 10's live tests (the pod) |
| 8. Timeout, memory and output limits each produce an error and a live service | 5, 6 |
| 9. `eve-tool revoke` with no restart; `ENABLED=false` fails closed for a checkpointed spec | 8, 9, 10 |
| 10. Deployed pod: no SA token, no secret mounts, read-only rootfs, no egress | 10's live tests + `infrastructure` |
| 11. Alembic in place, no-op on a migrated DB, not sharing Aegra's version table | 1, 10 |
| 12. `eve_sandbox` imports nothing from `eve` | 10 |

**Criteria 7 and 10 are only fully verified against the cluster.** The
in-process tests check what a process can check (empty environment, limits,
hash refusal); the `live`-marked tests in `tests/test_sandbox_live.py` check
the pod's own constraints, which is where §6.2's actual boundary lives. Do not
mark 5c done on the unit suite alone.

**Prerequisite P2 is `infrastructure` work, not in this plan.** The Deployment,
Service, default-deny-egress `NetworkPolicy`, and Gatus check land in that
repository. Task 10's live tests are what verify that work was done correctly.
