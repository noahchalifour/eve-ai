# Health Coach Specialist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ask_health`, a fourth specialist answering sleep, recovery, and training-load questions from the family's WHOOP and Oura wearables.

**Architecture:** Two `httpx` clients in eve-tools normalize both providers into one provider-agnostic shape; a thin fan-out layer merges them per member. Because WHOOP rotates its refresh token on every refresh, eve-tools gains its first piece of writable state — a single Postgres table under its own restricted role, with refreshes serialized by a row lock. The specialist itself is a stock `build_specialist` call.

**Tech Stack:** Python 3.12, FastAPI, httpx, psycopg 3 / psycopg_pool, Alembic, LangChain `create_agent`, pytest + respx.

**Spec:** [`docs/superpowers/specs/2026-09-01-eve-health-coach-design.md`](../specs/2026-09-01-eve-health-coach-design.md)

## Global Constraints

Every task's requirements implicitly include this section.

- **`None` never means zero.** A normalized field is `None` if and only if that provider does not measure it. WHOOP has no `steps`; Oura has no `strain_0_21`. Emitting `0` is a defect, not a rounding choice. (Spec §4.1)
- **`days` is clamped to `1..14` by eve-tools**, never trusted from the caller. (Spec §4)
- **Day attribution comes from the provider, never from a UTC instant.** Oura returns a local `day` string; WHOOP records carry `timezone_offset`. eve-tools does not know member timezones and must not start receiving them. (Spec §4.3.3)
- **`src/eve_tools/` imports nothing from `src/eve/`.** eve-tools gets its own pool in `src/eve_tools/db.py`; it does not reuse `eve.memory.db.get_pool`. (Spec §3.2)
- **eve-tools holds `SELECT, INSERT, UPDATE` on `eve_oauth_token` and nothing else.** No `DELETE`, no DDL, no other table. Alembic (Eve's, private `eve_alembic_version`) owns the DDL. (Spec §3.1–3.2)
- **Settings env prefix is `EVE_TOOLS_`** (`pydantic_settings`, `extra="ignore"`). Field `database_url` resolves `EVE_TOOLS_DATABASE_URL`.
- **HTTP in unit tests is faked with `respx`** (already a dev dependency). Never hit a real provider in the default test tier.
- **DB-backed tests carry `pytestmark = pytest.mark.integration`** and use `postgresql://eve:eve@127.0.0.1:15432/eve` from `docker-compose.test.yml`. The default `pytest` run excludes them (`addopts = ["-m", "not integration and not live and not docker"]`).
- **WHOOP base:** `https://api.prod.whoop.com/developer`. Token endpoint: `https://api.prod.whoop.com/oauth/oauth2/token`. **Oura base:** `https://api.ouraring.com/v2/usercollection`.

## One refinement to the spec

Spec §6's file table omits a fan-out layer. Merging two providers per member, clamping `days`, and assembling the `unconfigured` key is real logic that does not belong in an `app.py` lambda (the existing `_HANDLERS` entries are one-line argument adapters, and this would be the first that is not). Task 8 adds **`src/eve_tools/health.py`** for it. Nothing else in the spec changes.

## Prerequisites (not code — do not block on these)

Spec §7 P1–P4. P3/P4 are one `home-lab-infrastructure` PR (Postgres role + grants, `EVE_TOOLS_DATABASE_URL` in the ExternalSecret, NetworkPolicy egress to CNPG plus `api.prod.whoop.com` and `api.ouraring.com`). **Hard prerequisite for deploy, not for this plan.** Every task below is buildable and testable without it.

---

## File Structure

| File | Responsibility |
|---|---|
| `alembic/versions/0005_eve_oauth_token.py` | The table. DDL only. |
| `src/eve_tools/db.py` | eve-tools' own connection pool. Nothing else. |
| `src/eve_tools/oauth_store.py` | Token row read/write, and the refresh-under-row-lock protocol. Knows nothing about WHOOP or Oura specifically. |
| `src/eve_tools/whoop.py` | WHOOP HTTP + normalizers. One provider. |
| `src/eve_tools/oura.py` | Oura HTTP + normalizers. One provider. |
| `src/eve_tools/health.py` | Fan-out across configured providers, `days` clamping, `unconfigured` assembly. Knows both providers, neither's wire format. |
| `src/eve/specialists/health.py` | `ask_health` — three tools, prompt, permission. |
| `scripts/health_oauth_setup.py` | One-time per-member provisioning. |

Each provider client is independently replaceable behind `health.py`; `oauth_store.py` is provider-agnostic so a third wearable is a new client plus a row, not a change to the locking protocol.

---

## Task 1: The token table and eve-tools' pool

**Files:**
- Create: `alembic/versions/0005_eve_oauth_token.py`
- Create: `src/eve_tools/db.py`
- Modify: `src/eve_tools/settings.py` (add five fields)
- Test: `tests/test_eve_tools_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `eve_tools.db.get_pool() -> AsyncConnectionPool`, `eve_tools.db.close_pool() -> None`. Table `eve_oauth_token` with columns `provider, member_sub, access_token, refresh_token, expires_at, updated_at`, primary key `(provider, member_sub)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eve_tools_db.py`:

```python
"""eve-tools' own pool, against the real Postgres. ADR 0016: eve-tools holds
one table under its own role, so this proves the pool opens and the table
Alembic created is the shape oauth_store expects - not that Alembic works.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

TEST_DSN = "postgresql://eve:eve@127.0.0.1:15432/eve"


@pytest.fixture
async def migrated(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", TEST_DSN)
    monkeypatch.setenv("EVE_TOOLS_DATABASE_URL", TEST_DSN)
    from eve.settings import get_settings
    from eve_tools.settings import get_tools_settings

    get_settings.cache_clear()
    get_tools_settings.cache_clear()

    from eve.memory import db as eve_db
    from eve_tools import db as tools_db

    await eve_db.close_pool()
    await eve_db.migrate()
    await tools_db.close_pool()
    yield
    await tools_db.close_pool()
    await eve_db.close_pool()


async def test_the_pool_opens_from_the_tools_settings(migrated):
    from eve_tools import db

    pool = await db.get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT 1")
        assert await cur.fetchone() == (1,)


async def test_the_oauth_token_table_has_the_columns_the_store_needs(migrated):
    from eve_tools import db

    pool = await db.get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'eve_oauth_token'"
        )
        columns = {name: nullable for name, nullable in await cur.fetchall()}
    assert columns == {
        "provider": "NO",
        "member_sub": "NO",
        "access_token": "NO",
        # Nullable on purpose: a non-rotating credential (an Oura PAT) is a
        # normal row, not a special case. Spec 3.1.
        "refresh_token": "YES",
        "expires_at": "YES",
        "updated_at": "NO",
    }


async def test_the_primary_key_is_provider_plus_member(migrated):
    """Two members with the same provider must coexist, and one member must
    not get two rows for one provider - the store's upsert relies on it."""
    from eve_tools import db

    pool = await db.get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT a.attname FROM pg_index i"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid"
            "   AND a.attnum = ANY(i.indkey)"
            " WHERE i.indrelid = 'eve_oauth_token'::regclass AND i.indisprimary"
        )
        assert {row[0] for row in await cur.fetchall()} == {"provider", "member_sub"}


