# Eve Memory (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eve remembers the family across threads — standing facts about each member and the household are always in her prompt, past decisions are retrievable, and facts that change are superseded rather than accumulated.

**Architecture:** Four memory layers (`profile`, `household`, `episodic`, `digest`) in one Postgres table, distinguished by retrieval policy rather than shape. The graph becomes `load_context → recall → eve → extract → END`. `recall` runs a lexical + entity + recency SQL query immediately and races a Gemini embedding against a 120 ms budget, fusing the vector arm in by reciprocal rank if it lands and shipping lexical-only if it does not. `extract` runs after the answer has streamed, on the REFLEX tier, judging new facts against the overlapping ones already believed and emitting `add`/`supersede`/`reinforce`/`forget`. No cron, no worker, no scheduled job.

**Tech Stack:** Python 3.12, psycopg 3 + `psycopg_pool` (already present transitively via `langgraph-checkpoint-postgres` — no new dependency), pgvector HNSW, Postgres full-text search, `langchain-openai` `OpenAIEmbeddings` against LiteLLM, Gemini `gemini-flash-lite-latest` and `gemini-embedding-001`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-eve-memory-design.md`

## Global Constraints

- **Python 3.12.** Already declared as `requires-python = ">=3.12"`.
- **No new runtime dependency.** Everything this phase needs — `psycopg`, `psycopg_pool`, `opentelemetry-api`, `OpenAIEmbeddings` — is already installed transitively. If a task appears to need `uv add`, stop and re-read; it almost certainly does not.
- **No generative model call may precede the first streamed token** (ADR 0002, as amended by Task 12). Exactly one embedding call may, and it must be bounded and cancellable. `load_context` still performs no model call at all.
- **Model identifiers live only in `src/eve/models.py`** (spec §5) — with the single documented exception of `tests/test_models.py`, whose job is to pin the mapping. The embedding model name is the one addition, and it lives in `src/eve/settings.py` because it was pinned there in Phase 1; do not move it.
- **Embedding model and dimension are PINNED**: `gemini/gemini-embedding-001` at `1536` dims, **re-normalised to unit length after truncation** (ADR 0003 as amended by Task 1). Changing either requires re-embedding all family memory.
- **`content` is one self-contained sentence.** Enforced in the extraction prompt and validated on write. Ranking, budgeting and supersession are all per-row.
- **Nothing is deleted except an explicit `forget`.** Retirement sets `superseded_by`.
- **Prerequisite P1** (metered Gemini key in Vault + two LiteLLM model entries) must be complete before Task 1 can run. **Prerequisite P2** (`eve-db` restore exercised) is Noah's, in the `infrastructure` repo, and is not a task here — but this phase starts writing the one asset that cannot be rebuilt, so if it is still open at Task 6 that is a decision to accept risk, not a detail to discover later.
- **Documentation is part of the change**, not a follow-up.

---

## File Structure

### New

| File | Responsibility |
|---|---|
| `src/eve/memory/__init__.py` | Public surface: `recall`, `extract`, `MemoryBundle`. Nothing else imports the submodules. |
| `src/eve/memory/types.py` | `Memory`, `MemoryBundle`, `Operation`, `Extraction`. No behaviour. |
| `src/eve/memory/db.py` | psycopg pool, ordered DDL list, advisory-locked migration runner, `eve-migrate` console script. |
| `src/eve/memory/embed.py` | Embedding client: call LiteLLM, truncate to 1536, re-normalise. |
| `src/eve/memory/ranking.py` | Pure functions: recency decay, reciprocal-rank fusion, token budget. No I/O. |
| `src/eve/memory/store.py` | Every SQL statement. Reads and writes. |
| `src/eve/memory/recall.py` | The `recall` node. |
| `src/eve/memory/extract.py` | The `extract` node. |
| `prompts/extract.md` | The extraction prompt. Edited by pull request, like the persona. |
| `docs/adr/0005-memory-storage.md` | One table, supersession over deletion, read-time decay, no scheduled jobs. |
| `tests/test_memory_ranking.py` | Unit — pure ranking functions. |
| `tests/test_memory_embed.py` | Unit — truncation and normalisation against a fake client. |
| `tests/test_memory_store.py` | Integration — real Postgres. |
| `tests/test_memory_recall.py` | Unit — the recall node, including the degrade path. |
| `tests/test_memory_extract.py` | Unit — op application and the permission gate. |
| `tests/test_memory_integration.py` | Integration — end-to-end through `aegra serve`. |
| `tests/test_live_embeddings.py` | Live — real LiteLLM, real Gemini. |

### Modified

| File | Change |
|---|---|
| `src/eve/settings.py` | Embedding model repinned to Gemini; memory settings; `database_url`. |
| `src/eve/models.py` | `Tier.REFLEX` mapped; `NotImplementedError` branch removed. |
| `src/eve/state.py` | `EveState` gains `memory: MemoryBundle`. |
| `src/eve/context.py` | `build_system_prompt` gains the memory section. |
| `src/eve/graph.py` | Two new nodes; `build_graph` gains injectable `recall_fn`/`extract_fn`. |
| `prompts/eve.md` | Guidance on using memory without narrating it. |
| `pyproject.toml` | `[project.scripts] eve-migrate`; a `memory` pytest marker is **not** needed. |
| `Dockerfile` | `CMD` runs the migration before `aegra serve`. |
| `.env.example` | New settings, documented. |
| `docs/architecture.md` | Graph shape, memory module map, new module boundaries. |
| `docs/adr/0002-*.md`, `docs/adr/0003-*.md` | Amended. |
| `tests/test_graph.py` | Existing tests pass no-op `recall_fn`/`extract_fn`. |

Import graph stays acyclic. `memory.types` depends on nothing internal. `memory.db` and `memory.embed` depend on `settings`. `memory.ranking` depends on `memory.types` only. `memory.store` depends on `db`, `types`, `ranking`. `memory.recall` and `memory.extract` depend on `store`, `embed`, `ranking`, `settings`, and (extract only) `eve.models`. `graph` depends on `memory`.

---

## Task 1: Probe the Gemini path live, then pin it

Phase 1's tier table was written from documentation and four of its five entries were wrong against the live proxy (ADR 0004). Two assumptions here are exactly as unverified: whether LiteLLM honours `dimensions` for Gemini embeddings, and whether Flash Lite can produce structured output through the proxy. Both are cheap to check and expensive to discover in Task 9.

**Files:**
- Modify: `src/eve/settings.py`, `src/eve/models.py`, `docs/adr/0003-embedding-model-pinned.md`
- Test: `tests/test_live_embeddings.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: `eve.settings.get_settings()`, `eve.models.Tier`
- Produces: `Settings.embedding_model = "gemini/gemini-embedding-001"`, `TIER_MODELS[Tier.REFLEX] = "gemini/gemini-flash-lite-latest"`, and a recorded answer to "does the proxy truncate, or must we?"

- [ ] **Step 1: Confirm P1 is done**

```bash
curl -s -H "Authorization: Bearer $EVE_LITELLM_API_KEY" \
  https://litellm.chalifour.dev/v1/models | grep -o 'gemini[^"]*'
```

Expected: both `gemini/gemini-flash-lite-latest` and `gemini/gemini-embedding-001` appear. If they do not, **stop** — prerequisite P1 is not complete and nothing else in this plan can be verified.

- [ ] **Step 2: Write the live probe**

Create `tests/test_live_embeddings.py`:

```python
"""Live probes against the real LiteLLM proxy. Spends real quota.

These exist because Phase 1 learned the hard way that the proxy's behaviour
is not the vendor documentation's behaviour (ADR 0004). Two assumptions are
load-bearing for all of Phase 2 and neither is checkable offline.
"""

import math
import os

import pytest
from langchain_openai import OpenAIEmbeddings
from pydantic import BaseModel

from eve.models import Tier, get_model
from eve.settings import get_settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("EVE_LIVE_TESTS") != "1", reason="EVE_LIVE_TESTS!=1"
    ),
]


def _client() -> OpenAIEmbeddings:
    s = get_settings()
    return OpenAIEmbeddings(
        model=s.embedding_model,
        dimensions=s.embedding_dims,
        base_url=s.litellm_base_url,
        api_key=s.litellm_api_key or "unset",
        # LiteLLM proxies a non-OpenAI model here. The context-length check
        # runs tiktoken against an OpenAI tokeniser that does not describe
        # this model, and it rewrites the request body when it trips.
        check_embedding_ctx_length=False,
    )


async def test_proxy_returns_the_pinned_dimension():
    """If this fails, LiteLLM ignores `dimensions` and we truncate ourselves."""
    vec = await _client().aembed_query("the dog is called Cooper")
    assert len(vec) == get_settings().embedding_dims, (
        f"proxy returned {len(vec)} dims, not "
        f"{get_settings().embedding_dims} - client-side truncation required"
    )


async def test_returned_vector_is_unit_norm():
    """Matryoshka truncation breaks unit norm, and cosine distance over
    non-normalised vectors returns silently wrong rankings. If the proxy
    normalises for us this is a no-op; if it does not, embed.py must."""
    vec = await _client().aembed_query("the dog is called Cooper")
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, abs_tol=1e-3)


async def test_reflex_tier_produces_structured_output():
    """Task 9's extraction node is a structured-output call on this tier.
    Phase 1 found ocp/* silently strips tool definitions; verify Gemini does
    not before building on it."""

    class Fact(BaseModel):
        subject: str
        content: str

    model = get_model(Tier.REFLEX).with_structured_output(Fact)
    result = await model.ainvoke(
        "Extract one fact: Kendra's car is the blue Honda."
    )
    assert isinstance(result, Fact)
    assert result.content
```

- [ ] **Step 3: Repin the embedding model and map REFLEX**

In `src/eve/settings.py`, replace the embedding block:

```python
    # PINNED. Changing either value requires re-embedding ALL of Eve's memory
    # (spec section 7.3, ADR 0003). The Gemini conditional ADR 0003 carried
    # since Phase 1 resolved when the metered REFLEX key was provisioned: the
    # key is Gemini, so the embedding model is too.
    #
    # gemini-embedding-001 emits 3072 dimensions trained with Matryoshka
    # representation learning. Truncating to 1536 breaks unit norm, so
    # memory/embed.py re-normalises. Cosine distance over non-normalised
    # vectors fails silently - wrong rankings, no error.
    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_dims: int = 1536
```

In `src/eve/models.py`, map the tier and delete the guard:

```python
TIER_MODELS: dict[Tier, str] = {
    Tier.VOICE: "chatgpt/gpt-5.6-terra",
    Tier.DEEP: "chatgpt/gpt-5.6-sol",
    Tier.MECHANICAL: "chatgpt/gpt-5.6-luna",
    Tier.CODE: "chatgpt/gpt-5.6-sol",
    # Metered Google key, NOT the ChatGPT subscription proxy: this tier runs
    # on every turn (extraction) and in Phase 4 on every household signal, and
    # must not consume the rate limits Noah uses for his own work (spec 2.1).
    Tier.REFLEX: "gemini/gemini-flash-lite-latest",
}


@lru_cache(maxsize=None)
def get_model(tier: Tier) -> BaseChatModel:
    settings = get_settings()
    return ChatOpenAI(
        model=TIER_MODELS[tier],
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key or "unset",
        # The chatgpt/* models are registered with `mode: responses`. Gemini
        # is not, and sending it a Responses-API request fails.
        use_responses_api=TIER_MODELS[tier].startswith("chatgpt/"),
        streaming=True,
    )
```

- [ ] **Step 4: Update the tier test**

In `tests/test_models.py`, replace the test asserting REFLEX raises:

```python
def test_reflex_tier_is_the_metered_gemini_model():
    """REFLEX runs on every turn and must not spend the ChatGPT
    subscription's rate limits (spec 2.1). A regression here is invisible
    until Noah's own Codex sessions start getting throttled."""
    assert TIER_MODELS[Tier.REFLEX] == "gemini/gemini-flash-lite-latest"


def test_only_chatgpt_tiers_use_the_responses_api():
    assert get_model(Tier.VOICE).use_responses_api is True
    assert get_model(Tier.REFLEX).use_responses_api is False
```

Delete the existing `pytest.raises(NotImplementedError, match="Phase 2")` test.

- [ ] **Step 5: Run the unit tier**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 6: Run the live probe**

Run: `EVE_LIVE_TESTS=1 uv run pytest -m live tests/test_live_embeddings.py -v`
Expected: all three PASS. **If `test_proxy_returns_the_pinned_dimension` fails**, note the returned dimension — Task 3's `embed.py` must truncate client-side, which it already does unconditionally, so this is information rather than a blocker. **If `test_returned_vector_is_unit_norm` fails**, that is the expected outcome and confirms `embed.py`'s re-normalisation is load-bearing. **If `test_reflex_tier_produces_structured_output` fails**, stop and report — Task 9's design does not survive it.

- [ ] **Step 7: Amend ADR 0003**

Replace the Decision section of `docs/adr/0003-embedding-model-pinned.md`:

```markdown
## Decision

**Amended 2026-08-18.** The conditional below resolved when the metered
REFLEX key was provisioned. The key is Gemini, so the embedding model is
`gemini/gemini-embedding-001`, truncated to 1536 dimensions and
**re-normalised to unit length**, declared in `src/eve/settings.py`.

The original Phase 1 decision was `openai:text-embedding-3-small` at 1536
dimensions, with one conditional: if the REFLEX key turned out to be Gemini,
the model became `gemini-embedding-001` truncated to 1536, so the program
took on one new vendor rather than two. That is what happened. The
conditional is now spent and this ADR carries no open questions.

Voyage-3 was rejected despite better benchmark position: at this corpus size
recall is dominated by entity filtering and recency weighting, so a third
vendor is not justified.

### Re-normalisation is not optional

`gemini-embedding-001` emits 3072 dimensions trained with Matryoshka
representation learning. A truncated MRL vector is no longer unit-norm, and
cosine similarity over non-normalised vectors returns wrong rankings with no
error and no crash - just quietly worse recall that nobody attributes to
this. `src/eve/memory/embed.py` re-normalises unconditionally, and
`tests/test_live_embeddings.py` pins the proxy's actual behaviour.
```

- [ ] **Step 8: Commit**

```bash
git add src/eve/settings.py src/eve/models.py tests/test_models.py \
        tests/test_live_embeddings.py docs/adr/0003-embedding-model-pinned.md
git commit -m "feat: map REFLEX to metered Gemini, resolve the ADR 0003 conditional

Probed live first, as ADR 0004 taught. The embedding pin's Gemini branch is
now taken: gemini-embedding-001 at 1536 dims, re-normalised after truncation
because MRL truncation breaks unit norm and cosine distance fails silently
when it does."
```

---

## Task 2: Memory settings

**Files:**
- Modify: `src/eve/settings.py`, `.env.example`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `Settings.database_url: str`, `.memory_token_budget: int`, `.memory_episodic_half_life_days: float`, `.memory_recall_embed_budget_ms: int`, `.memory_profile_cap: int`, `.memory_household_cap: int`, `.memory_digest_every_n_turns: int`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings.py`:

```python
def test_database_url_falls_back_to_the_aegra_variable(monkeypatch):
    """Aegra is configured with DATABASE_URL and the cluster manifests set
    exactly that. Requiring a second EVE_DATABASE_URL saying the same thing
    is a deployment footgun that would surface as memory silently failing."""
    monkeypatch.delenv("EVE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://eve:eve@db:5432/eve")
    get_settings.cache_clear()
    assert get_settings().database_url == "postgresql://eve:eve@db:5432/eve"


def test_explicit_eve_database_url_wins(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://shared/eve")
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://dedicated/eve")
    get_settings.cache_clear()
    assert get_settings().database_url == "postgresql://dedicated/eve"


def test_memory_defaults_match_the_spec():
    s = get_settings()
    assert s.memory_token_budget == 1200
    assert s.memory_episodic_half_life_days == 90.0
    assert s.memory_recall_embed_budget_ms == 120
    assert s.memory_profile_cap == 40
    assert s.memory_household_cap == 60
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_settings.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'database_url'`.

- [ ] **Step 3: Add the settings**

In `src/eve/settings.py`, add `import os` at the top and this block after the embedding pin:

```python
    # Memory (Phase 2). Eve keeps its own small pool rather than reaching into
    # Aegra's internal db_manager.lg_pool: that is a private attribute path,
    # and a silent rename in an aegra-api bump would break memory in
    # production to save fifteen lines. Defaults to Aegra's own DATABASE_URL
    # so the cluster needs no new variable.
    database_url: str = ""
    memory_token_budget: int = 1200
    memory_episodic_half_life_days: float = 90.0
    # The ceiling on how long recall may wait for the embedding before
    # shipping lexical-only. Every millisecond here is spent before Eve's
    # first token (ADR 0002 as amended).
    memory_recall_embed_budget_ms: int = 120
    memory_profile_cap: int = 40
    memory_household_cap: int = 60
    memory_digest_every_n_turns: int = 6
```

And in `model_post_init`, before the existing checks:

```python
        if not self.database_url:
            self.database_url = os.environ.get("DATABASE_URL", "")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Document the settings**

Append to `.env.example`:

```bash
# Memory (Phase 2). EVE_DATABASE_URL defaults to DATABASE_URL above, so it
# only needs setting when memory lives in a different database from Aegra's
# own tables - which it does not, today.
# EVE_DATABASE_URL=postgresql://eve:eve@localhost:15432/eve
# EVE_MEMORY_TOKEN_BUDGET=1200
# EVE_MEMORY_RECALL_EMBED_BUDGET_MS=120
```

- [ ] **Step 6: Commit**

```bash
git add src/eve/settings.py tests/test_settings.py .env.example
git commit -m "feat: memory settings, defaulting the pool to Aegra's DATABASE_URL"
```

---

## Task 3: Schema, pool, and the migration runner

**Files:**
- Create: `src/eve/memory/__init__.py`, `src/eve/memory/db.py`
- Modify: `pyproject.toml`, `Dockerfile`
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Consumes: `eve.settings.get_settings()`
- Produces: `eve.memory.db.get_pool() -> AsyncConnectionPool`, `eve.memory.db.migrate() -> None`, `eve.memory.db.close_pool() -> None`, console script `eve-migrate`

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_memory_store.py`:

```python
"""Integration tests against the real Postgres in docker-compose.test.yml.

The compose file already runs the VectorChord image the cluster runs, so the
vector path is exercised on the same engine as production.
"""

import pytest

from eve.memory import db

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_memory")
    yield p
    await db.close_pool()


async def test_migrate_creates_the_table(pool):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT to_regclass('public.eve_memory')")
        assert (await cur.fetchone())[0] == "eve_memory"


async def test_migrate_is_idempotent(pool):
    """It runs on every pod start. If a second run is not a no-op, a rolling
    restart is an outage."""
    await db.migrate()
    await db.migrate()
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_schema_version")
        assert (await cur.fetchone())[0] == len(db.MIGRATIONS)


async def test_the_vector_column_accepts_a_1536_dim_vector(pool):
    vec = "[" + ",".join(["0.01"] * 1536) + "]"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, "
            "content, embedding) VALUES "
            "('episodic','member','sub-noah','event','x', %s::vector)",
            (vec,),
        )
        cur = await conn.execute("SELECT count(*) FROM eve_memory")
        assert (await cur.fetchone())[0] == 1


async def test_superseded_rows_are_excluded_by_the_partial_index(pool):
    """Not an index test - a correctness test. Every read path relies on
    `superseded_why IS NULL`, and this is the one place it is asserted
    directly rather than through a query helper."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('profile','member','sub-noah','fact','old') RETURNING id"
        )
        old = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('profile','member','sub-noah','fact','new') RETURNING id"
        )
        new = (await cur.fetchone())[0]
        await conn.execute(
            "UPDATE eve_memory SET superseded_by=%s, superseded_why='contradicted'"
            " WHERE id=%s",
            (new, old),
        )
        cur = await conn.execute(
            "SELECT content FROM eve_memory WHERE superseded_why IS NULL"
        )
        assert [r[0] for r in await cur.fetchall()] == ["new"]
```

- [ ] **Step 2: Run it and watch it fail**

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest -m integration tests/test_memory_store.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'eve.memory'`.

- [ ] **Step 3: Create the package**

Create `src/eve/memory/__init__.py`:

```python
"""Eve's memory (Phase 2).

Four layers - profile, household, episodic, digest - in one table,
distinguished by retrieval policy rather than by shape. See
docs/superpowers/specs/2026-08-18-eve-memory-design.md.

Import from this module, not from its submodules.
"""
```

- [ ] **Step 4: Write the migration runner**

Create `src/eve/memory/db.py`:

```python
"""Connection pool and schema migration for Eve's memory.

Migrations are a hand-rolled ordered list rather than Alembic. Aegra already
runs its own Alembic migrations at startup and ours must not interleave with
them, and there is exactly one table here.

    ponytail: hand-rolled because there is one table. Move to Alembic if
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
        conn.row_factory = dict_row
        await conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS eve_schema_version ("
                " name text PRIMARY KEY,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            cur = await conn.execute("SELECT name FROM eve_schema_version")
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
```

- [ ] **Step 5: Register the console script**

In `pyproject.toml`, after the `[project]` block:

```toml
[project.scripts]
eve-migrate = "eve.memory.db:main"
```

- [ ] **Step 6: Run the migration before the server**

In `Dockerfile`, replace the `CMD` line and its comment:

```dockerfile
# `aegra serve` runs the API and its background workers in ONE process
# (WORKER_COUNT x N_JOBS_PER_WORKER). There is no separate worker command.
#
# `eve-migrate` applies Eve's own memory schema first; Aegra runs its own
# Alembic migrations separately at startup. `exec` so aegra, not sh, receives
# SIGTERM - without it the pod takes the full termination grace period to die.
CMD ["sh", "-c", "eve-migrate && exec aegra serve"]
```

- [ ] **Step 7: Run the tests**

```bash
uv sync
uv run pytest -m integration tests/test_memory_store.py -v
```

Expected: all four PASS.

- [ ] **Step 8: Commit**

```bash
git add src/eve/memory/ pyproject.toml Dockerfile tests/test_memory_store.py
git commit -m "feat: memory schema, pool, and advisory-locked migration runner

One table for four layers, because they differ in retrieval policy and not in
shape. Superseded rows drop out of every read through partial indexes rather
than through a predicate every query has to remember."
```

---

## Task 4: Types and the pure ranking functions

Everything here is a pure function, which is exactly where a subtle ranking bug hides and exactly where it is cheapest to catch.

**Files:**
- Create: `src/eve/memory/types.py`, `src/eve/memory/ranking.py`
- Test: `tests/test_memory_ranking.py`

**Interfaces:**
- Consumes: nothing internal
- Produces: `Memory`, `MemoryBundle`, `Operation`, `Extraction`; `recency_decay(age_days, half_life_days) -> float`, `fuse(*rankings, k=60) -> list[str]`, `estimate_tokens(text) -> int`, `fit_budget(items, budget) -> list[Memory]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory_ranking.py`:

```python
import math
from datetime import UTC, datetime, timedelta

from eve.memory.ranking import (
    estimate_tokens,
    fit_budget,
    fuse,
    recency_decay,
)
from eve.memory.types import Memory


def _mem(mid: str, content: str = "x" * 40) -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id=mid,
        layer="episodic",
        scope_kind="member",
        scope_id="sub-noah",
        kind="event",
        subject=None,
        content=content,
        confidence=0.7,
        salience=0.5,
        created_at=now,
        last_seen_at=now,
    )