async def test_get_pool_without_a_url_says_which_variable_is_missing(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_DATABASE_URL", "")
    from eve_tools.settings import get_tools_settings

    get_tools_settings.cache_clear()
    from eve_tools import db

    await db.close_pool()
    with pytest.raises(RuntimeError, match="EVE_TOOLS_DATABASE_URL"):
        await db.get_pool()
```

Note the last test has no `migrated` fixture and needs no database — but the module-level `integration` marker still applies, which is correct: it imports a module whose only reason to exist is the database.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose -f docker-compose.test.yml up -d postgres && uv run pytest tests/test_eve_tools_db.py -m integration -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'eve_tools.db'`.

- [ ] **Step 3: Add the five settings fields**

In `src/eve_tools/settings.py`, inside `ToolsSettings`, after the Monarch fields:

```python
    # Phase: health coach. eve-tools' first writable state - one table, its
    # own role. Deliberately a SEPARATE connection string from
    # EVE_DATABASE_URL: that one is a superuser-ish role with every Eve table
    # in reach, and ADR 0016's whole isolation argument is that eve-tools'
    # role is not. Pointing this at EVE_DATABASE_URL "because it's the same
    # database" would silently undo it.
    database_url: str = ""
    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    oura_client_id: str = ""
    oura_client_secret: str = ""
```

- [ ] **Step 4: Write the migration**

Create `alembic/versions/0005_eve_oauth_token.py`:

```python
"""Per-member OAuth tokens for the health providers.

Revision ID: 0005_eve_oauth_token
Revises: 0004_eve_computer_task

eve-tools' first piece of persistent state, and the reason ADR 0016 exists.
WHOOP returns a NEW refresh_token on every refresh, so the environment-variable
pattern every other eve-tools credential uses cannot hold one: it would go
stale on first use and auth would break at the next restart.

The DDL is here, in Eve's Alembic history, rather than in eve-tools: ADR 0016
grants eve-tools SELECT/INSERT/UPDATE on this table and nothing more - no DDL,
no other table.

`refresh_token` and `expires_at` are nullable so a non-rotating credential is
an ordinary row whose refresh path is simply never entered.
"""
from alembic import op

revision = "0005_eve_oauth_token"
down_revision = "0004_eve_computer_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_oauth_token (
          provider      text        NOT NULL,
          member_sub    text        NOT NULL,
          access_token  text        NOT NULL,
          refresh_token text,
          expires_at    timestamptz,
          updated_at    timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (provider, member_sub)
        )
        """
    )
    # No secondary index: every read is a point lookup on the full primary key.


def downgrade() -> None:
    op.execute("DROP TABLE eve_oauth_token")
```

- [ ] **Step 5: Write `src/eve_tools/db.py`**

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_db.py -m integration -v`
Expected: PASS, 4 tests.

- [ ] **Step 7: Confirm the default tier still passes and skips these**

Run: `uv run pytest tests/test_eve_tools_db.py -v`
Expected: 4 deselected, 0 failed. If any of them *ran*, the `pytestmark` is missing.

- [ ] **Step 8: Commit**

```bash
git add alembic/versions/0005_eve_oauth_token.py src/eve_tools/db.py \
        src/eve_tools/settings.py tests/test_eve_tools_db.py
git commit -m "feat(health): add the eve_oauth_token table and eve-tools' own pool

eve-tools' first writable state. Separate DSN from EVE_DATABASE_URL on
purpose: ADR 0016's isolation argument is that eve-tools' Postgres role
reaches one table, and sharing Eve's pool would hand it Eve's role."
```

---

## Task 2: Token rows — read, upsert, and which providers a member has

**Files:**
- Create: `src/eve_tools/oauth_store.py`
- Test: `tests/test_eve_tools_oauth_store.py`

**Interfaces:**
- Consumes: `eve_tools.db.get_pool` (Task 1).
- Produces:
  - `class NotConnected(Exception)` — no row for that provider+member.
  - `class ReconnectRequired(Exception)` — a refresh was attempted and rejected.
  - `async get_row(provider: str, member_sub: str) -> dict | None`
  - `async save(provider: str, member_sub: str, access_token: str, refresh_token: str | None, expires_at: datetime | None) -> None` (upsert)
  - `async configured_providers(member_sub: str) -> list[str]` — sorted.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eve_tools_oauth_store.py`:

```python
"""The token store. Provider-agnostic on purpose: a third wearable should be
a new client plus a row, not a change to this protocol.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

TEST_DSN = "postgresql://eve:eve@127.0.0.1:15432/eve"


@pytest.fixture
async def store(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", TEST_DSN)
    monkeypatch.setenv("EVE_TOOLS_DATABASE_URL", TEST_DSN)
    from eve.settings import get_settings
    from eve_tools.settings import get_tools_settings

    get_settings.cache_clear()
    get_tools_settings.cache_clear()

    from eve.memory import db as eve_db
    from eve_tools import db as tools_db, oauth_store

    await eve_db.close_pool()
    await eve_db.migrate()
    await tools_db.close_pool()
    pool = await tools_db.get_pool()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_oauth_token")
    yield oauth_store
    await tools_db.close_pool()
    await eve_db.close_pool()


async def test_a_missing_row_reads_as_none(store):
    assert await store.get_row("whoop", "sub-noah") is None


async def test_save_then_read_round_trips_every_field(store):
    expires = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    await store.save("whoop", "sub-noah", "acc-1", "ref-1", expires)
    row = await store.get_row("whoop", "sub-noah")
    assert row["access_token"] == "acc-1"
    assert row["refresh_token"] == "ref-1"
    assert row["expires_at"] == expires


async def test_save_twice_updates_rather_than_duplicating(store):
    """The upsert is what makes rotation safe to repeat. A second INSERT
    would violate the primary key and take the whole request down."""
    await store.save("whoop", "sub-noah", "acc-1", "ref-1", None)
    await store.save("whoop", "sub-noah", "acc-2", "ref-2", None)
    row = await store.get_row("whoop", "sub-noah")
    assert (row["access_token"], row["refresh_token"]) == ("acc-2", "ref-2")


async def test_a_non_rotating_credential_is_an_ordinary_row(store):
    """An Oura PAT has no refresh token and no expiry. Spec 1.1 - this must
    not need a special case."""
    await store.save("oura", "sub-noah", "pat-1", None, None)
    row = await store.get_row("oura", "sub-noah")
    assert row["refresh_token"] is None
    assert row["expires_at"] is None


async def test_two_members_hold_the_same_provider_independently(store):
    await store.save("whoop", "sub-noah", "acc-noah", None, None)
    await store.save("whoop", "sub-kendra", "acc-kendra", None, None)
    noah = await store.get_row("whoop", "sub-noah")
    kendra = await store.get_row("whoop", "sub-kendra")
    assert noah["access_token"] == "acc-noah"
    assert kendra["access_token"] == "acc-kendra"


async def test_configured_providers_lists_only_the_members_own_rows(store):
    await store.save("whoop", "sub-noah", "a", None, None)
    await store.save("oura", "sub-kendra", "b", None, None)
    assert await store.configured_providers("sub-noah") == ["whoop"]
    assert await store.configured_providers("sub-kendra") == ["oura"]
    assert await store.configured_providers("sub-nobody") == []


async def test_configured_providers_is_sorted(store):
    """health.py's `unconfigured` list is compared in tests; an unstable
    order there would make those tests flap."""
    await store.save("whoop", "sub-noah", "a", None, None)
    await store.save("oura", "sub-noah", "b", None, None)
    assert await store.configured_providers("sub-noah") == ["oura", "whoop"]


async def test_updated_at_moves_on_every_save(store):
    await store.save("whoop", "sub-noah", "acc-1", None, None)
    first = (await store.get_row("whoop", "sub-noah"))["updated_at"]
    await store.save("whoop", "sub-noah", "acc-2", None, None)
    second = (await store.get_row("whoop", "sub-noah"))["updated_at"]
    assert second > first
    assert second > datetime.now(UTC) - timedelta(minutes=5)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_oauth_store.py -m integration -v`
Expected: FAIL — `ImportError: cannot import name 'oauth_store'`.

- [ ] **Step 3: Write the store's read/write half**

Create `src/eve_tools/oauth_store.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_oauth_store.py -m integration -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_tools/oauth_store.py tests/test_eve_tools_oauth_store.py
git commit -m "feat(health): add OAuth token row read and upsert

Upsert rather than insert because rotation runs this repeatedly against one
row. refresh_token/expires_at nullable so a non-rotating credential is an
ordinary row, not a special case."
```

---

## Task 3: Refresh under a row lock

This is the task the spec calls out as least worth skipping (§9): concurrent rotation is the failure that breaks auth silently and stays broken.

**Files:**
- Modify: `src/eve_tools/oauth_store.py`
- Test: `tests/test_eve_tools_oauth_store.py` (append)

**Interfaces:**
- Consumes: `get_row`, `save` (Task 2).
- Produces:
  - `SKEW_SECONDS = 120`
  - `async access_token(provider: str, member_sub: str, refresh: Refresher) -> str`
  - `async refresh_now(provider: str, member_sub: str, refresh: Refresher) -> str`
  - where `Refresher = Callable[[str], Awaitable[dict]]`, called with the current refresh token and returning `{"access_token": str, "refresh_token": str | None, "expires_in": int | None}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eve_tools_oauth_store.py`:

```python
async def test_a_fresh_token_is_returned_without_refreshing(store):
    future = datetime.now(UTC) + timedelta(hours=1)
    await store.save("whoop", "sub-noah", "acc-1", "ref-1", future)
    calls = []

    async def refresh(token):
        calls.append(token)
        raise AssertionError("must not refresh a fresh token")

    assert await store.access_token("whoop", "sub-noah", refresh) == "acc-1"
    assert calls == []


async def test_a_null_expiry_never_refreshes(store):
    """An Oura PAT has no expiry. Treating NULL as "expired long ago" would
    refresh it on every single call - with no refresh token to do it with."""
    await store.save("oura", "sub-noah", "pat-1", None, None)

    async def refresh(token):
        raise AssertionError("must not refresh a non-expiring credential")

    assert await store.access_token("oura", "sub-noah", refresh) == "pat-1"


async def test_a_token_inside_the_skew_window_refreshes(store):
    """Expiring in 30s is expiring mid-request. SKEW_SECONDS is 120."""
    await store.save(
        "whoop", "sub-noah", "acc-1", "ref-1",
        datetime.now(UTC) + timedelta(seconds=30),
    )

    async def refresh(token):
        assert token == "ref-1"
        return {"access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600}

    assert await store.access_token("whoop", "sub-noah", refresh) == "acc-2"
    row = await store.get_row("whoop", "sub-noah")
    assert row["refresh_token"] == "ref-2", "the rotated token must be persisted"
    assert row["expires_at"] > datetime.now(UTC) + timedelta(minutes=50)


async def test_an_expired_token_refreshes(store):
    await store.save(
        "whoop", "sub-noah", "acc-1", "ref-1",
        datetime.now(UTC) - timedelta(hours=2),
    )

    async def refresh(token):
        return {"access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600}

    assert await store.access_token("whoop", "sub-noah", refresh) == "acc-2"


async def test_a_missing_row_raises_not_connected(store):
    async def refresh(token):
        raise AssertionError("nothing to refresh")

    with pytest.raises(store.NotConnected):
        await store.access_token("whoop", "sub-noah", refresh)


async def test_a_rejected_refresh_raises_reconnect_required(store):
    await store.save(
        "whoop", "sub-noah", "acc-1", "ref-1",
        datetime.now(UTC) - timedelta(hours=2),
    )

    async def refresh(token):
        raise RuntimeError("400 invalid_grant")

    with pytest.raises(store.ReconnectRequired, match="whoop"):
        await store.access_token("whoop", "sub-noah", refresh)


async def test_an_expired_token_with_no_refresh_token_raises_reconnect_required(store):
    """Expired and nothing to refresh with. Must not silently return the dead
    access token."""
    await store.save(
        "whoop", "sub-noah", "acc-1", None,
        datetime.now(UTC) - timedelta(hours=2),
    )

    async def refresh(token):
        raise AssertionError("there is no refresh token to use")

    with pytest.raises(store.ReconnectRequired):
        await store.access_token("whoop", "sub-noah", refresh)


async def test_refresh_now_refreshes_even_a_fresh_token(store):
    """The reactive path: the provider answered 401 on a token that has not
    reached its stated expiry, because it was revoked server-side."""
    await store.save(
        "whoop", "sub-noah", "acc-1", "ref-1",
        datetime.now(UTC) + timedelta(hours=1),
    )

    async def refresh(token):
        return {"access_token": "acc-2", "refresh_token": "ref-2", "expires_in": 3600}

    assert await store.refresh_now("whoop", "sub-noah", refresh) == "acc-2"


async def test_two_concurrent_refreshes_rotate_the_token_exactly_once(store):
    """The bug this whole task exists for. WHOOP issues a new refresh token
    on every refresh; two callers refreshing at once would each rotate the
    other's token away, leaving a stored token the provider has already
    invalidated and auth broken until someone re-runs the setup script.

    A real Postgres is required: FOR UPDATE semantics are the thing under
    test, and a fake would assert nothing.
    """
    import asyncio

    await store.save(
        "whoop", "sub-noah", "acc-0", "ref-0",
        datetime.now(UTC) - timedelta(hours=2),
    )
    refreshes = []

    async def refresh(token):
        refreshes.append(token)
        # Hold the lock long enough that a naive implementation interleaves.
        await asyncio.sleep(0.3)
        n = len(refreshes)
        return {
            "access_token": f"acc-{n}",
            "refresh_token": f"ref-{n}",
            "expires_in": 3600,
        }

    results = await asyncio.gather(
        store.access_token("whoop", "sub-noah", refresh),
        store.access_token("whoop", "sub-noah", refresh),
    )

    assert len(refreshes) == 1, f"refreshed {len(refreshes)} times, must be 1"
    assert refreshes == ["ref-0"]
    # The second caller must return the token the first one stored, not a
    # stale read from before the lock.
    assert results == ["acc-1", "acc-1"]
    assert (await store.get_row("whoop", "sub-noah"))["refresh_token"] == "ref-1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_oauth_store.py -m integration -v`
Expected: FAIL — `AttributeError: module 'eve_tools.oauth_store' has no attribute 'access_token'`.

- [ ] **Step 3: Implement the refresh protocol**

Append to `src/eve_tools/oauth_store.py` (and add `from collections.abc import Awaitable, Callable`, `from datetime import UTC, timedelta` to the imports):

```python
# A token that expires mid-request is a failed request. Refresh this long
# before the stated expiry rather than at it.
SKEW_SECONDS = 120

# Called with the current refresh token; returns the provider's token
# response. The store never learns which provider it is talking to.
Refresher = Callable[[str], Awaitable[dict]]


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
    return fresh["access_token"]


async def access_token(provider: str, member_sub: str, refresh: Refresher) -> str:
    """The proactive path. Reads without a lock first: the overwhelmingly
    common case is a fresh token, and taking a row lock on every health
    question to discover that would serialize them for nothing."""
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
    """The reactive path, for a 401 on a token that has not reached its stated
    expiry - revoked server-side. Callers retry exactly once with the result."""
    return await _refresh_locked(provider, member_sub, refresh, force=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_oauth_store.py -m integration -v`
Expected: PASS, 17 tests.

If `test_two_concurrent_refreshes_rotate_the_token_exactly_once` fails with `refreshed 2 times`, the transaction is not wrapping the `SELECT ... FOR UPDATE` — check that `async with conn.transaction():` encloses both the select and the update.

- [ ] **Step 5: Add the OpenTelemetry attribute**

The `logger.info` above is the floor. Add the span attribute the spec asks for (§10) in `_refresh_locked`, after the `logger.info` and before the **final** `return fresh["access_token"]` — not the early return inside the lock, which is the path where no refresh happened:

```python
    from opentelemetry import trace

    span = trace.get_current_span()
    span.set_attribute("eve.health.token_refreshed", provider)
```

Import at module top instead of inline if the module already imports it. `opentelemetry` is already a transitive dependency — `eve/specialists/base.py` imports it the same way.

- [ ] **Step 6: Re-run the tests**

Run: `uv run pytest tests/test_eve_tools_oauth_store.py -m integration -v`
Expected: PASS, 17 tests. A no-op span outside a trace is valid; nothing should change.

- [ ] **Step 7: Commit**

```bash
git add src/eve_tools/oauth_store.py tests/test_eve_tools_oauth_store.py
git commit -m "feat(health): serialize token refresh with a row lock

WHOOP issues a new refresh token on every refresh, so two concurrent
refreshes would each rotate the other's away and leave auth permanently
broken. SELECT ... FOR UPDATE with a re-check inside the lock, plus a test
against real Postgres that a naive implementation fails."
```

---

## Task 4: WHOOP client — auth plumbing and recovery

**Files:**
- Create: `src/eve_tools/whoop.py`
- Test: `tests/test_eve_tools_whoop.py`

**Interfaces:**
- Consumes: `oauth_store.access_token`, `oauth_store.refresh_now`, `oauth_store.ReconnectRequired` (Task 3).
- Produces:
  - `async get_recovery(member_sub: str, days: int) -> list[dict]` — entries `{"date","source","score_0_100","hrv_ms","resting_hr","temp_deviation_c"}`, newest first.
  - `async _refresh(refresh_token: str) -> dict` (module-internal, passed to the store).
  - `def _record_date(record: dict) -> str | None` — provider-attributed local date.

Note these return a **bare list**, not `{"recovery": [...]}`. Task 8's `health.py` owns the envelope; a client that also built envelopes would make merging two providers awkward.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eve_tools_whoop.py`:

```python
"""WHOOP v2 client and normalizers.

Every test fakes HTTP with respx and the token store with monkeypatch: this
tier must never touch api.prod.whoop.com.
"""

from __future__ import annotations

import httpx
import pytest
import respx

BASE = "https://api.prod.whoop.com/developer"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    """Every client call starts by asking the store for an access token."""
    async def _access_token(provider, member_sub, refresh):
        assert provider == "whoop"
        return "acc-1"

    monkeypatch.setattr("eve_tools.whoop.oauth_store.access_token", _access_token)


def _recovery_record(score_state="SCORED", **score):
    return {
        "cycle_id": 93845,
        "sleep_id": "ec3c2f0e-0000-4000-8000-000000000000",
        "created_at": "2026-09-01T14:02:00.000Z",
        "score_state": score_state,
        "score": {
            "recovery_score": 68,
            "resting_heart_rate": 51,
            "hrv_rmssd_milli": 84.2,
            "skin_temp_celsius": 33.1,
            **score,
        },
    }


@respx.mock
async def test_recovery_maps_whoops_field_names_onto_the_normalized_shape():
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [_recovery_record()]})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": 93845, "start": "2026-09-01T07:30:00.000Z",
             "timezone_offset": "-07:00", "score_state": "SCORED",
             "score": {"strain": 14.2}},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_recovery("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "whoop",
        "score_0_100": 68,
        "hrv_ms": 84.2,
        "resting_hr": 51,
        # WHOOP reports an ABSOLUTE skin temperature; the normalized field is
        # a deviation from baseline. Putting one in the other's field would be
        # the normalizer lying, so it stays None. Spec 4.2.
        "temp_deviation_c": None,
    }]


@respx.mock
async def test_an_unscored_record_yields_nulls_rather_than_raising():
    """A PENDING_SCORE or UNSCORABLE record has no `score` object at all.
    Spec 4.3.2 - one bad day must not take down the whole answer."""
    record = {
        "cycle_id": 93845,
        "created_at": "2026-09-01T14:02:00.000Z",
        "score_state": "PENDING_SCORE",
    }
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [record]})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": 93845, "start": "2026-09-01T07:30:00.000Z",
             "timezone_offset": "-07:00"},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_recovery("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01", "source": "whoop", "score_0_100": None,
        "hrv_ms": None, "resting_hr": None, "temp_deviation_c": None,
    }]


@respx.mock
async def test_no_recovery_yet_this_morning_is_an_empty_list_not_an_error():
    """Spec 4.3.1: WHOOP has no recovery until the sleep cycle closes. Asked
    at 6am before waking, the endpoint legitimately returns nothing."""
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    from eve_tools import whoop

    assert await whoop.get_recovery("sub-noah", days=1) == []


@respx.mock
async def test_the_date_comes_from_the_records_own_timezone_offset():
    """Spec 4.3.3. A cycle starting 2026-09-02T05:30Z at -07:00 is the
    evening of 2026-09-01 in Vancouver. Taking the UTC date would misfile
    every night that begins after 5pm local."""
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [
            {**_recovery_record(), "cycle_id": 111},
        ]})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": 111, "start": "2026-09-02T05:30:00.000Z",
             "timezone_offset": "-07:00"},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_recovery("sub-noah", days=1)
    assert result[0]["date"] == "2026-09-01"


@respx.mock
async def test_results_are_newest_first_and_trimmed_to_days():
    respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [
            {**_recovery_record(), "cycle_id": 1, "score": {"recovery_score": 60}},
            {**_recovery_record(), "cycle_id": 2, "score": {"recovery_score": 70}},
            {**_recovery_record(), "cycle_id": 3, "score": {"recovery_score": 80}},
        ]})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": 1, "start": "2026-08-30T15:00:00.000Z", "timezone_offset": "-07:00"},
            {"id": 2, "start": "2026-08-31T15:00:00.000Z", "timezone_offset": "-07:00"},
            {"id": 3, "start": "2026-09-01T15:00:00.000Z", "timezone_offset": "-07:00"},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_recovery("sub-noah", days=2)
    assert [r["date"] for r in result] == ["2026-09-01", "2026-08-31"]


@respx.mock
async def test_a_401_refreshes_once_and_retries(monkeypatch):
    """Spec 3.3: a token can be revoked before its stated expiry."""
    calls = []

    async def _refresh_now(provider, member_sub, refresh):
        calls.append(provider)
        return "acc-2"

    monkeypatch.setattr("eve_tools.whoop.oauth_store.refresh_now", _refresh_now)

    seen = []

    def _handler(request):
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"records": []})

    respx.get(f"{BASE}/v2/recovery").mock(side_effect=_handler)
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    from eve_tools import whoop

    assert await whoop.get_recovery("sub-noah", days=1) == []
    assert calls == ["whoop"]
    assert seen == ["Bearer acc-1", "Bearer acc-2"]


@respx.mock
async def test_a_second_401_is_raised_rather_than_retried_forever(monkeypatch):
    async def _refresh_now(provider, member_sub, refresh):
        return "acc-2"

    monkeypatch.setattr("eve_tools.whoop.oauth_store.refresh_now", _refresh_now)
    route = respx.get(f"{BASE}/v2/recovery").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    from eve_tools import whoop

    with pytest.raises(httpx.HTTPStatusError):
        await whoop.get_recovery("sub-noah", days=1)
    assert route.call_count == 2


@respx.mock
async def test_refresh_posts_the_form_whoop_documents():
    """WHOOP's token endpoint takes form-encoded body, not JSON, and returns
    a NEW refresh_token every time - the fact this whole design exists for."""
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-2", "refresh_token": "ref-2",
            "expires_in": 3600, "scope": "read:recovery", "token_type": "bearer",
        })
    )
    from eve_tools import whoop

    result = await whoop._refresh("ref-1")
    assert result["access_token"] == "acc-2"
    assert result["refresh_token"] == "ref-2"
    body = dict(pair.split("=") for pair in route.calls[0].request.content.decode().split("&"))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "ref-1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_whoop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_tools.whoop'`.

- [ ] **Step 3: Write the client**

Create `src/eve_tools/whoop.py`:

```python
"""WHOOP v2 client. Plain httpx against a documented REST API - no SDK, no
reverse engineering, nothing like the Monarch situation.

v2, not v1: v1 was retired and its paths are gone. The two subtleties worth
knowing before reading the normalizers:

- A record's `score_state` can be PENDING_SCORE or UNSCORABLE, in which case
  there is NO `score` object. Every field read goes through `_score`.
- Recovery does not exist until the night's sleep cycle closes. Asked at 6am
  before the member wakes, WHOOP returns nothing for today. That is a normal
  answer, and the specialist's prompt says so - it is not an error here.

Returns bare lists, not envelopes: `health.py` merges two providers and owns
the envelope.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

from eve_tools import oauth_store
from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.prod.whoop.com/developer"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
PROVIDER = "whoop"

# One page is enough at days <= 14. Both providers paginate with next_token
# and neither client implements it; raise the limit or add paging if the
# window ever grows.
_PAGE_LIMIT = 25


async def _refresh(refresh_token: str) -> dict:
    """Exchange a refresh token. WHOOP wants a form body, not JSON, and
    returns a NEW refresh_token - `oauth_store` persists it under the row
    lock."""
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.whoop_client_id,
                "client_secret": settings.whoop_client_secret,
                # WHOOP only returns a refresh token when `offline` is asked
                # for; omitting it on refresh hands back a rotation-less
                # response and strands the row one hour later.
                "scope": "offline",
            },
        )
        response.raise_for_status()
        return response.json()


async def _get(member_sub: str, path: str, params: dict) -> dict:
    """One GET, with exactly one refresh-and-retry on 401.

    Bounded at one deliberately: a loop here against a genuinely revoked
    credential would hammer WHOOP's token endpoint on every health question.
    """
    token = await oauth_store.access_token(PROVIDER, member_sub, _refresh)
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in (1, 2):
            response = await client.get(
                f"{BASE_URL}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and attempt == 1:
                token = await oauth_store.refresh_now(
                    PROVIDER, member_sub, _refresh
                )
                continue
            response.raise_for_status()
            return response.json()
    raise AssertionError("unreachable")


def _window(days: int) -> dict:
    """WHOOP takes UTC instants. Widen by a day on each end and let
    `_record_date` decide which local day each record belongs to: a window
    computed in UTC would clip the local days at both edges.
    """
    now = datetime.now(UTC)
    return {
        "start": (now - timedelta(days=days + 1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": _PAGE_LIMIT,
    }


def _score(record: dict) -> dict:
    """The score object, or an empty dict when the record is unscored. Every
    normalizer reads through this so an UNSCORABLE day yields nulls instead of
    a KeyError. Same posture as monarch.get_budgets' non-dict guards."""
    score = record.get("score")
    if not isinstance(score, dict):
        return {}
    return score


def _num(value: object) -> float | int | None:
    """`bool` is an `int` subclass; a score field that came back True must not
    pass as a measurement."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _record_date(record: dict) -> str | None:
    """The record's own local date, from its own `timezone_offset`.

    eve-tools does not know member timezones and this design does not start
    passing them across that boundary (ADR 0006). It does not have to: WHOOP
    tells us. Deriving the date from the UTC instant alone would misfile every
    Vancouver night that starts after 5pm local.
    """
    raw = record.get("start") or record.get("created_at")
    if not raw:
        return None
    try:
        instant = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        logger.warning("unparseable WHOOP timestamp, dropping record: %r", raw)
        return None
    offset = record.get("timezone_offset")
    if offset:
        try:
            sign = -1 if str(offset).startswith("-") else 1
            hours, _, minutes = str(offset).lstrip("+-").partition(":")
            instant = instant + sign * timedelta(
                hours=int(hours), minutes=int(minutes or 0)
            )
        except ValueError:
            logger.warning("unparseable WHOOP timezone_offset: %r", offset)
    return instant.date().isoformat()


def _newest_first(entries: list[dict], days: int) -> list[dict]:
    """One entry per local date, newest first, trimmed to the window. The
    request deliberately over-fetches (`_window`), so the trim happens here on
    provider-attributed dates rather than on UTC arithmetic."""
    by_date: dict[str, dict] = {}
    for entry in entries:
        # First wins: `records` arrives newest-first from WHOOP, and a second
        # entry for one date is a nap or a re-scored duplicate.
        by_date.setdefault(entry["date"], entry)
    return [by_date[d] for d in sorted(by_date, reverse=True)][:days]


async def _cycle_dates(member_sub: str, days: int) -> dict[int, str]:
    """cycle_id -> local date. Recovery records carry no timestamp of their
    own that reflects the night they describe, so the date comes from the
    cycle they belong to."""
    raw = await _get(member_sub, "/v2/cycle", _window(days))
    dates = {}
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            continue
        date = _record_date(record)
        if record.get("id") is not None and date:
            dates[record["id"]] = date
    return dates


async def get_recovery(member_sub: str, days: int) -> list[dict]:
    cycles = await _cycle_dates(member_sub, days)
    raw = await _get(member_sub, "/v2/recovery", _window(days))
    entries = []
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            logger.warning("WHOOP recovery record was not a dict: %r", record)
            continue
        date = cycles.get(record.get("cycle_id")) or _record_date(record)
        if not date:
            continue
        score = _score(record)
        entries.append({
            "date": date,
            "source": PROVIDER,
            "score_0_100": _num(score.get("recovery_score")),
            "hrv_ms": _num(score.get("hrv_rmssd_milli")),
            "resting_hr": _num(score.get("resting_heart_rate")),
            # WHOOP's skin_temp_celsius is an absolute temperature; this field
            # is a deviation from baseline. Different quantities - mapping one
            # to the other would be the normalizer lying. Spec 4.2.
            "temp_deviation_c": None,
        })
    return _newest_first(entries, days)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_whoop.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_tools/whoop.py tests/test_eve_tools_whoop.py
git commit -m "feat(health): add the WHOOP client and recovery normalizer

Dates come from each record's own timezone_offset rather than its UTC
instant - eve-tools does not know member timezones and must not start
receiving them (ADR 0006), and a UTC date misfiles every Vancouver night
beginning after 5pm local."
```

---

## Task 5: WHOOP sleep and activity

**Files:**
- Modify: `src/eve_tools/whoop.py`
- Test: `tests/test_eve_tools_whoop.py` (append)

**Interfaces:**
- Consumes: `_get`, `_window`, `_score`, `_num`, `_record_date`, `_newest_first` (Task 4).
- Produces:
  - `async get_sleep(member_sub: str, days: int) -> list[dict]` — `{"date","source","score_0_100","hours","deep_hours","rem_hours","efficiency_pct","hrv_ms","resting_hr"}`
  - `async get_activity(member_sub: str, days: int) -> list[dict]` — `{"date","source","score_0_100","strain_0_21","active_calories","steps","workouts"}`, `workouts` entries `{"sport","duration_min","avg_hr"}`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eve_tools_whoop.py`:

```python
@respx.mock
async def test_sleep_converts_stage_milliseconds_to_hours():
    respx.get(f"{BASE}/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": "aaaa0000-0000-4000-8000-000000000000",
            "start": "2026-09-01T06:10:00.000Z",
            "timezone_offset": "-07:00",
            "nap": False,
            "score_state": "SCORED",
            "score": {
                "stage_summary": {
                    "total_in_bed_time_milli": 28_800_000,   # 8h
                    "total_awake_time_milli": 1_800_000,     # 0.5h
                    "total_slow_wave_sleep_time_milli": 5_400_000,  # 1.5h
                    "total_rem_sleep_time_milli": 7_200_000,       # 2h
                },
                "sleep_performance_percentage": 88,
                "sleep_efficiency_percentage": 93.5,
            },
        }]})
    )
    from eve_tools import whoop

    result = await whoop.get_sleep("sub-noah", days=1)
    assert result == [{
        "date": "2026-08-31",
        "source": "whoop",
        "score_0_100": 88,
        "hours": 7.5,          # in-bed minus awake
        "deep_hours": 1.5,
        "rem_hours": 2.0,
        "efficiency_pct": 93.5,
        # WHOOP reports HRV and resting HR on the RECOVERY record, not the
        # sleep record. None, not zero. Spec 4.1.
        "hrv_ms": None,
        "resting_hr": None,
    }]