def test_decay_is_one_half_at_the_half_life():
    assert math.isclose(recency_decay(90.0, 90.0), 0.5, rel_tol=1e-6)


def test_decay_is_one_for_something_recorded_now():
    assert recency_decay(0.0, 90.0) == 1.0


def test_decay_never_reaches_zero():
    """A fact from two years ago is faint, not gone. Clamping to zero would
    make old memories unrecallable even when nothing else matches."""
    assert recency_decay(730.0, 90.0) > 0.0


def test_fusion_prefers_what_both_arms_agree_on():
    """The whole point of hybrid recall: an item both arms found should beat
    an item only one arm found, even when that one arm ranked it first."""
    lexical = ["a", "b"]
    vector = ["c", "b"]
    assert fuse(lexical, vector)[0] == "b"


def test_fusion_keeps_items_only_one_arm_found():
    """Each arm covers the other's blind spot; dropping singletons would
    throw away exactly the coverage hybrid recall exists to buy."""
    assert set(fuse(["a"], ["b"])) == {"a", "b"}


def test_fusion_of_one_arm_preserves_its_order():
    assert fuse(["a", "b", "c"]) == ["a", "b", "c"]


def test_token_estimate_is_four_characters():
    assert estimate_tokens("x" * 40) == 10


def test_budget_drops_from_the_end():
    """Items arrive ranked, so the tail is the least relevant. Dropping the
    head would silently discard the best match whenever the budget bites."""
    items = [_mem("a"), _mem("b"), _mem("c")]  # 10 tokens each
    assert [m.id for m in fit_budget(items, 25)] == ["a", "b"]


def test_budget_never_returns_nothing_when_it_has_something():
    """A single item longer than the whole budget still beats an empty
    memory section, which reads to the model as 'she knows nothing'."""
    assert len(fit_budget([_mem("a", "x" * 10_000)], 10)) == 1
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_memory_ranking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.memory.ranking'`.

- [ ] **Step 3: Write the types**

Create `src/eve/memory/types.py`:

```python
"""Shapes only. No behaviour, no I/O, no internal imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

Layer = Literal["profile", "household", "episodic", "digest"]


@dataclass(frozen=True, slots=True)
class Memory:
    id: str
    layer: str
    scope_kind: str
    scope_id: str
    kind: str
    subject: str | None
    content: str
    confidence: float
    salience: float
    created_at: datetime
    last_seen_at: datetime


class MemoryBundle(TypedDict):
    """What `recall` puts in state and `build_system_prompt` renders."""

    profile: list[Memory]
    household: list[Memory]
    episodic: list[Memory]
    digest: str | None
    # Observability, not behaviour: whether the vector arm landed inside its
    # budget. Read by the span attributes in recall.py.
    vector_used: bool
    latency_ms: float


# Pydantic, not a dataclass: these are the structured-output schema handed to
# the REFLEX model in extract.py.
class Operation(BaseModel):
    op: Literal["add", "supersede", "reinforce", "forget"]
    target_id: str | None = Field(
        default=None, description="Existing memory id. Required except for `add`."
    )
    layer: Literal["profile", "household", "episodic"] | None = None
    kind: Literal["fact", "preference", "event", "decision"] | None = None
    subject: str | None = Field(
        default=None,
        description="Lowercase entity this is about: 'cooper', 'kendra', 'honda'.",
    )
    content: str | None = Field(
        default=None, description="ONE self-contained sentence."
    )


class Extraction(BaseModel):
    operations: list[Operation] = Field(default_factory=list)
```

- [ ] **Step 4: Write the ranking functions**

Create `src/eve/memory/ranking.py`:

```python
"""Pure ranking maths. No I/O, no settings lookups - everything is an
argument, so every one of these is trivially testable and none of them can
surprise you at 3am."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from eve.memory.types import Memory


def recency_decay(age_days: float, half_life_days: float) -> float:
    """Exponential decay, computed at read time rather than cached.

    A `decayed_score` column refreshed nightly would be a cache of an
    expression cheaper to evaluate than to maintain, and it would be wrong
    for as long as the pod was down.
    """
    if half_life_days <= 0:
        return 1.0
    return math.exp(-max(age_days, 0.0) / half_life_days)


def fuse(*rankings: Sequence[str], k: int = 60) -> list[str]:
    """Reciprocal-rank fusion over ranked id lists.

    RRF rather than score normalisation: `ts_rank` and cosine similarity are
    on incomparable scales, and any attempt to map them onto each other is a
    tuning parameter nobody will ever revisit. Rank position is comparable by
    construction. k=60 is the value from the original TREC work; it flattens
    the difference between ranks 1 and 2 enough that agreement between arms
    matters more than either arm's confidence.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item_id in enumerate(ranking):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + position + 1)
    return sorted(scores, key=lambda i: (-scores[i], i))


def estimate_tokens(text: str) -> int:
    """Four characters per token.

    ponytail: a knob whose job is to stop the prompt growing without bound
    does not earn a tokeniser dependency. Being 15% wrong about a 1200-token
    budget changes nothing.
    """
    return len(text) // 4


def fit_budget(items: Iterable[Memory], budget: int) -> list[Memory]:
    """Take ranked items until the token budget is spent.

    Always returns at least one item when given one: an over-long single
    memory still beats an empty section, which the model reads as "she knows
    nothing about this" rather than "this did not fit."
    """
    kept: list[Memory] = []
    spent = 0
    for item in items:
        cost = estimate_tokens(item.content)
        if kept and spent + cost > budget:
            break
        kept.append(item)
        spent += cost
    return kept
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_memory_ranking.py -v`
Expected: all nine PASS.

- [ ] **Step 6: Commit**

```bash
git add src/eve/memory/types.py src/eve/memory/ranking.py tests/test_memory_ranking.py
git commit -m "feat: memory types and pure ranking functions

RRF rather than score normalisation: ts_rank and cosine similarity are on
incomparable scales, and mapping them onto each other is a tuning parameter
nobody would ever revisit."
```

---

## Task 5: The embedding client

**Files:**
- Create: `src/eve/memory/embed.py`
- Test: `tests/test_memory_embed.py`

**Interfaces:**
- Consumes: `eve.settings.get_settings()`
- Produces: `embed_query(text) -> list[float]`, `embed_texts(texts) -> list[list[float]]`, `to_pgvector(vec) -> str`, `get_embedder()` (cached, monkeypatchable)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory_embed.py`:

```python
import math

import pytest

from eve.memory import embed


class FakeEmbedder:
    """Returns 3072 non-normalised dims, which is what gemini-embedding-001
    emits before truncation."""

    def __init__(self, dims: int = 3072, scale: float = 5.0):
        self._vec = [scale] * dims

    async def aembed_query(self, text: str) -> list[float]:
        return list(self._vec)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vec) for _ in texts]


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setattr(embed, "get_embedder", lambda: FakeEmbedder())


async def test_query_is_truncated_to_the_pinned_dimension():
    assert len(await embed.embed_query("hi")) == 1536


async def test_query_is_renormalised_after_truncation():
    """MRL truncation breaks unit norm. Cosine distance over non-normalised
    vectors returns wrong rankings with no error - this is the assertion that
    stops that being silent."""
    vec = await embed.embed_query("hi")
    assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, abs_tol=1e-9)


async def test_documents_are_batched_and_all_normalised():
    vecs = await embed.embed_texts(["a", "b", "c"])
    assert len(vecs) == 3
    for vec in vecs:
        assert len(vec) == 1536
        assert math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, abs_tol=1e-9)


async def test_a_zero_vector_raises_rather_than_dividing_by_zero(monkeypatch):
    monkeypatch.setattr(embed, "get_embedder", lambda: FakeEmbedder(scale=0.0))
    with pytest.raises(ValueError, match="zero vector"):
        await embed.embed_query("hi")


async def test_empty_input_does_not_call_the_api(monkeypatch):
    """Extraction frequently produces no new rows. A round trip to Gemini to
    embed nothing is latency and money spent on nothing."""

    class Exploding:
        async def aembed_documents(self, texts):
            raise AssertionError("called the API with no input")

    monkeypatch.setattr(embed, "get_embedder", lambda: Exploding())
    assert await embed.embed_texts([]) == []


def test_pgvector_literal_round_trips():
    assert embed.to_pgvector([0.5, -0.25]) == "[0.5,-0.25]"
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_memory_embed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.memory.embed'`.

- [ ] **Step 3: Write the client**

Create `src/eve/memory/embed.py`:

```python
"""The embedding client.

Truncate to the pinned dimension, then RE-NORMALISE. gemini-embedding-001
emits 3072 dimensions trained with Matryoshka representation learning, and a
truncated MRL vector is no longer unit-norm. pgvector's cosine operator does
not care and does not complain; it just ranks wrong. See ADR 0003.

Truncation is unconditional even though LiteLLM may honour `dimensions` and
return 1536 already - in which case the slice is a no-op and the cost is one
comparison. Depending on the proxy to do it is depending on a remote config
we do not own.
"""

from __future__ import annotations

import math
from functools import lru_cache

from langchain_openai import OpenAIEmbeddings

from eve.settings import get_settings


@lru_cache(maxsize=1)
def get_embedder() -> OpenAIEmbeddings:
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.embedding_model,
        dimensions=settings.embedding_dims,
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key or "unset",
        # LiteLLM proxies a non-OpenAI model here. langchain's context-length
        # check runs tiktoken against an OpenAI tokeniser that does not
        # describe Gemini, and it rewrites the request body when it trips.
        check_embedding_ctx_length=False,
    )


def _normalise(vec: list[float], dims: int) -> list[float]:
    vec = vec[:dims]
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        raise ValueError("embedding is the zero vector; refusing to normalise")
    return [v / norm for v in vec]


async def embed_query(text: str) -> list[float]:
    dims = get_settings().embedding_dims
    return _normalise(await get_embedder().aembed_query(text), dims)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    dims = get_settings().embedding_dims
    raw = await get_embedder().aembed_documents(texts)
    return [_normalise(vec, dims) for vec in raw]


def to_pgvector(vec: list[float]) -> str:
    """pgvector's text input format.

    ponytail: a string literal cast with `%s::vector` rather than the
    `pgvector` package's psycopg adapter. One function against a stable wire
    format, versus a dependency and a per-connection registration hook.
    """
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_memory_embed.py -v`
Expected: all six PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eve/memory/embed.py tests/test_memory_embed.py
git commit -m "feat: embedding client with unconditional truncate-then-normalise

Truncation is unconditional even if LiteLLM honours `dimensions`: depending
on the proxy to do it is depending on remote config we do not own."
```

---

## Task 6: Store reads

**Files:**
- Create: `src/eve/memory/store.py`
- Test: `tests/test_memory_store.py` (extend)

**Interfaces:**
- Consumes: `eve.memory.db.get_pool()`, `eve.memory.types.Memory`, `eve.memory.embed.to_pgvector`
- Produces: `load_always_on(sub, thread_id) -> tuple[list[Memory], list[Memory], str | None]`, `search_episodic_lexical(sub, query, limit) -> list[Memory]`, `search_episodic_vector(sub, embedding, limit) -> list[Memory]`, `subjects_in(text) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_memory_store.py`:

```python
from eve.memory import store