@respx.mock
async def test_a_nap_does_not_displace_the_nights_sleep():
    """`nap: True` records are real but are not "how did I sleep last night"."""
    respx.get(f"{BASE}/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json={"records": [
            {"id": "n", "start": "2026-09-01T21:00:00.000Z",
             "timezone_offset": "-07:00", "nap": True, "score_state": "SCORED",
             "score": {"stage_summary": {"total_in_bed_time_milli": 1_800_000,
                                         "total_awake_time_milli": 0},
                       "sleep_performance_percentage": 20}},
            {"id": "m", "start": "2026-09-01T14:00:00.000Z",
             "timezone_offset": "-07:00", "nap": False, "score_state": "SCORED",
             "score": {"stage_summary": {"total_in_bed_time_milli": 28_800_000,
                                         "total_awake_time_milli": 1_800_000},
                       "sleep_performance_percentage": 88}},
        ]})
    )
    from eve_tools import whoop

    result = await whoop.get_sleep("sub-noah", days=1)
    assert len(result) == 1
    assert result[0]["score_0_100"] == 88


@respx.mock
async def test_unscored_sleep_yields_nulls_not_zero_hours():
    respx.get(f"{BASE}/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": "a", "start": "2026-09-01T06:10:00.000Z",
            "timezone_offset": "-07:00", "nap": False,
            "score_state": "PENDING_SCORE",
        }]})
    )
    from eve_tools import whoop

    result = await whoop.get_sleep("sub-noah", days=1)
    assert result[0]["hours"] is None, "zero hours would read as 'you did not sleep'"
    assert result[0]["deep_hours"] is None


@respx.mock
async def test_activity_maps_strain_and_converts_kilojoules_to_calories():
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": 93845, "start": "2026-09-01T14:00:00.000Z",
            "timezone_offset": "-07:00", "score_state": "SCORED",
            "score": {"strain": 14.2, "kilojoule": 3397.0,
                      "average_heart_rate": 78, "max_heart_rate": 171},
        }]})
    )
    respx.get(f"{BASE}/v2/activity/workout").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": "w", "start": "2026-09-01T16:00:00.000Z",
            "end": "2026-09-01T17:02:00.000Z", "timezone_offset": "-07:00",
            "sport_name": "cycling", "score_state": "SCORED",
            "score": {"average_heart_rate": 138, "strain": 9.1},
        }]})
    )
    from eve_tools import whoop

    result = await whoop.get_activity("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "whoop",
        # WHOOP has no daily activity score. None, not zero.
        "score_0_100": None,
        "strain_0_21": 14.2,
        "active_calories": 812,   # 3397 kJ / 4.184
        # WHOOP has no step count at all. None, not zero - a 0 here would
        # have the coach reporting you never moved. Spec 4.1.
        "steps": None,
        "workouts": [{"sport": "cycling", "duration_min": 62, "avg_hr": 138}],
    }]


@respx.mock
async def test_a_day_with_no_workouts_gets_an_empty_list_not_none():
    """`workouts` is a list field, so its empty value is [] - distinct from a
    scalar the provider does not measure, which is None."""
    respx.get(f"{BASE}/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": 1, "start": "2026-09-01T14:00:00.000Z",
            "timezone_offset": "-07:00", "score_state": "SCORED",
            "score": {"strain": 4.1, "kilojoule": 1000.0},
        }]})
    )
    respx.get(f"{BASE}/v2/activity/workout").mock(
        return_value=httpx.Response(200, json={"records": []})
    )
    from eve_tools import whoop

    result = await whoop.get_activity("sub-noah", days=1)
    assert result[0]["workouts"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_whoop.py -v`
Expected: FAIL — `AttributeError: module 'eve_tools.whoop' has no attribute 'get_sleep'`.

- [ ] **Step 3: Implement both normalizers**

Append to `src/eve_tools/whoop.py`:

```python
def _hours(millis: object) -> float | None:
    value = _num(millis)
    return None if value is None else round(value / 3_600_000, 2)


async def get_sleep(member_sub: str, days: int) -> list[dict]:
    raw = await _get(member_sub, "/v2/activity/sleep", _window(days))
    entries = []
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            logger.warning("WHOOP sleep record was not a dict: %r", record)
            continue
        # A nap is real data but it is not "how did I sleep last night", and
        # letting one win `_newest_first`'s first-wins rule would answer that
        # question with a 30-minute afternoon doze.
        if record.get("nap"):
            continue
        date = _record_date(record)
        if not date:
            continue
        score = _score(record)
        stages = score.get("stage_summary")
        stages = stages if isinstance(stages, dict) else {}
        in_bed = _num(stages.get("total_in_bed_time_milli"))
        awake = _num(stages.get("total_awake_time_milli"))
        asleep = None if in_bed is None else in_bed - (awake or 0)
        entries.append({
            "date": date,
            "source": PROVIDER,
            "score_0_100": _num(score.get("sleep_performance_percentage")),
            "hours": _hours(asleep),
            "deep_hours": _hours(stages.get("total_slow_wave_sleep_time_milli")),
            "rem_hours": _hours(stages.get("total_rem_sleep_time_milli")),
            "efficiency_pct": _num(score.get("sleep_efficiency_percentage")),
            # Both live on the recovery record, not here. None, not zero.
            "hrv_ms": None,
            "resting_hr": None,
        })
    return _newest_first(entries, days)


def _workouts_by_date(records: list) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for record in records or []:
        if not isinstance(record, dict):
            continue
        date = _record_date(record)
        if not date:
            continue
        start, end = record.get("start"), record.get("end")
        duration = None
        if start and end:
            try:
                duration = round(
                    (
                        datetime.fromisoformat(str(end).replace("Z", "+00:00"))
                        - datetime.fromisoformat(str(start).replace("Z", "+00:00"))
                    ).total_seconds()
                    / 60
                )
            except ValueError:
                logger.warning("unparseable WHOOP workout bounds: %r %r", start, end)
        grouped.setdefault(date, []).append({
            "sport": record.get("sport_name"),
            "duration_min": duration,
            "avg_hr": _num(_score(record).get("average_heart_rate")),
        })
    return grouped


async def get_activity(member_sub: str, days: int) -> list[dict]:
    raw = await _get(member_sub, "/v2/cycle", _window(days))
    workouts = _workouts_by_date(
        (await _get(member_sub, "/v2/activity/workout", _window(days))).get("records")
    )
    entries = []
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            logger.warning("WHOOP cycle record was not a dict: %r", record)
            continue
        date = _record_date(record)
        if not date:
            continue
        score = _score(record)
        kilojoules = _num(score.get("kilojoule"))
        entries.append({
            "date": date,
            "source": PROVIDER,
            # WHOOP has no daily activity score; strain is the analogue and
            # has its own field. None, not zero.
            "score_0_100": None,
            "strain_0_21": _num(score.get("strain")),
            "active_calories": (
                None if kilojoules is None else round(kilojoules / 4.184)
            ),
            # WHOOP does not count steps. Spec 4.1: None, never 0.
            "steps": None,
            "workouts": workouts.get(date, []),
        })
    return _newest_first(entries, days)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_whoop.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_tools/whoop.py tests/test_eve_tools_whoop.py
git commit -m "feat(health): add WHOOP sleep and activity normalizers

steps and score_0_100 are None for WHOOP, not 0 - it does not measure them,
and a zero would have the coach reporting you never moved. Naps are dropped
from sleep so an afternoon doze cannot answer 'how did I sleep last night'."
```

---

## Task 6: Oura client — auth plumbing and recovery

The subtle one: Oura's readiness endpoint exposes only *contributor scores*, so raw HRV and resting heart rate have to be joined from the detailed `sleep` collection (spec §4.2).

**Files:**
- Create: `src/eve_tools/oura.py`
- Test: `tests/test_eve_tools_oura.py`

**Interfaces:**
- Consumes: `oauth_store.access_token`, `oauth_store.refresh_now` (Task 3).
- Produces: `async get_recovery(member_sub: str, days: int) -> list[dict]` — same keys as `whoop.get_recovery`, `source: "oura"`. Plus `_refresh`, `_get`, `_window`, `_num`, `_sleep_by_date` for Task 7.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eve_tools_oura.py`:

```python
"""Oura v2 client and normalizers.

The join in `get_recovery` is the thing to keep an eye on: daily_readiness
carries only CONTRIBUTOR scores (0-100 sub-scores), not raw HRV or resting
heart rate. Those live in the detailed `sleep` collection, so recovery is two
requests where WHOOP needs one.
"""

from __future__ import annotations

import httpx
import pytest
import respx

BASE = "https://api.ouraring.com/v2/usercollection"
TOKEN_URL = "https://api.ouraring.com/oauth/token"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    async def _access_token(provider, member_sub, refresh):
        assert provider == "oura"
        return "acc-1"

    monkeypatch.setattr("eve_tools.oura.oauth_store.access_token", _access_token)


def _sleep_record(day="2026-09-01", **overrides):
    return {
        "id": "sleep-1",
        "day": day,
        "type": "long_sleep",
        "total_sleep_duration": 26_640,   # 7.4h
        "deep_sleep_duration": 4_320,     # 1.2h
        "rem_sleep_duration": 6_480,      # 1.8h
        "efficiency": 92,
        "average_hrv": 61,
        "lowest_heart_rate": 48,
        **overrides,
    }


@respx.mock
async def test_recovery_joins_readiness_with_the_sleep_collection():
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [{
            "id": "r-1", "day": "2026-09-01", "score": 81,
            "temperature_deviation": -0.2,
            "contributors": {"hrv_balance": 90, "resting_heart_rate": 95},
        }]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": [_sleep_record()]})
    )
    from eve_tools import oura

    result = await oura.get_recovery("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "oura",
        "score_0_100": 81,
        # Raw values from the sleep collection, NOT the contributor
        # sub-scores (90 and 95) - those are 0-100 ratings, not milliseconds
        # and beats per minute.
        "hrv_ms": 61,
        "resting_hr": 48,
        "temp_deviation_c": -0.2,
    }]


@respx.mock
async def test_readiness_without_a_matching_sleep_row_nulls_the_raw_fields():
    """Not an error: readiness for today can land before the detailed sleep
    row does."""
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "r-1", "day": "2026-09-01", "score": 81},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    result = await oura.get_recovery("sub-noah", days=1)
    assert result[0]["hrv_ms"] is None
    assert result[0]["resting_hr"] is None
    assert result[0]["score_0_100"] == 81


@respx.mock
async def test_the_date_is_ouras_own_local_day_string():
    """Spec 4.3.3. Oura already attributes each row to a local day, so there
    is no timezone arithmetic to get wrong here."""
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "r-1", "day": "2026-08-30", "score": 70},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    assert (await oura.get_recovery("sub-noah", days=3))[0]["date"] == "2026-08-30"


@respx.mock
async def test_a_nap_does_not_win_the_join_over_the_nights_sleep():
    """Oura's `sleep` collection holds every sleep period, naps included.
    The longest one for the day is the night."""
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "r-1", "day": "2026-09-01", "score": 81},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": [
            _sleep_record(type="late_nap", total_sleep_duration=1_800,
                          average_hrv=40, lowest_heart_rate=60),
            _sleep_record(),
        ]})
    )
    from eve_tools import oura

    result = await oura.get_recovery("sub-noah", days=1)
    assert result[0]["hrv_ms"] == 61
    assert result[0]["resting_hr"] == 48


@respx.mock
async def test_results_are_newest_first_and_trimmed_to_days():
    respx.get(f"{BASE}/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "a", "day": "2026-08-30", "score": 60},
            {"id": "b", "day": "2026-08-31", "score": 70},
            {"id": "c", "day": "2026-09-01", "score": 80},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    result = await oura.get_recovery("sub-noah", days=2)
    assert [r["date"] for r in result] == ["2026-09-01", "2026-08-31"]


@respx.mock
async def test_a_401_refreshes_once_and_retries(monkeypatch):
    async def _refresh_now(provider, member_sub, refresh):
        return "acc-2"

    monkeypatch.setattr("eve_tools.oura.oauth_store.refresh_now", _refresh_now)
    seen = []

    def _handler(request):
        seen.append(request.headers["Authorization"])
        if len(seen) == 1:
            return httpx.Response(401, json={"detail": "unauthorized"})
        return httpx.Response(200, json={"data": []})

    respx.get(f"{BASE}/daily_readiness").mock(side_effect=_handler)
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    assert await oura.get_recovery("sub-noah", days=1) == []
    assert seen == ["Bearer acc-1", "Bearer acc-2"]


@respx.mock
async def test_refresh_posts_the_form_oura_documents():
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={
            "access_token": "acc-2", "refresh_token": "ref-2",
            "expires_in": 86400, "token_type": "bearer",
        })
    )
    from eve_tools import oura

    result = await oura._refresh("ref-1")
    assert result["access_token"] == "acc-2"
    body = dict(pair.split("=") for pair in route.calls[0].request.content.decode().split("&"))
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "ref-1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_oura.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_tools.oura'`.

- [ ] **Step 3: Write the client**

Create `src/eve_tools/oura.py`:

```python
"""Oura v2 client. Plain httpx, documented REST.

Two things differ from WHOOP and both simplify life here:

- Oura attributes every row to a local `day` string, so there is no timezone
  arithmetic to get wrong (spec 4.3.3).
- Its tokens are long-lived, and if Personal Access Tokens still work
  (spec 1.1) a row may have no refresh token at all. `oauth_store` already
  treats that as an ordinary row, so nothing here special-cases it.

One thing is harder: `daily_readiness` exposes only CONTRIBUTOR scores -
0-100 sub-ratings, not raw measurements. Raw HRV and resting heart rate live
in the detailed `sleep` collection, so recovery is a two-request join. Reading
`contributors.hrv_balance` as an HRV in milliseconds would report a rating as
a measurement.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

from eve_tools import oauth_store
from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ouraring.com/v2/usercollection"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
PROVIDER = "oura"


async def _refresh(refresh_token: str) -> dict:
    settings = get_tools_settings()
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.oura_client_id,
                "client_secret": settings.oura_client_secret,
            },
        )
        response.raise_for_status()
        return response.json()


async def _get(member_sub: str, path: str, params: dict) -> dict:
    """One GET, exactly one refresh-and-retry on 401. Same bound and the same
    reason as the WHOOP client's."""
    token = await oauth_store.access_token(PROVIDER, member_sub, _refresh)
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in (1, 2):
            response = await client.get(
                f"{BASE_URL}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 401 and attempt == 1:
                token = await oauth_store.refresh_now(
                    PROVIDER, member_sub, _refresh
                )
                continue
            response.raise_for_status()
            return response.json()
    raise AssertionError("unreachable")


def _window(days: int) -> dict:
    """Oura takes local date strings. One extra day at each end because
    eve-tools does not know the member's timezone and "today" here is a UTC
    date - `_newest_first` trims on Oura's own `day` attribution afterwards.
    """
    today = datetime.now(UTC).date()
    return {
        "start_date": (today - timedelta(days=days + 1)).isoformat(),
        "end_date": (today + timedelta(days=1)).isoformat(),
    }


# Spec 4.4: one page is enough at days <= 14. Oura's date-bounded collections
# return the whole window in one response at this size, and neither client
# implements next_token paging - raise this, or add paging, if the window
# ever grows.
_PAGE_LIMIT = 25


def _num(value: object) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _hours(seconds: object) -> float | None:
    """Oura reports durations in SECONDS (WHOOP uses milliseconds)."""
    value = _num(seconds)
    return None if value is None else round(value / 3600, 2)


def _newest_first(entries: list[dict], days: int) -> list[dict]:
    by_date: dict[str, dict] = {}
    for entry in entries:
        by_date.setdefault(entry["date"], entry)
    return [by_date[d] for d in sorted(by_date, reverse=True)][:days]


async def _sleep_by_date(member_sub: str, days: int) -> dict[str, dict]:
    """The night's sleep per local day, from the detailed collection.

    Oura's `sleep` collection holds every sleep period, naps included. The
    longest one for a day is the night - taking the first would let a
    20-minute doze supply the day's HRV and resting heart rate.
    """
    raw = await _get(member_sub, "/sleep", _window(days))
    best: dict[str, dict] = {}
    for record in raw.get("data") or []:
        if not isinstance(record, dict):
            logger.warning("Oura sleep record was not a dict: %r", record)
            continue
        day = record.get("day")
        if not day:
            continue
        current = best.get(day)
        if current is None or (_num(record.get("total_sleep_duration")) or 0) > (
            _num(current.get("total_sleep_duration")) or 0
        ):
            best[day] = record
    return best


async def get_recovery(member_sub: str, days: int) -> list[dict]:
    sleep = await _sleep_by_date(member_sub, days)
    raw = await _get(member_sub, "/daily_readiness", _window(days))
    entries = []
    for record in raw.get("data") or []:
        if not isinstance(record, dict):
            logger.warning("Oura readiness record was not a dict: %r", record)
            continue
        day = record.get("day")
        if not day:
            continue
        night = sleep.get(day) or {}
        entries.append({
            "date": day,
            "source": PROVIDER,
            "score_0_100": _num(record.get("score")),
            # From the sleep collection, NOT contributors.hrv_balance - that
            # is a 0-100 rating, not milliseconds.
            "hrv_ms": _num(night.get("average_hrv")),
            "resting_hr": _num(night.get("lowest_heart_rate")),
            "temp_deviation_c": _num(record.get("temperature_deviation")),
        })
    return _newest_first(entries, days)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_oura.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_tools/oura.py tests/test_eve_tools_oura.py
git commit -m "feat(health): add the Oura client and recovery normalizer

Recovery is a two-request join: daily_readiness carries only 0-100
contributor sub-scores, so raw HRV and resting HR come from the detailed
sleep collection. Reading contributors.hrv_balance as milliseconds would
report a rating as a measurement."
```

---

## Task 7: Oura sleep and activity

**Files:**
- Modify: `src/eve_tools/oura.py`
- Test: `tests/test_eve_tools_oura.py` (append)

**Interfaces:**
- Consumes: `_get`, `_window`, `_num`, `_hours`, `_newest_first`, `_sleep_by_date` (Task 6).
- Produces: `async get_sleep(member_sub, days) -> list[dict]` and `async get_activity(member_sub, days) -> list[dict]`, same keys as their WHOOP counterparts (Task 5).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_eve_tools_oura.py`:

```python
@respx.mock
async def test_sleep_joins_the_daily_score_with_the_detailed_durations():
    """daily_sleep carries the score; the `sleep` collection carries the
    durations. Neither has both."""
    respx.get(f"{BASE}/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "ds-1", "day": "2026-09-01", "score": 81},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": [_sleep_record()]})
    )
    from eve_tools import oura

    result = await oura.get_sleep("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "oura",
        "score_0_100": 81,
        "hours": 7.4,
        "deep_hours": 1.2,
        "rem_hours": 1.8,
        "efficiency_pct": 92,
        "hrv_ms": 61,
        "resting_hr": 48,
    }]