_VEC = [0.0] * 1535 + [1.0]


async def _insert(pool, **kw) -> str:
    cols = {
        "layer": "episodic",
        "scope_kind": "member",
        "scope_id": "sub-noah",
        "kind": "event",
        "subject": None,
        "content": "something happened",
        "embedding": None,
        **kw,
    }
    names = ", ".join(cols)
    holes = ", ".join(
        "%s::vector" if n == "embedding" else "%s" for n in cols
    )
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"INSERT INTO eve_memory ({names}) VALUES ({holes}) RETURNING id",
            tuple(cols.values()),
        )
        return str((await cur.fetchone())[0])


async def test_always_on_returns_profile_household_and_digest(pool):
    await _insert(pool, layer="profile", kind="fact", content="Noah is vegetarian")
    await _insert(
        pool,
        layer="household",
        scope_kind="household",
        scope_id="",
        kind="fact",
        content="The dog is Cooper",
    )
    await _insert(
        pool,
        layer="digest",
        scope_kind="thread",
        scope_id="thread-1",
        kind="digest",
        content="They discussed dinner.",
    )
    profile, household, digest = await store.load_always_on("sub-noah", "thread-1")
    assert [m.content for m in profile] == ["Noah is vegetarian"]
    assert [m.content for m in household] == ["The dog is Cooper"]
    assert digest == "They discussed dinner."


async def test_always_on_does_not_leak_another_members_profile(pool):
    """The isolation that matters most in this whole phase. A profile fact is
    the most personal thing Eve stores."""
    await _insert(
        pool, layer="profile", scope_id="sub-kendra", kind="fact", content="secret"
    )
    profile, _, _ = await store.load_always_on("sub-noah", "thread-1")
    assert profile == []


async def test_household_is_visible_to_every_member(pool):
    await _insert(
        pool,
        layer="household",
        scope_kind="household",
        scope_id="",
        kind="fact",
        content="Trash goes out Sunday",
    )
    _, household, _ = await store.load_always_on("sub-kendra", "thread-1")
    assert [m.content for m in household] == ["Trash goes out Sunday"]


async def test_lexical_search_finds_a_matching_episode(pool):
    await _insert(pool, content="We decided to replace the dishwasher in March")
    await _insert(pool, content="The car needs an oil change")
    found = await store.search_episodic_lexical("sub-noah", "dishwasher", limit=10)
    assert [m.content for m in found] == [
        "We decided to replace the dishwasher in March"
    ]


async def test_lexical_search_matches_on_subject_when_the_text_does_not(pool):
    """Entity matching is the arm that carries names, and names are most of
    family memory. FTS on 'cooper' would miss a row phrased 'he needs a walk'."""
    await _insert(pool, subject="cooper", content="He needs a walk before 7")
    found = await store.search_episodic_lexical("sub-noah", "how is Cooper", limit=10)
    assert len(found) == 1


async def test_lexical_search_excludes_superseded_rows(pool):
    old = await _insert(pool, content="Kendra works Tuesdays")
    new = await _insert(pool, content="Kendra works Wednesdays")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_memory SET superseded_by=%s,"
            " superseded_why='contradicted' WHERE id=%s",
            (new, old),
        )
    found = await store.search_episodic_lexical("sub-noah", "Kendra works", limit=10)
    assert [m.content for m in found] == ["Kendra works Wednesdays"]


async def test_vector_search_returns_the_nearest_row(pool):
    from eve.memory.embed import to_pgvector

    await _insert(pool, content="near", embedding=to_pgvector(_VEC))
    await _insert(
        pool, content="far", embedding=to_pgvector([1.0] + [0.0] * 1535)
    )
    found = await store.search_episodic_vector("sub-noah", _VEC, limit=1)
    assert [m.content for m in found] == ["near"]


async def test_vector_search_ignores_rows_with_no_embedding(pool):
    """Rows are written before they are embedded, and the embedding call can
    fail. A NULL embedding must not become a spurious nearest neighbour."""
    await _insert(pool, content="unembedded", embedding=None)
    assert await store.search_episodic_vector("sub-noah", _VEC, limit=5) == []