@respx.mock
async def test_a_daily_score_with_no_detailed_row_nulls_the_durations():
    respx.get(f"{BASE}/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "ds-1", "day": "2026-09-01", "score": 81},
        ]})
    )
    respx.get(f"{BASE}/sleep").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    from eve_tools import oura

    result = await oura.get_sleep("sub-noah", days=1)
    assert result[0]["hours"] is None, "zero hours would read as 'you did not sleep'"
    assert result[0]["score_0_100"] == 81


@respx.mock
async def test_activity_maps_score_calories_and_steps():
    respx.get(f"{BASE}/daily_activity").mock(
        return_value=httpx.Response(200, json={"data": [{
            "id": "da-1", "day": "2026-09-01", "score": 88,
            "active_calories": 612, "steps": 11_284,
            "target_calories": 500,
        }]})
    )
    from eve_tools import oura

    result = await oura.get_activity("sub-noah", days=1)
    assert result == [{
        "date": "2026-09-01",
        "source": "oura",
        "score_0_100": 88,
        # Oura has no strain metric at all. None, not zero - a 0 here reads
        # as "you did nothing strenuous", which is a claim. Spec 4.1.
        "strain_0_21": None,
        "active_calories": 612,
        "steps": 11_284,
        # daily_activity has no per-workout breakdown. Empty list, because
        # workouts is a list field. Spec 4.2.
        "workouts": [],
    }]


@respx.mock
async def test_a_missing_step_count_is_none_not_zero():
    respx.get(f"{BASE}/daily_activity").mock(
        return_value=httpx.Response(200, json={"data": [
            {"id": "da-1", "day": "2026-09-01", "score": 88},
        ]})
    )
    from eve_tools import oura

    result = await oura.get_activity("sub-noah", days=1)
    assert result[0]["steps"] is None
    assert result[0]["active_calories"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_oura.py -v`
Expected: FAIL — `AttributeError: module 'eve_tools.oura' has no attribute 'get_sleep'`.

- [ ] **Step 3: Implement both normalizers**

Append to `src/eve_tools/oura.py`:

```python
async def get_sleep(member_sub: str, days: int) -> list[dict]:
    """Two requests, like recovery: `daily_sleep` has the score, the `sleep`
    collection has the durations, and neither has both."""
    detailed = await _sleep_by_date(member_sub, days)
    raw = await _get(member_sub, "/daily_sleep", _window(days))
    entries = []
    for record in raw.get("data") or []:
        if not isinstance(record, dict):
            logger.warning("Oura daily_sleep record was not a dict: %r", record)
            continue
        day = record.get("day")
        if not day:
            continue
        night = detailed.get(day) or {}
        entries.append({
            "date": day,
            "source": PROVIDER,
            "score_0_100": _num(record.get("score")),
            "hours": _hours(night.get("total_sleep_duration")),
            "deep_hours": _hours(night.get("deep_sleep_duration")),
            "rem_hours": _hours(night.get("rem_sleep_duration")),
            "efficiency_pct": _num(night.get("efficiency")),
            "hrv_ms": _num(night.get("average_hrv")),
            "resting_hr": _num(night.get("lowest_heart_rate")),
        })
    return _newest_first(entries, days)


async def get_activity(member_sub: str, days: int) -> list[dict]:
    raw = await _get(member_sub, "/daily_activity", _window(days))
    entries = []
    for record in raw.get("data") or []:
        if not isinstance(record, dict):
            logger.warning("Oura daily_activity record was not a dict: %r", record)
            continue
        day = record.get("day")
        if not day:
            continue
        entries.append({
            "date": day,
            "source": PROVIDER,
            "score_0_100": _num(record.get("score")),
            # Oura has no strain metric. Spec 4.1: None, never 0.
            "strain_0_21": None,
            "active_calories": _num(record.get("active_calories")),
            "steps": _num(record.get("steps")),
            # No per-workout breakdown in daily_activity. A list field's
            # empty value is [], not None.
            "workouts": [],
        })
    return _newest_first(entries, days)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_oura.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_tools/oura.py tests/test_eve_tools_oura.py
git commit -m "feat(health): add Oura sleep and activity normalizers

strain_0_21 is None for Oura, not 0 - it has no strain metric, and a zero
reads as the claim 'you did nothing strenuous'."
```

---

## Task 8: Fan-out across providers, and the dispatch table

**Files:**
- Create: `src/eve_tools/health.py`
- Modify: `src/eve_tools/app.py`
- Test: `tests/test_eve_tools_health.py`
- Test: `tests/test_eve_tools_app.py` (append)

**Interfaces:**
- Consumes: `whoop.get_recovery/get_sleep/get_activity` (Tasks 4–5), `oura.*` (Tasks 6–7), `oauth_store.configured_providers` (Task 2), `oauth_store.NotConnected`, `oauth_store.ReconnectRequired` (Tasks 2–3).
- Produces:
  - `async get_recovery(member_sub: str, days: int = 1) -> dict` → `{"recovery": [...], "unconfigured": [...]}` (`unconfigured` omitted when empty)
  - `async get_sleep(...) -> dict` → `{"sleep": [...], ...}`
  - `async get_activity(...) -> dict` → `{"activity": [...], ...}`
  - `MAX_DAYS = 14`
- Consumed by: `app._HANDLERS` and, later, `eve_ambient.sources.health`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eve_tools_health.py`:

```python
"""The fan-out layer: which providers a member has, merging their answers,
clamping `days`, and the `unconfigured` key.

Both clients are stubbed. Their wire formats are tested in
test_eve_tools_whoop.py and test_eve_tools_oura.py; what matters here is the
merge and the envelope.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def stub(monkeypatch):
    """Both clients plus the store, with recorded calls."""
    calls = {"whoop": [], "oura": [], "providers": []}

    def _client(name, entries):
        async def _get(member_sub, days):
            calls[name].append((member_sub, days))
            return list(entries)

        return _get

    def _configure(providers, whoop_entries=(), oura_entries=()):
        async def _configured(member_sub):
            calls["providers"].append(member_sub)
            return list(providers)

        monkeypatch.setattr(
            "eve_tools.health.oauth_store.configured_providers", _configured
        )
        for metric in ("get_recovery", "get_sleep", "get_activity"):
            monkeypatch.setattr(
                f"eve_tools.health.whoop.{metric}",
                _client("whoop", whoop_entries),
            )
            monkeypatch.setattr(
                f"eve_tools.health.oura.{metric}",
                _client("oura", oura_entries),
            )
        return calls

    return _configure


async def test_a_member_with_one_device_gets_that_devices_entries(stub):
    calls = stub(
        ["whoop"],
        whoop_entries=[{"date": "2026-09-01", "source": "whoop", "score_0_100": 68}],
    )
    from eve_tools import health

    result = await health.get_recovery("sub-noah", days=1)
    assert result == {
        "recovery": [{"date": "2026-09-01", "source": "whoop", "score_0_100": 68}],
        # Oura has no row for this member, and the coach saying so is more
        # useful than silence. Spec 4.3.4.
        "unconfigured": ["oura"],
    }
    assert calls["oura"] == [], "must not call a provider the member has not connected"


async def test_a_member_with_both_devices_gets_both_labelled_by_source(stub):
    """Spec 4: the specialist reports both rather than silently preferring
    one. Two entries per day, each with its own `source`."""
    stub(
        ["oura", "whoop"],
        whoop_entries=[{"date": "2026-09-01", "source": "whoop", "score_0_100": 68}],
        oura_entries=[{"date": "2026-09-01", "source": "oura", "score_0_100": 81}],
    )
    from eve_tools import health

    result = await health.get_recovery("sub-noah", days=1)
    assert "unconfigured" not in result
    assert {e["source"] for e in result["recovery"]} == {"oura", "whoop"}
    assert len(result["recovery"]) == 2


async def test_a_member_with_no_device_gets_an_empty_list_and_both_providers(stub):
    stub([])
    from eve_tools import health

    assert await health.get_recovery("sub-noah", days=1) == {
        "recovery": [],
        "unconfigured": ["oura", "whoop"],
    }


async def test_entries_are_sorted_newest_first_across_providers(stub):
    stub(
        ["oura", "whoop"],
        whoop_entries=[
            {"date": "2026-08-31", "source": "whoop"},
            {"date": "2026-09-01", "source": "whoop"},
        ],
        oura_entries=[{"date": "2026-09-01", "source": "oura"}],
    )
    from eve_tools import health

    result = await health.get_recovery("sub-noah", days=2)
    assert [e["date"] for e in result["recovery"]] == [
        "2026-09-01", "2026-09-01", "2026-08-31",
    ]


@pytest.mark.parametrize(
    "requested,expected",
    [(0, 1), (-5, 1), (1, 1), (14, 14), (15, 14), (900, 14), (None, 1), ("3", 3)],
)
async def test_days_is_clamped_rather_than_trusted(stub, requested, expected):
    """Spec 4: 1..14, enforced here, not by the caller. A model that asks for
    900 days must not turn into 900 days of provider traffic."""
    calls = stub(["whoop"])
    from eve_tools import health

    await health.get_recovery("sub-noah", days=requested)
    assert calls["whoop"] == [("sub-noah", expected)]


async def test_sleep_and_activity_use_their_own_envelope_keys(stub):
    stub(["whoop"], whoop_entries=[{"date": "2026-09-01", "source": "whoop"}])
    from eve_tools import health

    assert "sleep" in await health.get_sleep("sub-noah", days=1)
    assert "activity" in await health.get_activity("sub-noah", days=1)


async def test_a_broken_credential_surfaces_rather_than_reading_as_no_data(
    stub, monkeypatch
):
    """The one failure that must NOT degrade to an empty list. Broken auth
    reported as "no sleep data" would have the coach describing a quiet night
    that never happened."""
    stub(["whoop", "oura"], oura_entries=[{"date": "2026-09-01", "source": "oura"}])
    from eve_tools import health, oauth_store

    async def _boom(member_sub, days):
        raise oauth_store.ReconnectRequired("whoop refused to refresh")

    monkeypatch.setattr("eve_tools.health.whoop.get_recovery", _boom)

    result = await health.get_recovery("sub-noah", days=1)
    # The healthy provider still answers - one broken device must not take
    # down the other.
    assert [e["source"] for e in result["recovery"]] == ["oura"]
    assert "whoop" in result["errors"][0]


async def test_one_providers_transport_failure_does_not_lose_the_others_data(
    stub, monkeypatch
):
    stub(["whoop", "oura"], oura_entries=[{"date": "2026-09-01", "source": "oura"}])
    from eve_tools import health

    async def _boom(member_sub, days):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("eve_tools.health.whoop.get_recovery", _boom)

    result = await health.get_recovery("sub-noah", days=1)
    assert [e["source"] for e in result["recovery"]] == ["oura"]
    assert "whoop" in result["errors"][0]
```

Append to `tests/test_eve_tools_app.py`:

```python
def test_the_health_tools_are_dispatchable():
    """The dispatch table is the whole routing layer - a handler that exists
    but is unregistered 404s at runtime with nothing failing at import."""
    from eve_tools.app import _HANDLERS

    assert "health.get_recovery" in _HANDLERS
    assert "health.get_sleep" in _HANDLERS
    assert "health.get_activity" in _HANDLERS


async def test_health_get_recovery_dispatches_with_member_and_days(monkeypatch):
    mock_get = AsyncMock(return_value={"recovery": []})
    monkeypatch.setattr("eve_tools.app.health.get_recovery", mock_get)
    async with await _client() as client:
        response = await client.post(
            "/invoke",
            json={"tool": "health.get_recovery",
                  "arguments": {"member_sub": "sub-noah", "days": 3}},
            headers={"Authorization": "Bearer test-key"},
        )
    assert response.status_code == 200
    mock_get.assert_awaited_once_with("sub-noah", 3)


async def test_health_get_recovery_defaults_days_to_one(monkeypatch):
    mock_get = AsyncMock(return_value={"recovery": []})
    monkeypatch.setattr("eve_tools.app.health.get_recovery", mock_get)
    async with await _client() as client:
        await client.post(
            "/invoke",
            json={"tool": "health.get_recovery", "arguments": {"member_sub": "sub-noah"}},
            headers={"Authorization": "Bearer test-key"},
        )
    mock_get.assert_awaited_once_with("sub-noah", 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_eve_tools_health.py tests/test_eve_tools_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_tools.health'`.

- [ ] **Step 3: Write the fan-out layer**

Create `src/eve_tools/health.py`:

```python
"""Fan-out across whichever health providers a member has connected.

Knows both providers exist; knows neither's wire format. The clients return
bare lists so this layer owns the envelope - which is what makes "the member
has both devices" a merge rather than a special case in each client.

Also the seam a future `eve_ambient.sources.health` reads through, the way
`eve_ambient.sources.finances` reads `monarch.get_budgets`: the shapes here
are deliberately what a signal source would want, so adding one needs no
reshaping (spec section 8).
"""

from __future__ import annotations

import asyncio
import logging

from eve_tools import oauth_store, oura, whoop

logger = logging.getLogger(__name__)

# Trend questions are why a window exists at all. Past two weeks the answer
# belongs to a chart, not a conversation.
MAX_DAYS = 14

_CLIENTS = {"whoop": whoop, "oura": oura}


def _clamp_days(days: object) -> int:
    """1..MAX_DAYS. Clamped here rather than trusted: `days` arrives from a
    model, and "900" must not become 900 days of provider traffic."""
    try:
        value = int(days)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_DAYS, value))


async def _fan_out(metric: str, key: str, member_sub: str, days: object) -> dict:
    window = _clamp_days(days)
    connected = await oauth_store.configured_providers(member_sub)
    targets = [name for name in _CLIENTS if name in connected]

    async def _one(name: str) -> tuple[str, list[dict] | str]:
        try:
            return name, await getattr(_CLIENTS[name], metric)(member_sub, window)
        except oauth_store.ReconnectRequired as exc:
            # Must never degrade to an empty list: "no sleep data" would have
            # the coach describing a quiet night that never happened.
            return name, f"{name}: {exc}"
        except oauth_store.NotConnected as exc:
            return name, f"{name}: {exc}"
        except Exception as exc:
            logger.warning("%s %s failed for %s", name, metric, member_sub, exc_info=True)
            return name, f"{name}: {exc.__class__.__name__}: {exc}"

    entries: list[dict] = []
    errors: list[str] = []
    for name, outcome in await asyncio.gather(*(_one(n) for n in targets)):
        if isinstance(outcome, str):
            errors.append(outcome)
        else:
            entries.extend(outcome)

    # Newest first across providers. Two devices give two entries per date,
    # each labelled by `source`; the specialist reports both rather than
    # choosing between them.
    entries.sort(key=lambda e: (e.get("date") or "", e.get("source") or ""), reverse=True)

    result: dict = {key: entries}
    unconfigured = sorted(name for name in _CLIENTS if name not in connected)
    if unconfigured:
        result["unconfigured"] = unconfigured
    if errors:
        result["errors"] = errors
    return result


async def get_recovery(member_sub: str, days: int = 1) -> dict:
    return await _fan_out("get_recovery", "recovery", member_sub, days)


async def get_sleep(member_sub: str, days: int = 1) -> dict:
    return await _fan_out("get_sleep", "sleep", member_sub, days)


async def get_activity(member_sub: str, days: int = 1) -> dict:
    return await _fan_out("get_activity", "activity", member_sub, days)
```


- [ ] **Step 4: Wire the dispatch table**

In `src/eve_tools/app.py`, extend the import and add three entries after the `finances.*` block:

```python
from eve_tools import caldav_client, gmail, health, home_assistant, mcp_dispatch, monarch
```

```python
    "health.get_recovery": lambda a: health.get_recovery(
        a["member_sub"], a.get("days", 1)
    ),
    "health.get_sleep": lambda a: health.get_sleep(a["member_sub"], a.get("days", 1)),
    "health.get_activity": lambda a: health.get_activity(
        a["member_sub"], a.get("days", 1)
    ),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_health.py tests/test_eve_tools_app.py -v`
Expected: PASS. `test_eve_tools_health.py` has 15 tests (the `days` parametrize contributes 8).

- [ ] **Step 6: Commit**

```bash
git add src/eve_tools/health.py src/eve_tools/app.py \
        tests/test_eve_tools_health.py tests/test_eve_tools_app.py
git commit -m "feat(health): fan out across a member's connected providers

Broken auth surfaces in an errors key rather than degrading to an empty
list: 'no sleep data' would have the coach describing a quiet night that
never happened. One provider failing does not lose the other's data."
```

---

## Task 9: The specialist and the graph wiring

**Files:**
- Create: `src/eve/specialists/health.py`
- Modify: `src/eve/graph.py` (import block near line 53; `_BASE_TOOLS` at line 65)
- Modify: `family.yaml` (both members' `permissions`)
- Test: `tests/test_specialists_health.py`
- Test: `tests/test_graph.py:616-617` (the `_BASE_TOOLS` name-set assertion)

**Interfaces:**
- Consumes: `eve.specialists.base.build_specialist`, `eve.tools_client.invoke`, the `health.*` tool names from Task 8.
- Produces: `eve.specialists.health.ask_health: BaseTool`, permission `"health"`. Consumed by `graph._BASE_TOOLS`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_specialists_health.py`:

```python
"""tests/test_specialists_health.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.health as health_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import CONFIG, MEMBER, STATE


def _model_with(*ai_messages):
    return lambda: FakeToolCallingModel(messages=iter(ai_messages))


async def test_ask_health_reads_recovery_through_eve_tools(monkeypatch):
    tool_call = {
        "name": "get_recovery",
        "args": {"days": 1},
        "id": "call-1",
        "type": "tool_call",
    }
    factory = _model_with(
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Your recovery is 68% - a normal training day."),
    )
    monkeypatch.setattr("eve.specialists.health._model_for_test", factory)
    importlib.reload(health_module)
    monkeypatch.setattr("eve.specialists.health._model_for_test", factory)

    mock_invoke = AsyncMock(return_value='{"recovery": []}')
    monkeypatch.setattr(health_module, "invoke", mock_invoke)

    member = {**MEMBER, "permissions": ["health"]}
    result = await health_module.ask_health.ainvoke(
        {
            "request": "how's my recovery?",
            "state": {**STATE, "member": member},
            "config": {"configurable": {"member": member}},
        }
    )
    assert result == "Your recovery is 68% - a normal training day."
    # member_sub crosses the boundary, not the member's name, role, or
    # timezone. ADR 0006 / 0016.
    mock_invoke.assert_awaited_once_with(
        "health.get_recovery", {"member_sub": "sub-noah", "days": 1}
    )


async def test_a_member_without_the_health_permission_is_denied(monkeypatch):
    def _never():
        raise AssertionError("the model must not be built for a denied call")

    monkeypatch.setattr("eve.specialists.health._model_for_test", _never)
    importlib.reload(health_module)
    monkeypatch.setattr("eve.specialists.health._model_for_test", _never)

    member = {**MEMBER, "permissions": ["home.control"]}
    result = await health_module.ask_health.ainvoke(
        {
            "request": "how did I sleep?",
            "state": {**STATE, "member": member},
            "config": CONFIG,
        }
    )
    assert "Permission denied" in result
    assert "health" in result


def test_the_prompt_carries_the_clinical_guardrail():
    """Spec 5.1. A wearable-derived LLM opinion on a symptom reads as
    authoritative when it should not."""
    prompt = health_module.SYSTEM_PROMPT
    assert "doctor" in prompt
    assert "diagnose" in prompt


def test_the_prompt_explains_that_null_is_not_zero():
    """Spec 4.1 is a contract the model has to honour too - it is the thing
    that turns a None into 'WHOOP doesn't count steps' instead of 'you took
    no steps'."""
    assert "null" in health_module.SYSTEM_PROMPT.lower()


def test_the_prompt_explains_the_morning_gap():
    """Spec 4.3.1: an empty recovery result before wake-up is normal, and a
    coach that reports it as a fault is wrong every single morning."""
    assert "scored" in health_module.SYSTEM_PROMPT.lower()


def test_all_three_tools_exist_with_the_names_eve_tools_dispatches():
    """The eve-tools handler table keys are health.get_recovery / get_sleep /
    get_activity; a renamed tool here would 404 at runtime with nothing
    failing at import."""
    assert {
        health_module.get_recovery.name,
        health_module.get_sleep.name,
        health_module.get_activity.name,
    } == {"get_recovery", "get_sleep", "get_activity"}
```

Modify the assertion in `tests/test_graph.py` (currently lines 616-617):

```python
    # The Phase 3/4 toolset is untouched.
    assert {"ask_home", "ask_mail", "ask_finances", "ask_health", "search_skills",
            "search_memory"} <= names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_specialists_health.py tests/test_graph.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.specialists.health'`, and the `test_graph.py` assertion fails on the missing `ask_health`.

- [ ] **Step 3: Write the specialist**

Create `src/eve/specialists/health.py`:

```python
"""Health coach specialist: WHOOP and Oura via eve-tools. Read-only - neither
provider is written to, and nothing has asked for it (design doc section 8).

Per-member, so every tool passes `member_sub` across the eve-tools boundary
the way `mail.py` does. ADR 0016 records that this makes health the second
domain to do so; the subs stay opaque and eve-tools still learns no names,
roles, timezones, or permissions.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.tools_client import invoke

SYSTEM_PROMPT = (
    "You are the family's health coach. You answer questions about sleep, "
    "recovery, and training load using WHOOP and Oura data.\n\n"
    "State every number exactly as returned; never estimate or interpolate. "
    "A null field means that device does not measure it - say so rather than "
    "treating it as zero. An empty recovery result early in the morning "
    "means last night's sleep has not been scored yet, which is normal; say "
    "that rather than reporting a problem. If a member has two devices, "
    "report both rather than choosing between them.\n\n"
    "You give practical guidance on training, rest, and sleep habits "
    "grounded in these numbers. You do not diagnose, interpret symptoms, or "
    "give medical advice - if a question touches illness, injury, "
    "medication, or anything clinical, say it needs a doctor."
)


def _model_for_test():
    return get_model(Tier.MECHANICAL)


# `config` precedes `days` because `days` carries a default and Python forbids
# a non-defaulted parameter after one. `@tool` excludes RunnableConfig-
# annotated parameters from the tool schema, so position does not affect what
# the model sees.
@tool
async def get_recovery(config: RunnableConfig, days: int = 1) -> str:
    """Recovery score, HRV, and resting heart rate for recent days."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke(
        "health.get_recovery", {"member_sub": member_sub, "days": days}
    )


@tool
async def get_sleep(config: RunnableConfig, days: int = 1) -> str:
    """Sleep duration, stages, and efficiency for recent nights."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke("health.get_sleep", {"member_sub": member_sub, "days": days})


@tool
async def get_activity(config: RunnableConfig, days: int = 1) -> str:
    """Training load, calories, steps, and workouts for recent days."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke(
        "health.get_activity", {"member_sub": member_sub, "days": days}
    )


# No per-tool permission check: all three are reads, so unlike mail.send there
# is nothing to gate beyond the coarse ask_health boundary.
ask_health = build_specialist(
    name="health",
    tools=[get_recovery, get_sleep, get_activity],
    system_prompt=SYSTEM_PROMPT,
    permission="health",
    model_factory=lambda _tier: _model_for_test(),
)
```

- [ ] **Step 4: Wire it into the graph**

In `src/eve/graph.py`, add to the import block (alphabetical, after `finances`):

```python
from eve.specialists.health import ask_health
```

and extend `_BASE_TOOLS`:

```python
_BASE_TOOLS = [ask_home, ask_mail, ask_finances, ask_health, search_skills, search_memory]
```

- [ ] **Step 5: Grant the permission**

In `family.yaml`, add to **both** Noah's and Kendra's `permissions` lists:

```yaml
      # Health coach: WHOOP and Oura recovery, sleep, and training load.
      # Read-only; a bare noun like `finances` because there is no write
      # surface to distinguish it from.
      - health
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_specialists_health.py tests/test_graph.py tests/test_family.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole default tier**

Run: `uv run pytest`
Expected: PASS. A failure here is almost certainly another `_BASE_TOOLS` or tool-count assertion elsewhere in `test_graph.py` — search for `ask_finances` and for hard-coded tool counts.

- [ ] **Step 8: Commit**

```bash
git add src/eve/specialists/health.py src/eve/graph.py family.yaml \
        tests/test_specialists_health.py tests/test_graph.py
git commit -m "feat(health): add the ask_health specialist and wire it up

Unconditional like the other three specialists rather than switched: the
credential-absent case already degrades to a clean 'no device connected'
answer. write_skill/propose_tool/dispatch_computer_task are switched because
they act; three reads do not need a kill switch."
```

---

## Task 10: Provisioning script and environment documentation

**Files:**
- Create: `scripts/health_oauth_setup.py`
- Modify: `.env.example`
- Test: `tests/test_health_oauth_setup.py`

**Interfaces:**
- Consumes: `oauth_store.save` (Task 2), the `_refresh` token endpoints (Tasks 4, 6).
- Produces: a CLI writing the first `eve_oauth_token` row per member per provider. Not imported by anything.

- [ ] **Step 1: Write the failing test**

Create `tests/test_health_oauth_setup.py`:

```python
"""The provisioning script's pure parts. The browser round trip is not tested
- there is nothing to assert about it that would not be asserting httpx.

Loaded by path, not imported: `scripts/` has no `__init__.py` and is operator
tooling rather than part of the package. Same approach as
tests/test_gmail_oauth_setup.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "health_oauth_setup.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("health_oauth_setup", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = _load()


def test_the_authorize_url_carries_the_offline_scope_for_whoop():
    """WHOOP only issues a refresh token when `offline` is requested. Without
    it provisioning appears to succeed and auth dies an hour later - the worst
    available failure mode."""
    url = setup.authorize_url("whoop", "client-1", "http://localhost:8321/callback", "st")
    assert "api.prod.whoop.com" in url
    assert "offline" in url
    assert "response_type=code" in url
    assert "state=st" in url


def test_the_authorize_url_requests_ouras_daily_scopes():
    url = setup.authorize_url("oura", "client-1", "http://localhost:8321/callback", "st")
    assert "cloud.ouraring.com" in url
    assert "daily" in url


def test_an_unknown_provider_is_rejected_by_name():
    with pytest.raises(ValueError, match="garmin"):
        setup.authorize_url("garmin", "c", "http://localhost/cb", "st")


def test_expiry_is_computed_from_expires_in():
    from datetime import UTC, datetime

    result = setup.expires_at({"expires_in": 3600})
    assert result is not None
    assert 3500 < (result - datetime.now(UTC)).total_seconds() < 3700


def test_a_response_without_an_expiry_stores_null():
    """A non-expiring credential is an ordinary row whose refresh path is
    never entered (oauth_store._is_stale)."""
    assert setup.expires_at({"access_token": "a"}) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_health_oauth_setup.py -v`
Expected: FAIL — `FileNotFoundError` from `spec_from_file_location`, at import time of the test module itself (`setup = _load()` runs at collection).

- [ ] **Step 3: Write the script**

Create `scripts/health_oauth_setup.py`:

```python
"""One-time provisioning of a member's WHOOP or Oura credential.

    uv run python -m scripts.health_oauth_setup whoop <member_sub>

Runs the authorization-code flow against a loopback redirect, then writes the
first `eve_oauth_token` row. Run once per member per device. After that
`oauth_store` keeps the row current on its own - which for WHOOP means
rotating the refresh token on every refresh, the reason that table exists.

Mirrors `scripts/gmail_oauth_setup.py` in shape. It does NOT write to Vault:
the credential's home is Postgres, not a secret store, because it changes
without a human involved.

Requires EVE_TOOLS_DATABASE_URL and the provider's client id/secret in the
environment. Point the DSN at the same database the cluster uses, or run it
against a port-forward.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import webbrowser
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

REDIRECT_PORT = 8321
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"

_PROVIDERS = {
    "whoop": {
        "authorize": "https://api.prod.whoop.com/oauth/oauth2/auth",
        "token": "https://api.prod.whoop.com/oauth/oauth2/token",
        # `offline` is what makes WHOOP issue a refresh token at all. Omit it
        # and provisioning looks like it worked until the access token expires
        # an hour later with nothing to renew it.
        "scope": (
            "offline read:recovery read:sleep read:workout read:cycles "
            "read:profile"
        ),
    },
    "oura": {
        "authorize": "https://cloud.ouraring.com/oauth/authorize",
        "token": "https://api.ouraring.com/oauth/token",
        "scope": "daily heartrate personal",
    },
}


def authorize_url(provider: str, client_id: str, redirect_uri: str, state: str) -> str:
    config = _PROVIDERS.get(provider)
    if config is None:
        raise ValueError(
            f"unknown provider {provider!r}; expected one of "
            f"{', '.join(sorted(_PROVIDERS))}"
        )
    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
    })
    return f"{config['authorize']}?{query}"