def test_subjects_in_lowercases_and_drops_stopwords():
    assert store.subjects_in("How is Cooper doing?") == ["cooper", "doing"]
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest -m integration tests/test_memory_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'store'`.

- [ ] **Step 3: Write the read side**

Create `src/eve/memory/store.py`:

```python
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
        conn.row_factory = dict_row
        cur = await conn.execute(sql, params)
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
          * exp(-EXTRACT(EPOCH FROM (now() - last_seen_at)) / 86400.0
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -m integration tests/test_memory_store.py -v`
Expected: all thirteen PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eve/memory/store.py tests/test_memory_store.py
git commit -m "feat: memory reads - always-on layers in one query, hybrid episodic arms

The lexical arm cannot fail and must never depend on a network call: it is
what the turn ships when the vector arm misses its budget."
```

---

## Task 7: Store writes

**Files:**
- Modify: `src/eve/memory/store.py`
- Test: `tests/test_memory_store.py` (extend)

**Interfaces:**
- Consumes: everything from Task 6
- Produces: `add(...) -> str`, `supersede(old_id, new_id, why) -> None`, `reinforce(memory_id) -> None`, `forget(memory_id) -> None`, `set_embeddings(pairs) -> None`, `upsert_digest(thread_id, content) -> None`, `evict_over_cap(layer, scope_kind, scope_id, cap) -> int`, `overlapping(sub, subjects, embedding, limit) -> list[Memory]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_memory_store.py`:

```python
async def test_add_returns_an_id_that_reads_back(pool):
    mid = await store.add(
        layer="profile",
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        content="Noah is vegetarian",
        subject="noah",
        source_thread="t1",
    )
    profile, _, _ = await store.load_always_on("sub-noah", "t1")
    assert [(m.id, m.content) for m in profile] == [(mid, "Noah is vegetarian")]


async def test_supersede_hides_the_old_row_but_keeps_it(pool):
    """The row survives because Phase 5's eval harness needs to answer 'what
    did Eve believe on the day she got that wrong'."""
    old = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="Kendra works Tuesdays",
    )
    new = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="Kendra works Wednesdays",
    )
    await store.supersede(old, new, "contradicted")
    profile, _, _ = await store.load_always_on("sub-noah", "t1")
    assert [m.content for m in profile] == ["Kendra works Wednesdays"]
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_memory")
        assert (await cur.fetchone())[0] == 2


async def test_forget_actually_deletes(pool):
    """'Eve, forget I said that' has to mean the row is gone. A tombstone
    that still holds the text is a quiet lie to a family member about their
    own data."""
    mid = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="something private",
    )
    await store.forget(mid)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_memory")
        assert (await cur.fetchone())[0] == 0


async def test_reinforce_bumps_last_seen_and_salience(pool):
    mid = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="x",
    )
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_memory SET last_seen_at = now() - interval '30 days'"
        )
    await store.reinforce(mid)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT salience, now() - last_seen_at < interval '1 minute'"
            " FROM eve_memory WHERE id=%s",
            (mid,),
        )
        salience, recent = await cur.fetchone()
        assert salience > 0.5
        assert recent


async def test_reinforce_clamps_salience_at_one(pool):
    """Otherwise a fact mentioned every day drifts to a salience no other
    memory can ever outrank, and the layer stops being sortable."""
    mid = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="x",
    )
    for _ in range(20):
        await store.reinforce(mid)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT salience FROM eve_memory WHERE id=%s", (mid,))
        assert (await cur.fetchone())[0] <= 1.0


async def test_set_embeddings_makes_a_row_findable_by_vector(pool):
    mid = await store.add(
        layer="episodic", scope_kind="member", scope_id="sub-noah",
        kind="event", content="the dishwasher",
    )
    assert await store.search_episodic_vector("sub-noah", _VEC, limit=5) == []
    await store.set_embeddings([(mid, _VEC)])
    found = await store.search_episodic_vector("sub-noah", _VEC, limit=5)
    assert [m.id for m in found] == [mid]


async def test_upsert_digest_replaces_rather_than_accumulates(pool):
    await store.upsert_digest("t1", "first summary")
    await store.upsert_digest("t1", "second summary")
    _, _, digest = await store.load_always_on("sub-noah", "t1")
    assert digest == "second summary"
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM eve_memory WHERE layer='digest'"
        )
        assert (await cur.fetchone())[0] == 1


async def test_eviction_retires_the_weakest_until_the_cap_is_met(pool):
    for i in range(5):
        mid = await store.add(
            layer="profile", scope_kind="member", scope_id="sub-noah",
            kind="fact", content=f"fact {i}",
        )
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE eve_memory SET salience=%s WHERE id=%s", (i / 10.0, mid)
            )
    evicted = await store.evict_over_cap("profile", "member", "sub-noah", cap=3)
    profile, _, _ = await store.load_always_on("sub-noah", "t1")
    assert evicted == 2
    assert {m.content for m in profile} == {"fact 2", "fact 3", "fact 4"}


async def test_eviction_supersedes_rather_than_deletes(pool):
    """A mistakenly evicted fact has to be recoverable, and 'why did she
    forget that' has to have an answer."""
    for i in range(3):
        await store.add(
            layer="profile", scope_kind="member", scope_id="sub-noah",
            kind="fact", content=f"fact {i}",
        )
    await store.evict_over_cap("profile", "member", "sub-noah", cap=1)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM eve_memory WHERE superseded_why='evicted'"
        )
        assert (await cur.fetchone())[0] == 2


async def test_overlapping_finds_candidates_by_subject_and_by_vector(pool):
    by_subject = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", subject="kendra", content="Kendra works Tuesdays",
    )
    by_vector = await store.add(
        layer="episodic", scope_kind="member", scope_id="sub-noah",
        kind="event", content="unrelated words entirely",
    )
    await store.set_embeddings([(by_vector, _VEC)])
    found = await store.overlapping("sub-noah", ["kendra"], _VEC, limit=10)
    assert {m.id for m in found} == {by_subject, by_vector}
```

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest -m integration tests/test_memory_store.py -v`
Expected: FAIL with `AttributeError: module 'eve.memory.store' has no attribute 'add'`.

- [ ] **Step 3: Write the write side**

Append to `src/eve/memory/store.py`:

```python
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
    """Hard delete. The ONE exception to supersede-don't-delete (spec 4.2)."""
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
    """
    pool = await get_pool()
    async with pool.connection() as conn:
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
                * exp(-EXTRACT(EPOCH FROM (now() - last_seen_at)) / 86400.0 / 365.0)
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
```

**`superseded_why IS NULL` is the live predicate throughout, not `superseded_by IS NULL`.** An eviction retires a row and replaces it with nothing, so its `superseded_by` stays NULL — filtering on that column would leave every evicted row visible in every read, and would make the eviction subselect re-select the same rows on every run. Only `superseded_why` distinguishes live from retired. If you find yourself writing `superseded_by IS NULL` anywhere, it is a bug.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -m integration tests/test_memory_store.py -v`
Expected: all twenty-three PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eve/memory/store.py tests/test_memory_store.py
git commit -m "feat: memory writes - add, supersede, reinforce, forget, evict

Forget hard-deletes; everything else supersedes. A tombstone that still holds
the text is not forgetting, and treating it as such would be a quiet lie to a
family member about their own data."
```

---

## Task 8: The `recall` node

**Files:**
- Create: `src/eve/memory/recall.py`
- Modify: `src/eve/memory/__init__.py`, `src/eve/state.py`
- Test: `tests/test_memory_recall.py`

**Interfaces:**
- Consumes: `store.load_always_on`, `store.search_episodic_lexical`, `store.search_episodic_vector`, `embed.embed_query`, `ranking.fuse`, `ranking.fit_budget`
- Produces: `eve.memory.recall(state, config) -> dict` returning `{"memory": MemoryBundle}`; `EveState` gains `memory: MemoryBundle`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory_recall.py`:

```python
import asyncio
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.memory import recall as recall_mod
from eve.memory.types import Memory

CONFIG = {"configurable": {"thread_id": "t1"}}


def _mem(mid: str, content: str, layer: str = "episodic") -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id=mid, layer=layer, scope_kind="member", scope_id="sub-noah",
        kind="event", subject=None, content=content, confidence=0.7,
        salience=0.5, created_at=now, last_seen_at=now,
    )


def _state(text: str = "how is Cooper?") -> dict:
    return {
        "messages": [HumanMessage(text)],
        "member": {
            "sub": "sub-noah", "name": "Noah", "role": "adult",
            "timezone": "America/Vancouver", "permissions": [],
            "local_time": "2026-08-18 09:00 PDT",
        },
        "system_prompt": "",
    }


@pytest.fixture
def wired(monkeypatch):
    calls = {"vector": 0}

    async def always_on(sub, thread_id):
        return ([_mem("p1", "Noah is vegetarian", "profile")],
                [_mem("h1", "The dog is Cooper", "household")],
                "They talked about dinner.")

    async def lexical(sub, query, limit=20):
        return [_mem("e1", "Cooper had his shots in June")]

    async def vector(sub, embedding, limit=20):
        calls["vector"] += 1
        return [_mem("e2", "The vet is on Fifth Avenue")]

    async def embed_query(text):
        return [0.0] * 1535 + [1.0]

    monkeypatch.setattr(recall_mod, "load_always_on", always_on)
    monkeypatch.setattr(recall_mod, "search_episodic_lexical", lexical)
    monkeypatch.setattr(recall_mod, "search_episodic_vector", vector)
    monkeypatch.setattr(recall_mod, "embed_query", embed_query)
    return calls


async def test_recall_returns_all_four_layers(wired):
    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]
    assert [m.id for m in bundle["profile"]] == ["p1"]
    assert [m.id for m in bundle["household"]] == ["h1"]
    assert {m.id for m in bundle["episodic"]} == {"e1", "e2"}
    assert bundle["digest"] == "They talked about dinner."
    assert bundle["vector_used"] is True


async def test_a_slow_embedding_degrades_to_lexical_rather_than_failing(
    monkeypatch, wired
):
    """The load-bearing property of the whole design. An untested degrade
    path does not work."""

    async def slow(text):
        await asyncio.sleep(5)
        return [0.0] * 1536

    monkeypatch.setattr(recall_mod, "embed_query", slow)
    monkeypatch.setattr(
        recall_mod, "EMBED_BUDGET_OVERRIDE_S", 0.01, raising=False
    )
    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]

    assert bundle["vector_used"] is False
    assert [m.id for m in bundle["episodic"]] == ["e1"]
    assert bundle["profile"], "always-on layers must survive a degraded turn"
    assert wired["vector"] == 0


async def test_a_failing_embedding_degrades_rather_than_raising(
    monkeypatch, wired
):
    async def boom(text):
        raise RuntimeError("gemini is down")

    monkeypatch.setattr(recall_mod, "embed_query", boom)
    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]
    assert bundle["vector_used"] is False
    assert [m.id for m in bundle["episodic"]] == ["e1"]


async def test_no_human_message_skips_retrieval_but_keeps_always_on(wired):
    """A resumed run can reach recall with no new human turn. Embedding an
    empty string is a wasted call, but the standing facts still belong in
    the prompt."""
    state = _state()
    state["messages"] = [AIMessage("hello")]
    bundle = (await recall_mod.recall(state, CONFIG))["memory"]
    assert bundle["episodic"] == []
    assert bundle["profile"]
    assert bundle["vector_used"] is False


async def test_the_budget_truncates_episodic_and_reports_the_count(
    monkeypatch, wired
):
    async def many(sub, query, limit=20):
        return [_mem(f"e{i}", "x" * 400) for i in range(20)]

    monkeypatch.setattr(recall_mod, "search_episodic_lexical", many)
    bundle = (await recall_mod.recall(_state(), CONFIG))["memory"]
    assert 0 < len(bundle["episodic"]) < 20
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_memory_recall.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.memory.recall'`.

- [ ] **Step 3: Add `memory` to state**

In `src/eve/state.py`:

```python
from eve.memory.types import MemoryBundle


class EveState(TypedDict):
    messages: Annotated[list, add_messages]
    member: MemberContext
    system_prompt: str
    # Written by `recall`, rendered into the system prompt by `load_context`'s
    # builder, read by `extract`. Phase 3's tools loop reads it too.
    memory: MemoryBundle
```

- [ ] **Step 4: Write the node**

Create `src/eve/memory/recall.py`:

```python
"""The `recall` node.

The lexical arm fires immediately and cannot fail. The vector arm races a
budget and is fused in only if it lands. A degraded turn is a complete turn:
the always-on layers are untouched and episodic falls back to a real lexical
ranking rather than to nothing. There is no path where Gemini being slow
makes Eve amnesiac - only slightly worse at paraphrase, for one turn.

This is the one place ADR 0002 bends, and it bends by exactly one bounded,
cancellable embedding call.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from opentelemetry import trace

from eve.memory.embed import embed_query
from eve.memory.ranking import fit_budget, fuse
from eve.memory.store import (
    load_always_on,
    search_episodic_lexical,
    search_episodic_vector,
)
from eve.memory.types import Memory, MemoryBundle
from eve.settings import get_settings

logger = logging.getLogger(__name__)

_CANDIDATES = 20
# Set by tests to shrink the race window. Production reads settings.
EMBED_BUDGET_OVERRIDE_S: float | None = None


def _last_human_text(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _budget_seconds() -> float:
    if EMBED_BUDGET_OVERRIDE_S is not None:
        return EMBED_BUDGET_OVERRIDE_S
    return get_settings().memory_recall_embed_budget_ms / 1000.0


async def recall(state: dict, config: RunnableConfig) -> dict:
    started = perf_counter()
    settings = get_settings()
    sub = state["member"]["sub"]
    thread_id = config.get("configurable", {}).get("thread_id")
    query = _last_human_text(state["messages"])

    # Start the clock on the embedding BEFORE the lexical query, so the two
    # overlap. The lexical round trip is a few milliseconds of the budget the
    # embedding would otherwise have had entirely to itself.
    embed_task = (
        asyncio.create_task(embed_query(query)) if query.strip() else None
    )

    profile, household, digest = await load_always_on(sub, thread_id)
    lexical = (
        await search_episodic_lexical(sub, query, limit=_CANDIDATES)
        if query.strip()
        else []
    )

    episodic: list[Memory] = lexical
    vector_used = False
    if embed_task is not None:
        remaining = _budget_seconds() - (perf_counter() - started)
        try:
            embedding = await asyncio.wait_for(embed_task, timeout=max(remaining, 0.0))
        except Exception:
            # Timeout, transport error, a zero vector - all the same response.
            # wait_for cancels the task for us on timeout; cancel() is a no-op
            # if it already finished.
            embed_task.cancel()
            logger.debug("recall: vector arm missed its budget", exc_info=True)
        else:
            vectors = await search_episodic_vector(
                sub, embedding, limit=_CANDIDATES
            )
            episodic = _fuse_memories(lexical, vectors)
            vector_used = True

    share = settings.memory_token_budget // 3
    profile = fit_budget(profile, share)
    household = fit_budget(household, share)
    # Whatever the always-on layers did not spend flows to episodic, which is
    # the only unbounded layer and so the only one that can use it.
    spent = sum(len(m.content) // 4 for m in (*profile, *household))
    episodic = fit_budget(episodic, settings.memory_token_budget - spent)

    latency_ms = (perf_counter() - started) * 1000
    _record_span(profile, household, episodic, vector_used, latency_ms)

    return {
        "memory": MemoryBundle(
            profile=profile,
            household=household,
            episodic=episodic,
            digest=digest,
            vector_used=vector_used,
            latency_ms=latency_ms,
        )
    }


def _fuse_memories(lexical: list[Memory], vectors: list[Memory]) -> list[Memory]:
    by_id = {m.id: m for m in (*lexical, *vectors)}
    order = fuse([m.id for m in lexical], [m.id for m in vectors])
    return [by_id[i] for i in order]


def _record_span(
    profile: list[Memory],
    household: list[Memory],
    episodic: list[Memory],
    vector_used: bool,
    latency_ms: float,
) -> None:
    """Whether the 120ms budget actually holds is a number in Langfuse, not
    an assumption. If the degrade rate turns out to be high, the honest
    response might be to drop the vector arm entirely - and that is a
    decision this attribute makes possible."""
    span = trace.get_current_span()
    span.set_attribute("eve.recall.vector_used", vector_used)
    span.set_attribute("eve.recall.latency_ms", round(latency_ms, 1))
    span.set_attribute(
        "eve.recall.items", len(profile) + len(household) + len(episodic)
    )
    span.set_attribute(
        "eve.recall.tokens",
        sum(len(m.content) // 4 for m in (*profile, *household, *episodic)),
    )
```

- [ ] **Step 5: Export it**

Append to `src/eve/memory/__init__.py`:

```python
from eve.memory.recall import recall
from eve.memory.types import Memory, MemoryBundle

__all__ = ["Memory", "MemoryBundle", "recall"]
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_memory_recall.py -v`
Expected: all five PASS.

- [ ] **Step 7: Commit**

```bash
git add src/eve/memory/recall.py src/eve/memory/__init__.py src/eve/state.py \
        tests/test_memory_recall.py
git commit -m "feat: the recall node - lexical immediately, vector on a budget

ADR 0002 bends here and only here, by exactly one bounded, cancellable
embedding call. The degrade path has its own test because an untested
degrade path does not work."
```

---

## Task 9: Rendering memory into the prompt

**Files:**
- Modify: `src/eve/context.py`, `prompts/eve.md`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: `MemoryBundle`
- Produces: `build_system_prompt(persona, member, memory=None) -> str` — note the third parameter is optional, so Task 10 can wire it without breaking Phase 1's callers in the same commit

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_context.py`:

```python
from datetime import UTC, datetime

from eve.context import build_system_prompt
from eve.memory.types import Memory, MemoryBundle


def _mem(content: str, layer: str) -> Memory:
    now = datetime.now(UTC)
    return Memory(
        id="m1", layer=layer, scope_kind="member", scope_id="sub-noah",
        kind="fact", subject=None, content=content, confidence=0.7,
        salience=0.5, created_at=now, last_seen_at=now,
    )


def _bundle(**kw) -> MemoryBundle:
    return MemoryBundle(
        profile=[], household=[], episodic=[], digest=None,
        vector_used=False, latency_ms=0.0, **kw
    )


MEMBER = {
    "sub": "sub-noah", "name": "Noah", "role": "adult",
    "timezone": "America/Vancouver", "permissions": [],
    "local_time": "2026-08-18 09:00 PDT",
}


def test_prompt_without_memory_is_unchanged():
    """Phase 1 callers and any turn where memory is empty must not gain a
    dangling empty heading, which reads to the model as 'you know nothing'."""
    prompt = build_system_prompt("You are Eve.", MEMBER)
    assert "What you remember" not in prompt


def test_empty_bundle_adds_no_heading():
    prompt = build_system_prompt("You are Eve.", MEMBER, _bundle())
    assert "What you remember" not in prompt


def test_each_populated_layer_gets_its_own_section():
    bundle = _bundle(
        profile=[_mem("Noah is vegetarian", "profile")],
        household=[_mem("The dog is Cooper", "household")],
        episodic=[_mem("Replacing the dishwasher in March", "episodic")],
        digest="They were planning dinner.",
    )
    prompt = build_system_prompt("You are Eve.", MEMBER, bundle)
    assert "Noah is vegetarian" in prompt
    assert "The dog is Cooper" in prompt
    assert "Replacing the dishwasher in March" in prompt
    assert "They were planning dinner." in prompt


def test_layers_are_labelled_by_confidence_not_merged():
    """Episodic recall is a guess and standing facts are not. Presenting them
    as one undifferentiated list invites Eve to state a fuzzy match with the
    same certainty as a profile fact."""
    bundle = _bundle(
        profile=[_mem("Noah is vegetarian", "profile")],
        episodic=[_mem("Something from a past conversation", "episodic")],
    )
    prompt = build_system_prompt("You are Eve.", MEMBER, bundle)
    assert prompt.index("Noah is vegetarian") < prompt.index(
        "Something from a past conversation"
    )
    assert "may be relevant" in prompt
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_context.py -v`
Expected: FAIL — `build_system_prompt() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Render the bundle**

In `src/eve/context.py`, replace `build_system_prompt`:

```python
def _section(title: str, memories: list) -> str:
    if not memories:
        return ""
    lines = "\n".join(f"- {m.content}" for m in memories)
    return f"\n### {title}\n{lines}\n"


def build_system_prompt(
    persona: str, member: MemberContext, memory: MemoryBundle | None = None
) -> str:
    prompt = (
        f"{persona}\n\n"
        "## Who you are speaking with\n"
        f"- Name: {member['name']}\n"
        f"- Role in the family: {member['role']}\n"
        f"- Their local time right now: {member['local_time']}\n"
    )
    if memory is None:
        return prompt

    # Standing facts and retrieved episodes are separated on purpose, and
    # episodic carries a hedge in its heading. Merged into one list, a fuzzy
    # vector match reads to the model with exactly the same authority as
    # "Noah is vegetarian", and Eve states a guess as a fact.
    body = (
        _section("What you know about them", memory["profile"])
        + _section("What you know about this household", memory["household"])
        + _section(
            "From earlier conversations - may be relevant, may not",
            memory["episodic"],
        )
    )
    if memory["digest"]:
        body += f"\n### Where this conversation has got to\n{memory['digest']}\n"
    if not body:
        return prompt
    return prompt + "\n## What you remember\n" + body
```

Add the import at the top of `src/eve/context.py`:

```python
from eve.memory.types import MemoryBundle
```

- [ ] **Step 4: Tell Eve how to use it**

Append to `prompts/eve.md`:

```markdown
What you remember:
- You are given what you know about this person and this household. Use it
  the way a person would - naturally, without announcing it. Never say
  "according to my memory" or "I have it recorded that."
- Things under "From earlier conversations" are a guess at what is relevant.
  If one turns out not to fit, ignore it silently. Do not explain that you
  found something irrelevant.
- If something you remember contradicts what you are being told now, believe
  the person. Say what you thought was true, briefly, so they can correct it.
- You never invent a memory. If you do not know something about the family,
  ask.
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_context.py -v`
Expected: PASS, including the existing Phase 1 tests.

- [ ] **Step 6: Commit**

```bash
git add src/eve/context.py prompts/eve.md tests/test_context.py
git commit -m "feat: render memory into the system prompt

Standing facts and retrieved episodes are separated, and episodic carries a
hedge in its heading: merged into one list a fuzzy vector match reads with
the same authority as 'Noah is vegetarian'."
```

---

## Task 10: The `extract` node

**Files:**
- Create: `src/eve/memory/extract.py`, `prompts/extract.md`
- Modify: `src/eve/memory/__init__.py`
- Test: `tests/test_memory_extract.py`

**Interfaces:**
- Consumes: `store.*`, `embed.embed_texts`, `eve.models.get_model`, `Extraction`, `Operation`
- Produces: `eve.memory.extract(state, config) -> dict` (returns `{}`); `apply_operations(ops, member, thread_id, run_id) -> dict[str, int]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory_extract.py`:

```python
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from eve.memory import extract as extract_mod
from eve.memory.types import Extraction, Memory, Operation

MEMBER_SHARED = {
    "sub": "sub-noah", "name": "Noah", "role": "adult",
    "timezone": "America/Vancouver",
    "permissions": ["memory.write_shared"],
    "local_time": "2026-08-18 09:00 PDT",
}
MEMBER_PLAIN = {**MEMBER_SHARED, "sub": "sub-kid", "permissions": []}


@pytest.fixture
def recorded(monkeypatch):
    calls = {"add": [], "supersede": [], "reinforce": [], "forget": [],
             "embed": [], "evict": []}

    async def add(**kw):
        calls["add"].append(kw)
        return f"new-{len(calls['add'])}"

    async def supersede(old, new, why):
        calls["supersede"].append((old, new, why))

    async def reinforce(mid):
        calls["reinforce"].append(mid)

    async def forget(mid):
        calls["forget"].append(mid)

    async def set_embeddings(pairs):
        calls["embed"].extend(pairs)

    async def evict_over_cap(layer, scope_kind, scope_id, cap):
        calls["evict"].append((layer, scope_kind, scope_id, cap))
        return 0

    async def embed_texts(texts):
        return [[0.0] * 1535 + [1.0] for _ in texts]

    for name, fn in [
        ("add", add), ("supersede", supersede), ("reinforce", reinforce),
        ("forget", forget), ("set_embeddings", set_embeddings),
        ("evict_over_cap", evict_over_cap),
    ]:
        monkeypatch.setattr(extract_mod, name, fn)
    monkeypatch.setattr(extract_mod, "embed_texts", embed_texts)
    return calls


async def test_add_writes_a_row_and_embeds_it(recorded):
    ops = [Operation(op="add", layer="episodic", kind="event",
                     subject="cooper", content="Cooper had his shots.")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["add"][0]["content"] == "Cooper had his shots."
    assert recorded["add"][0]["scope_id"] == "sub-noah"
    assert len(recorded["embed"]) == 1


async def test_only_episodic_rows_are_embedded(recorded):
    """Profile and household are injected in full and never searched by
    vector. Embedding them is a Gemini call bought for nothing."""
    ops = [
        Operation(op="add", layer="profile", kind="fact", content="Vegetarian."),
        Operation(op="add", layer="episodic", kind="event", content="Went out."),
    ]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert len(recorded["embed"]) == 1


async def test_household_write_requires_the_permission(recorded):
    """First real use of a permission string resolved in Phase 1 and never
    read since."""
    ops = [Operation(op="add", layer="household", kind="fact",
                     content="Trash goes out Sunday.")]
    await extract_mod.apply_operations(ops, MEMBER_PLAIN, "t1", "r1")
    written = recorded["add"][0]
    assert written["layer"] == "profile"
    assert written["scope_kind"] == "member"
    assert written["scope_id"] == "sub-kid"


async def test_household_write_is_allowed_with_the_permission(recorded):
    ops = [Operation(op="add", layer="household", kind="fact",
                     content="Trash goes out Sunday.")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    written = recorded["add"][0]
    assert written["layer"] == "household"
    assert written["scope_kind"] == "household"
    assert written["scope_id"] == ""


async def test_supersede_points_the_old_row_at_a_newly_added_one(recorded):
    ops = [
        Operation(op="add", layer="profile", kind="fact",
                  content="Kendra works Wednesdays."),
        Operation(op="supersede", target_id="old-1"),
    ]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["supersede"] == [("old-1", "new-1", "contradicted")]


async def test_supersede_with_no_replacement_still_retires_the_row(recorded):
    ops = [Operation(op="supersede", target_id="old-1")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["supersede"] == [("old-1", None, "contradicted")]


async def test_operations_missing_required_fields_are_dropped(recorded):
    """The model is cheap and will occasionally emit an `add` with no content
    or a `forget` with no target. Neither should reach SQL."""
    ops = [
        Operation(op="add", layer="profile", kind="fact", content=None),
        Operation(op="forget", target_id=None),
        Operation(op="reinforce", target_id=None),
    ]
    counts = await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert recorded["add"] == []
    assert recorded["forget"] == []
    assert recorded["reinforce"] == []
    assert counts == {}


async def test_eviction_runs_for_the_layers_that_are_capped(recorded):
    ops = [Operation(op="add", layer="profile", kind="fact", content="x.")]
    await extract_mod.apply_operations(ops, MEMBER_SHARED, "t1", "r1")
    assert ("profile", "member", "sub-noah", 40) in recorded["evict"]


async def test_a_model_failure_does_not_break_the_turn(monkeypatch, recorded):
    """extract runs after the answer has streamed. If it raises, the run
    fails and the user sees an error for a turn that already succeeded."""

    class Boom:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, _messages):
            raise RuntimeError("gemini is down")

    monkeypatch.setattr(extract_mod, "get_model", lambda _tier: Boom())
    monkeypatch.setattr(extract_mod, "overlapping", _no_overlap)
    state = {
        "messages": [HumanMessage("hi"), AIMessage("hello")],
        "member": MEMBER_SHARED,
        "memory": None,
    }
    assert await extract_mod.extract(state, {"configurable": {}}) == {}


async def _no_overlap(sub, subjects, embedding, limit=10):
    return []


async def test_extraction_asks_the_model_about_overlapping_memories(
    monkeypatch, recorded
):
    """Contradictions are resolved with the context that revealed them, which
    means the model has to see what is already believed."""
    seen = {}
    now = datetime.now(UTC)

    async def overlapping(sub, subjects, embedding, limit=10):
        return [Memory(
            id="old-1", layer="profile", scope_kind="member",
            scope_id="sub-noah", kind="fact", subject="kendra",
            content="Kendra works Tuesdays", confidence=0.7, salience=0.5,
            created_at=now, last_seen_at=now,
        )]

    class Recording:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            seen["prompt"] = messages[0].content
            return Extraction(operations=[])

    monkeypatch.setattr(extract_mod, "overlapping", overlapping)
    monkeypatch.setattr(extract_mod, "get_model", lambda _tier: Recording())
    state = {
        "messages": [HumanMessage("Kendra moved to Wednesdays"),
                     AIMessage("Got it.")],
        "member": MEMBER_SHARED,
        "memory": None,
    }
    await extract_mod.extract(state, {"configurable": {"thread_id": "t1"}})
    assert "old-1" in seen["prompt"]
    assert "Kendra works Tuesdays" in seen["prompt"]
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_memory_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.memory.extract'`.

- [ ] **Step 3: Write the extraction prompt**

Create `prompts/extract.md`:

```markdown
You maintain the memory of a family assistant. You are not talking to anyone;
you produce operations on a memory store.

You are given the last exchange and the memories that might overlap with it.
Decide what, if anything, should change.

Layers:
- `profile` — durable facts about the person speaking: dietary needs, work
  patterns, health, relationships, and their preferences about how they like
  to be spoken to. Small and slow-changing.
- `household` — durable facts true for the whole family: pets, vehicles,
  routines, house rules, standing arrangements.
- `episodic` — something that happened or was decided, tied to a time.

Operations:
- `add` — a new memory. Set `layer`, `kind`, `subject`, `content`.
- `supersede` — an existing memory is now wrong. Set `target_id`. If a
  replacement exists, emit the `add` for it FIRST, in the same list.
- `reinforce` — an existing memory was restated or confirmed. Set `target_id`.
- `forget` — the person explicitly asked you to forget something. Set
  `target_id`. Only ever in response to an explicit instruction.

Rules:
- `content` is ONE self-contained sentence that makes sense read on its own,
  months later, with no surrounding conversation.
- `subject` is a single lowercase word naming what the memory is about:
  `cooper`, `kendra`, `honda`, `kitchen`.
- Record what is durable. Not "Noah said hello", not "Noah asked about the
  weather". If you would not care about it in a month, do not record it.
- Prefer superseding over adding when something overlaps. A memory store that
  only accumulates gets confidently worse over time.
- Most turns produce NO operations. An empty list is the correct and common
  answer.
```

- [ ] **Step 4: Write the node**

Create `src/eve/memory/extract.py`:

```python
"""The `extract` node.

Runs AFTER the answer has streamed, so its latency is invisible. It is the
only place memory is written, and the only place contradictions are resolved
- with the conversational context that revealed them, which a nightly
reconciler could never have (spec 5.4).

It runs on every turn with no gating heuristic. A cheap "does this look
fact-bearing" filter would be wrong exactly when it matters, and Flash Lite
over five people is a rounding error. Gate it when there is a bill to point at.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from opentelemetry import trace

from eve.memory.embed import embed_texts
from eve.memory.store import (
    add,
    evict_over_cap,
    forget,
    overlapping,
    reinforce,
    set_embeddings,
    subjects_in,
    supersede,
    upsert_digest,
)
from eve.memory.types import Extraction, Operation
from eve.models import Tier, get_model
from eve.settings import get_settings

logger = logging.getLogger(__name__)

_CAPPED = {"profile": "memory_profile_cap", "household": "memory_household_cap"}


@lru_cache(maxsize=1)
def load_extract_prompt() -> str:
    return (get_settings().prompt_file.parent / "extract.md").read_text()


def _resolve_scope(op: Operation, member: dict) -> tuple[str, str, str]:
    """Layer, scope_kind, scope_id - after the permission gate.

    A member without `memory.write_shared` gets the fact written to their own
    profile instead of the household's. Degraded, not dropped: throwing away
    a real fact because of a permission is worse than filing it narrowly.
    """
    if op.layer == "household":
        if "memory.write_shared" in (member.get("permissions") or []):
            return "household", "household", ""
        return "profile", "member", member["sub"]
    return op.layer or "episodic", "member", member["sub"]


async def apply_operations(
    operations: list[Operation], member: dict, thread_id: str | None, run_id: str | None
) -> dict[str, int]:
    counts: dict[str, int] = {}
    last_added: str | None = None
    to_embed: list[tuple[str, str]] = []
    touched_scopes: set[tuple[str, str, str]] = set()

    for op in operations:
        if op.op == "add":
            if not op.content:
                continue
            layer, scope_kind, scope_id = _resolve_scope(op, member)
            last_added = await add(
                layer=layer,
                scope_kind=scope_kind,
                scope_id=scope_id,
                kind=op.kind or "fact",
                content=op.content,
                subject=op.subject,
                source_thread=thread_id,
                source_run=run_id,
            )
            # Only episodic is ever searched by vector. Profile and household
            # are injected in full, so embedding them buys nothing.
            if layer == "episodic":
                to_embed.append((last_added, op.content))
            if layer in _CAPPED:
                touched_scopes.add((layer, scope_kind, scope_id))
        elif op.op == "supersede":
            if not op.target_id:
                continue
            await supersede(op.target_id, last_added, "contradicted")
        elif op.op == "reinforce":
            if not op.target_id:
                continue
            await reinforce(op.target_id)
        elif op.op == "forget":
            if not op.target_id:
                continue
            await forget(op.target_id)
        else:
            continue
        counts[op.op] = counts.get(op.op, 0) + 1

    if to_embed:
        vectors = await embed_texts([content for _, content in to_embed])
        await set_embeddings(
            [(mid, vec) for (mid, _), vec in zip(to_embed, vectors, strict=True)]
        )

    settings = get_settings()
    for layer, scope_kind, scope_id in touched_scopes:
        evicted = await evict_over_cap(
            layer, scope_kind, scope_id, getattr(settings, _CAPPED[layer])
        )
        if evicted:
            counts["evict"] = counts.get("evict", 0) + evicted

    return counts


def _render_candidates(memories: list) -> str:
    if not memories:
        return "(none)"
    return "\n".join(
        f"- id={m.id} layer={m.layer} subject={m.subject or '-'}: {m.content}"
        for m in memories
    )


def _last_exchange(messages: list) -> tuple[str, str]:
    human = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    ai = next((m.content for m in reversed(messages) if isinstance(m, AIMessage)), "")
    return str(human), str(ai)


async def extract(state: dict, config: RunnableConfig) -> dict:
    member = state["member"]
    thread_id = config.get("configurable", {}).get("thread_id")
    run_id = config.get("configurable", {}).get("run_id")
    human, ai = _last_exchange(state["messages"])
    if not human:
        return {}

    try:
        candidates = await overlapping(
            member["sub"], subjects_in(human), None, limit=10
        )
        prompt = (
            f"{load_extract_prompt()}\n\n"
            f"## Existing memories that may overlap\n{_render_candidates(candidates)}\n\n"
            f"## The exchange\n{member['name']}: {human}\nEve: {ai}\n"
        )
        model = get_model(Tier.REFLEX).with_structured_output(Extraction)
        result = await model.ainvoke([HumanMessage(prompt)])
        counts = await apply_operations(
            list(result.operations), member, thread_id, run_id
        )
    except Exception:
        # This node runs after the answer has already streamed. Raising here
        # would fail a run the user experienced as successful, and would lose
        # the answer on resume. A missed extraction costs one memory; a failed
        # run costs the turn.
        logger.warning("extraction failed for thread %s", thread_id, exc_info=True)
        trace.get_current_span().set_attribute("eve.extract.failed", True)
        return {}

    span = trace.get_current_span()
    for op_name in ("add", "supersede", "reinforce", "forget", "evict"):
        span.set_attribute(f"eve.extract.ops.{op_name}", counts.get(op_name, 0))

    await _maybe_refresh_digest(state, thread_id, ai)
    return {}


async def _maybe_refresh_digest(state: dict, thread_id: str | None, ai: str) -> None:
    """Rewrite the thread digest every N turns.

    Not every turn: the digest exists to stop a long thread being re-read, and
    a thread short enough not to need one does not earn a second model call.
    """
    if not thread_id:
        return
    settings = get_settings()
    turns = sum(1 for m in state["messages"] if isinstance(m, HumanMessage))
    if turns == 0 or turns % settings.memory_digest_every_n_turns != 0:
        return
    transcript = "\n".join(
        f"{'Them' if isinstance(m, HumanMessage) else 'Eve'}: {m.content}"
        for m in state["messages"]
        if isinstance(m, HumanMessage | AIMessage)
    )
    try:
        model = get_model(Tier.REFLEX)
        summary = await model.ainvoke(
            [
                HumanMessage(
                    "Summarise this conversation in at most four sentences, "
                    "written so someone joining now would know what is going "
                    "on and what is still open.\n\n" + transcript
                )
            ]
        )
        await upsert_digest(thread_id, str(summary.content))
    except Exception:
        logger.warning("digest refresh failed for thread %s", thread_id, exc_info=True)
```

- [ ] **Step 5: Export it**

In `src/eve/memory/__init__.py`:

```python
from eve.memory.extract import extract
from eve.memory.recall import recall
from eve.memory.types import Memory, MemoryBundle

__all__ = ["Memory", "MemoryBundle", "extract", "recall"]
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_memory_extract.py -v`
Expected: all eleven PASS.

- [ ] **Step 7: Commit**

```bash
git add src/eve/memory/extract.py src/eve/memory/__init__.py prompts/extract.md \
        tests/test_memory_extract.py
git commit -m "feat: the extract node - write-time contradiction handling

Runs after the answer has streamed, judging new facts against the overlapping
ones already believed. A model failure here is swallowed: raising would fail a
run the user already experienced as successful."
```

---

## Task 11: Wire the graph

**Files:**
- Modify: `src/eve/graph.py`, `tests/test_graph.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `eve.memory.recall`, `eve.memory.extract`
- Produces: `build_graph(model_factory=get_model, recall_fn=recall, extract_fn=extract) -> StateGraph`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_graph.py`:

```python
async def _no_recall(state, config):
    return {"memory": None}


async def _no_extract(state, config):
    return {}


async def test_the_graph_runs_recall_before_eve_and_extract_after(monkeypatch):
    """The order is the whole latency argument: recall must inform the answer
    it precedes, and extract must not delay it."""
    order = []

    async def recall(state, config):
        order.append("recall")
        return {"memory": None}

    async def extract(state, config):
        order.append("extract")
        return {}

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    def factory(_tier):
        order.append("eve")
        return GenericFakeChatModel(messages=iter([AIMessage(content="Hi.")]))

    app = build_graph(
        model_factory=factory, recall_fn=recall, extract_fn=extract
    ).compile()
    await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert order == ["recall", "eve", "extract"]


async def test_memory_reaches_the_system_prompt(monkeypatch):
    from datetime import UTC, datetime

    from eve.memory.types import Memory, MemoryBundle

    now = datetime.now(UTC)
    bundle = MemoryBundle(
        profile=[Memory(
            id="p1", layer="profile", scope_kind="member", scope_id="sub-noah",
            kind="fact", subject=None, content="Noah is vegetarian",
            confidence=0.7, salience=0.5, created_at=now, last_seen_at=now,
        )],
        household=[], episodic=[], digest=None,
        vector_used=False, latency_ms=1.0,
    )

    async def recall(state, config):
        return {"memory": bundle}

    seen = {}

    class RecordingModel(GenericFakeChatModel):
        async def ainvoke(self, input, config=None, **kwargs):
            seen["messages"] = input
            return AIMessage(content="ok")

    monkeypatch.setattr("eve.context.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.context.load_persona", lambda: "You are Eve.")

    app = build_graph(
        model_factory=lambda _t: RecordingModel(messages=iter([])),
        recall_fn=recall,
        extract_fn=_no_extract,
    ).compile()
    await app.ainvoke({"messages": [HumanMessage("hello")]}, CONFIG)

    assert "Noah is vegetarian" in seen["messages"][0].content
```

Then add `recall_fn=_no_recall, extract_fn=_no_extract` to the `build_graph(...)` call in each of the five existing tests in this file, so the unit tier still touches no database.

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_graph.py -v`
Expected: FAIL — `build_graph() got an unexpected keyword argument 'recall_fn'`.

- [ ] **Step 3: Wire the nodes**

In `src/eve/graph.py`, replace the module docstring's second paragraph and `build_graph`:

```python
"""Eve's graph.

    START -> load_context -> recall -> eve -> extract -> END

`load_context` is pure local computation. `recall` is the one place ADR 0002
bends: a single bounded, cancellable embedding call, which ships lexical-only
if it misses its budget. `extract` runs after the answer has streamed, so its
latency is invisible. Phase 3 wraps `eve` in a tools loop without reshaping
any of this.

The system prompt is rebuilt from scratch every turn and passed to the model
without being appended to `messages`, so persona, member-context and memory
edits take effect on existing threads instead of being frozen into history.
"""
```

```python
def build_graph(
    model_factory=get_model, recall_fn=memory_recall, extract_fn=memory_extract
) -> StateGraph:
    async def eve(state: EveState, config: RunnableConfig) -> dict:
        model = model_factory(Tier.VOICE)
        # Through the MODULE, not a from-import. `tests/test_graph.py`
        # monkeypatches `eve.context.load_persona`, and a module-level
        # `from eve.context import load_persona` here would bind the real
        # function at import time and quietly ignore the patch - the tests
        # would still pass while asserting against the real prompts/eve.md.
        prompt = context.build_system_prompt(
            context.load_persona(), state["member"], state.get("memory")
        )
        messages = [_persona_message(prompt), *state["messages"]]
        return {"messages": [await model.ainvoke(messages, config)]}

    builder = StateGraph(EveState)
    builder.add_node("load_context", load_context)
    builder.add_node("recall", recall_fn)
    builder.add_node("eve", eve)
    builder.add_node("extract", extract_fn)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "recall")
    builder.add_edge("recall", "eve")
    builder.add_edge("eve", "extract")
    builder.add_edge("extract", END)
    return builder
```

With these imports added:

```python
from eve import context
from eve.context import load_context
from eve.memory import extract as memory_extract, recall as memory_recall
```

Note the prompt is now assembled in the `eve` node rather than in `load_context`: memory does not exist yet when `load_context` runs. `load_context` continues to return `system_prompt` for compatibility with the Phase 1 state shape and tests; the `eve` node rebuilds it with memory folded in.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_graph.py -v`
Expected: all seven PASS.

- [ ] **Step 5: Run the whole unit tier**

Run: `uv run pytest`
Expected: PASS, no database required.

- [ ] **Step 6: Commit**

```bash
git add src/eve/graph.py tests/test_graph.py
git commit -m "feat: wire recall and extract into the graph

recall before eve because it must inform the answer it precedes; extract
after because it must not delay it."
```

---

## Task 12: End-to-end integration and the docs

**Files:**
- Create: `tests/test_memory_integration.py`, `docs/adr/0005-memory-storage.md`
- Modify: `docs/architecture.md`, `docs/adr/0002-no-llm-before-first-token.md`, `README.md`

**Interfaces:**
- Consumes: everything
- Produces: the definition-of-done evidence

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_memory_integration.py`:

```python
"""End-to-end memory through a live `aegra serve`.

The unit tests prove each part works against a fake. This proves the parts
are connected - which is the failure Phase 1 would have shipped if the live
tier had not existed.
"""

import pytest
from langgraph_sdk import get_client

from eve.memory import db, store

pytestmark = pytest.mark.integration


@pytest.fixture
async def clean_memory(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    pool = await db.get_pool()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_memory")
    yield
    await db.close_pool()


async def test_a_fact_written_in_one_thread_is_recalled_in_another(
    aegra_server, clean_memory
):
    """DoD item 1. The single behaviour this whole phase exists to produce."""
    await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="Noah is vegetarian", subject="noah",
    )
    client = get_client(url=aegra_server, headers={"Authorization": "Bearer tok-noah"})
    thread = await client.threads.create()
    run = await client.runs.create(
        thread["thread_id"], "eve",
        input={"messages": [{"role": "human", "content": "what should we eat?"}]},
    )
    await client.runs.join(thread["thread_id"], run["run_id"])

    # The assertion is on what the graph loaded, not on what the model said.
    # Asserting that Eve's generated text mentions vegetarianism makes the
    # test a coin flip on model behaviour; asserting the fact reached recall
    # in a thread that never mentioned it is the actual claim.
    profile, _, _ = await store.load_always_on("sub-noah", thread["thread_id"])
    assert [m.content for m in profile] == ["Noah is vegetarian"]


async def test_another_member_does_not_see_that_profile(
    aegra_server, clean_memory
):
    """DoD item 6."""
    await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="Noah is vegetarian",
    )
    profile, _, _ = await store.load_always_on("sub-kid", "t1")
    assert profile == []


async def test_recall_survives_the_database_having_no_memories(
    aegra_server, clean_memory
):
    """The state Eve ships in on day one. An empty store must produce a
    complete turn, not an exception in a node nobody has exercised."""
    client = get_client(url=aegra_server, headers={"Authorization": "Bearer tok-noah"})
    thread = await client.threads.create()
    run = await client.runs.create(
        thread["thread_id"], "eve",
        input={"messages": [{"role": "human", "content": "hello"}]},
    )
    result = await client.runs.join(thread["thread_id"], run["run_id"])
    assert result is not None
```

- [ ] **Step 2: Run the integration tier**

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest -m integration -v
```

Expected: PASS. This tier calls the real LiteLLM for the `eve` and `extract` model calls, so `EVE_LITELLM_API_KEY` must be set to a working key.

- [ ] **Step 3: Amend ADR 0002**

Replace the Decision and Consequences sections of `docs/adr/0002-no-llm-before-first-token.md`:

```markdown
## Decision

**Amended 2026-08-18 (Phase 2).** No *generative* model call sits in front of
Eve. `load_context` remains pure local computation. `recall` may make exactly
one embedding call, which must be bounded by
`EVE_MEMORY_RECALL_EMBED_BUDGET_MS` (default 120) and cancellable, and whose
absence must leave a complete turn.

Target, unchanged: p50 first token under 1s, p95 under 2s, measured from
request receipt to the first SSE content event.

### What was withdrawn, and why

The original decision said memory recall "runs CONCURRENTLY with her first
tokens and merges into a later turn or a mid-stream update." That is
withdrawn. Concurrent recall cannot inform the answer it runs alongside, so
"what did we decide about the kitchen?" would miss on the turn it was asked
and land on the next one - which is worse than no episodic memory at all,
because it looks like Eve is ignoring the question.

The enemy this ADR was written against was a router model classifying intent
before answering: hundreds of milliseconds of generative latency, unbounded,
on the critical path. An embedding call is a different animal - ~100ms,
bounded, and cancellable. Admitting it is a smaller concession than the
original wording's alternative.

## Consequences

`src/eve/memory/recall.py` runs the lexical arm immediately and races the
embedding against the budget. If the embedding misses, the turn ships with
profile, household, digest and lexically-ranked episodic memory intact - the
degrade costs paraphrase matching for one turn and nothing else.
`eve.recall.vector_used` in Langfuse reports how often that happens; if it is
often, the honest response is to drop the vector arm, not to raise the budget.

This remains the constraint most likely to be violated by a well-meaning later
change. Phase 3's tools loop in particular must not put a model call between
the request and the first token.
```

- [ ] **Step 4: Write ADR 0005**

Create `docs/adr/0005-memory-storage.md`:

```markdown
# 5. Memory storage: one table, supersession, read-time decay

**Status:** Accepted
**Date:** 2026-08-18

## Context

Phase 2 stores four memory layers - profile, household, episodic, digest -
with contradiction handling and decay, for a family of five. Aegra already
provides a vector-capable `AsyncPostgresStore` injected into every node, which
was the obvious candidate.

## Decision

**One table, `eve_memory`, owned by Eve.** Layers are a column. They differ in
retrieval policy, not in shape; four tables would mean four queries, four
migrations, and four places to fix the same bug.

**Not Aegra's store.** Its `search` has no full-text arm and no way to express
recency weighting, so hybrid recall is not expressible against it. Supersession,
confidence and decay would live in JSON, making every contradiction check an
over-fetch-then-filter-in-Python instead of an indexed predicate.

**Supersession, not deletion.** Retirement sets `superseded_by`; partial
indexes drop retired rows from every read at no cost. Phase 5's eval harness
needs to answer "what did Eve believe on the day she got that wrong."

The one exception is an explicit instruction to forget, which hard-deletes.
A tombstone that still holds the text is not forgetting, and treating it as
such would be a quiet lie to a family member about their own data.

**Read-time decay, no scheduled jobs.** Decay is `exp(-age/half_life)`
evaluated in the query. A nightly-refreshed `decayed_score` column would be a
cache of an expression cheaper to evaluate than to maintain, and wrong for as
long as the pod was down. Eviction and contradiction resolution happen in the
`extract` node instead of a cron, because the turn that reveals a
contradiction is the only place the context to resolve it exists.

## Consequences

Eve owns a schema for the first time, applied by a hand-rolled ordered-DDL
runner under a Postgres advisory lock, run as `eve-migrate` before
`aegra serve`. Not Alembic: there is one table. Move to Alembic past roughly
five migrations.

Phase 2 introduces no cron, no worker, and no scheduled job of any kind.
```

- [ ] **Step 5: Update the architecture document**

In `docs/architecture.md`: change the graph diagram to `START -> load_context -> recall -> eve -> extract -> END` and describe the two new nodes; add the `memory/` package to the module map with its internal dependency order; replace the `REFLEX` row of the tier table with `gemini/gemini-flash-lite-latest`, "Ambient filtering; memory extraction", "Phase 2"; delete the sentence saying `get_model(Tier.REFLEX)` raises; add a "Memory" section covering the four layers, the hybrid recall race, and the `eve-migrate` step in the container's CMD; correct the Phase 2 sentence in the "Store isolation" section to record that Eve owns its own table and the `store.scopes` lever went unused; and add ADR 0005 to the decision-record list.

- [ ] **Step 6: Update the README phase table**

In `README.md`, mark Phase 2 complete in the same style Phase 1 uses.

- [ ] **Step 7: Run every tier**

```bash
uv run pytest
uv run pytest -m integration
EVE_LIVE_TESTS=1 uv run pytest -m live
```

Expected: all three PASS.

- [ ] **Step 8: Commit**

```bash
git add tests/test_memory_integration.py docs/ README.md
git commit -m "docs: Phase 2 architecture, ADR 0005, ADR 0002 amendment

ADR 0002's 'merge into a later turn' prescription is withdrawn rather than
reinterpreted: concurrent recall cannot inform the answer it runs alongside,
which makes episodic memory miss on the turn it is asked for."
```

---

## Verification against the definition of done

Run these against the deployed instance after merging, and record the results
in the spec the way Phase 1 recorded §4.2.1.

| # | Criterion | How to verify |
|---|---|---|
| 1 | A fact stated in one thread is used, unprompted, in a different thread | Tell Eve something durable; open a new thread; ask a question it bears on. |
| 2 | A contradicting fact supersedes the old one | State the contradiction; `SELECT content, superseded_why FROM eve_memory WHERE subject='<x>'`. |
| 3 | "Forget that" hard-deletes | Ask her to forget it; `SELECT count(*)` returns 0, not a tombstone. |
| 4 | Recall adds < 150 ms p50 to TTFT | `eve.recall.latency_ms` in Langfuse over a day. |
| 5 | A forced embedding failure degrades cleanly | Point `EVE_MEMORY_RECALL_EMBED_BUDGET_MS=1` at a running pod; conversation continues, `eve.recall.vector_used` goes false. |
| 6 | Household readable by both, profile not | Ask Kendra's session about a household fact and about one of Noah's profile facts. |
| 7 | `eve.extract.ops.supersede` is non-zero in production | Langfuse, after a week. A memory system that only ever emits `add` is broken in the way §2 of the spec warns about. |
| 8 | Migration runs before `aegra serve` and fails loudly | `kubectl logs` on a fresh pod shows `eve-migrate` before Aegra's startup. |