def expires_at(token_response: dict) -> datetime | None:
    """None when the provider states no expiry - an ordinary row whose
    refresh path is never entered."""
    expires_in = token_response.get("expires_in")
    if not expires_in:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(expires_in))


def _await_code(expected_state: str) -> str:
    """Serve exactly one loopback request and return its `code`."""
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib's casing
            params = parse_qs(urlparse(self.path).query)
            captured.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab.")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("localhost", REDIRECT_PORT), Handler)
    server.handle_request()
    server.server_close()

    if captured.get("state") != expected_state:
        raise RuntimeError("state mismatch on the OAuth callback; start over")
    code = captured.get("code")
    if not code:
        raise RuntimeError(f"no code in the callback: {captured}")
    return code


async def _exchange(provider: str, code: str, client_id: str, secret: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _PROVIDERS[provider]["token"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "client_secret": secret,
            },
        )
        response.raise_for_status()
        return response.json()


async def _main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in _PROVIDERS:
        print(__doc__)
        print(f"providers: {', '.join(sorted(_PROVIDERS))}")
        return 2
    provider, member_sub = sys.argv[1], sys.argv[2]

    from eve_tools import oauth_store
    from eve_tools.settings import get_tools_settings

    settings = get_tools_settings()
    client_id = getattr(settings, f"{provider}_client_id")
    secret = getattr(settings, f"{provider}_client_secret")
    if not client_id or not secret:
        print(
            f"set EVE_TOOLS_{provider.upper()}_CLIENT_ID and "
            f"EVE_TOOLS_{provider.upper()}_CLIENT_SECRET first",
            file=sys.stderr,
        )
        return 1

    state = secrets.token_urlsafe(16)
    url = authorize_url(provider, client_id, REDIRECT_URI, state)
    print(f"\nOpening {provider} authorization. If nothing opens, visit:\n{url}\n")
    webbrowser.open(url)

    code = await asyncio.to_thread(_await_code, state)
    tokens = await _exchange(provider, code, client_id, secret)

    if provider == "whoop" and not tokens.get("refresh_token"):
        print(
            "WHOOP returned no refresh_token - the `offline` scope was not "
            "granted. Auth will break in an hour. Re-run and approve every "
            "requested scope.",
            file=sys.stderr,
        )
        return 1

    try:
        await oauth_store.save(
            provider,
            member_sub,
            tokens["access_token"],
            tokens.get("refresh_token"),
            expires_at(tokens),
        )
    except Exception as exc:
        print(f"\ncould not store the credential: {exc}", file=sys.stderr)
        print("\n--- token response, store it by hand ---")
        print(tokens)
        return 1

    print(f"\nstored the {provider} credential for {member_sub}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_health_oauth_setup.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 5: Document the environment variables**

Append to `.env.example`, after the Monarch block:

```bash
# --- health coach (WHOOP + Oura) ---
# eve-tools' only writable state: one table under its own Postgres role
# (ADR 0016). Deliberately NOT the same connection string as
# EVE_DATABASE_URL - that role reaches every Eve table, and eve-tools' must
# reach exactly one. In the cluster this points at a role granted
# SELECT/INSERT/UPDATE on eve_oauth_token and nothing else.
EVE_TOOLS_DATABASE_URL=postgresql://eve:eve@localhost:15432/eve

# From the WHOOP developer dashboard. The redirect URI registered there must
# be http://localhost:8321/callback for scripts/health_oauth_setup.py.
EVE_TOOLS_WHOOP_CLIENT_ID=replace-me
EVE_TOOLS_WHOOP_CLIENT_SECRET=replace-me

# From the Oura developer dashboard, same redirect URI. A freshly registered
# app may be capped at a small number of users until Oura approves it.
EVE_TOOLS_OURA_CLIENT_ID=replace-me
EVE_TOOLS_OURA_CLIENT_SECRET=replace-me
```

- [ ] **Step 6: Run the whole default tier**

Run: `uv run pytest`
Expected: PASS. `tests/test_settings.py` may assert the exact set of `ToolsSettings` fields — if so, extend it with the five new names.

- [ ] **Step 7: Commit**

```bash
git add scripts/health_oauth_setup.py tests/test_health_oauth_setup.py .env.example
git commit -m "feat(health): add OAuth provisioning for WHOOP and Oura

Refuses to finish if WHOOP returns no refresh_token: without the offline
scope, provisioning appears to succeed and auth dies an hour later, which is
the worst available failure mode."
```

---

## Task 11: ADR 0016 and the architecture document

**Files:**
- Create: `docs/adr/0016-eve-tools-owns-a-credential-table.md`
- Modify: `docs/architecture.md`
- Test: none (documentation)

**Interfaces:**
- Consumes: nothing.
- Produces: the written record of ADR 0006's amendment. Referenced by comments already written in `src/eve_tools/db.py`, `src/eve_tools/settings.py`, `alembic/versions/0005_eve_oauth_token.py`, and `src/eve/specialists/health.py`.

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0016-eve-tools-owns-a-credential-table.md`:

```markdown
# 16. eve-tools owns one credential table

**Status:** Accepted
**Date:** 2026-09-01
**Amends:** [ADR 0006](0006-eve-tools-isolation.md)

## Context

ADR 0006 gave `eve-tools` "no permission data, no Kubernetes credentials of
its own, and no family roster data beyond the member subject identifiers
that per-member credentials are keyed by" — and, implicitly but centrally,
no database. Every third-party credential it held was either static (the
Home Assistant token) or non-rotating (Google refresh tokens), so
environment variables were sufficient storage and the service kept no
persistent state at all.

The health coach specialist breaks that. **WHOOP returns a new
`refresh_token` on every refresh** and the previous one cannot be relied on
afterwards. A rotating token in an environment variable is stale after its
first use: the next pod restart reads a dead value from the ExternalSecret
and auth is broken until a human re-runs the provisioning flow. There is no
version of "store it in the environment" that works.

Alternatives considered and rejected:

- **A writable file on a PVC.** Avoids a database role, but adds a volume,
  needs file locking the moment eve-tools has more than one replica, and
  the concurrency problem below is the hard part either way.
- **Keeping the rotated token in process memory.** Works until restart,
  then breaks permanently. Not a design.
- **Having Eve hold the token and pass it down per call.** Puts a
  third-party credential in Eve's container, which is the specific thing
  ADR 0006 exists to prevent, and the refresh call needs the client secret
  anyway.
- **Oura only, deferring WHOOP.** Real option, and it would have needed no
  new infrastructure. Rejected because both members' devices were in scope
  and deferring one of two providers is not delivering the feature.

## Decision

`eve-tools` gets a Postgres connection of its own, and exactly one table.

- The table is `eve_oauth_token`, keyed `(provider, member_sub)`. Its DDL
  lives in Eve's Alembic history (revision `0005_eve_oauth_token`, private
  `eve_alembic_version` table per ADR 0011). eve-tools has no DDL grant and
  never migrates.
- eve-tools connects via `EVE_TOOLS_DATABASE_URL`, a **separate connection
  string** resolving to a **dedicated Postgres role** granted
  `SELECT, INSERT, UPDATE` on `eve_oauth_token` and nothing else. No
  `DELETE`. No grant on `eve_memory`, `eve_pat`, `eve_tool`,
  `eve_computer_task`, or any Aegra table. Sharing Eve's connection string
  would hand eve-tools Eve's role and forfeit the whole point.
- `src/eve_tools/` continues to import nothing from `src/eve/`. It has its
  own pool in `src/eve_tools/db.py` rather than reusing
  `eve.memory.db.get_pool`.
- Token refresh is serialized by `SELECT ... FOR UPDATE` on the row, with
  the freshness check repeated inside the lock. This is a correctness
  requirement, not an optimization: two concurrent refreshes would each
  rotate the other's token away, leaving a stored credential the provider
  has already invalidated.

This ADR also corrects a detail of 0006's text. 0006 described
`member_sub` crossing the boundary as one narrow exception, for `mail.*`.
It is now two domains, `mail.*` and `health.*`. The identifiers remain
opaque; eve-tools still learns no names, roles, timezones, or permissions.
Notably, the health clients derive each record's local date from the
provider's own attribution (Oura's `day` string, WHOOP's
`timezone_offset`) specifically so that member timezones do **not** have to
cross the boundary.

## Consequences

ADR 0006's isolation claim weakens from "no database" to **"one table, its
own role, no read access to anything else."** That is a real reduction and
the reason this is a written amendment rather than an implementation
detail: a compromised eve-tools can now read and rewrite every family
member's health OAuth tokens. It still cannot reach the cluster, the family
roster, anyone's permissions, Eve's memory, or Eve's own credentials.

The blast radius grew by exactly the credentials eve-tools was always going
to hold — the tokens are for APIs it already calls. What is new is that they
are now durable and shared between replicas rather than injected per pod.

Two operational costs follow. eve-tools now needs network egress to the
CNPG cluster, so it can fail to start for a reason unrelated to any
third-party API. And the Postgres role and its grants are provisioned
out-of-band in `home-lab-infrastructure`; a deploy that ships this code
without them starts and then fails every health question with a connection
error.
```

- [ ] **Step 2: Verify the cross-references resolve**

Run:

```bash
grep -rn "0016" src/ alembic/ docs/superpowers/specs/2026-09-01-eve-health-coach-design.md
ls docs/adr/0006-eve-tools-isolation.md docs/adr/0011-alembic-with-a-private-version-table.md
```

Expected: every `ADR 0016` mention written in Tasks 1–9 now points at a file that exists, and both amended/cited ADRs are present.

- [ ] **Step 3: Update the architecture document**

`docs/architecture.md` is the living description of the deployed system. Make four edits, matching the surrounding prose style:

1. In the specialists section, add Health beside Home, Mail, and Finances: three read tools (`get_recovery`, `get_sleep`, `get_activity`), permission `health`, unconditional.
2. In the eve-tools section, note the Postgres connection and its single-table role, linking ADR 0016.
3. In the data-stores section, add `eve_oauth_token` to the table list with its purpose and its key.
4. In the permissions list, add `health`.

Do not restructure the document; add to it.

- [ ] **Step 4: Full verification**

```bash
uv run pytest
docker compose -f docker-compose.test.yml up -d postgres
uv run pytest -m integration
```

Expected: both tiers pass. The integration tier includes the concurrency test from Task 3.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0016-eve-tools-owns-a-credential-table.md docs/architecture.md
git commit -m "docs(health): record ADR 0016, eve-tools owns one credential table

ADR 0006's isolation claim weakens from 'no database' to 'one table, its own
role, no read access to anything else'. That is a real reduction, so it is a
written amendment rather than an implementation detail."
```

---

## Definition of Done

- [ ] `uv run pytest` passes.
- [ ] `uv run pytest -m integration` passes, including `test_two_concurrent_refreshes_rotate_the_token_exactly_once`.
- [ ] A member with no connected device gets `unconfigured`, not an error.
- [ ] A member with a broken credential gets an `errors` entry, **not** an empty list.
- [ ] No normalized scalar field is ever `0` where the provider does not measure it.
- [ ] `grep -rn "from eve\." src/eve_tools/` returns nothing.
- [ ] The `home-lab-infrastructure` PR (spec §7 P3/P4) is open or merged before release.
- [ ] Live verification, once P1–P4 land: ask Eve "how did I sleep last night?" and confirm the numbers match the WHOOP and Oura apps. Nothing in the test suite can catch a normalizer that maps a real field to the wrong place.
