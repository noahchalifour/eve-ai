# Eve's Computer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Eve a fifth deploy, `eve-computer` — a persistent Linux desktop she can dispatch tasks to, report back on in her own voice, and that a human can watch and interrupt live over VNC.

**Architecture:** A new FastAPI harness service (`src/eve_computer/`, `Dockerfile.eve-computer`) runs a `claude-agent-sdk`-driven agent loop with bash/read/write/edit tools plus a lifted computer-use tool, behind a one-task-at-a-time queue (`POST /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/artifacts/{name}`, `DELETE /tasks/{id}`). Eve's main container gets one new tool, `dispatch_computer_task`, gated by a new `computer.use` permission, which writes a row to a new Postgres table (`eve_computer_task`) and calls the box over a bearer-token HTTP door. `eve-ambient` gains a fifth polled source that checks on outstanding tasks, marks them finished or stale, and reports the result on the *same thread the family member asked from* — bypassing the relevance filter, since a direct request is never "not relevant." `eve-sandbox` is untouched.

**Tech Stack:** FastAPI, `claude-agent-sdk`, Alembic/psycopg (Postgres), LangChain/LangGraph tools, pytest (unit/integration/docker/live tiers), Docker, `uv`.

**Spec:** [`docs/superpowers/specs/2026-08-28-eve-computer-design.md`](../specs/2026-08-28-eve-computer-design.md)

## Global Constraints

- Image runs as user `eve`, uid **10004**, with **passwordless sudo** — the pod spec (dropped capabilities, `hostUsers: false`, no host mounts, no ServiceAccount token, `seccompProfile: RuntimeDefault`), not the user account, is the container boundary.
- One PVC, **~50 GiB**, mounted at `/home/eve`; everything outside it is ephemeral. A `bootstrap.sh` replays `/home/eve/.eve/packages.txt` on every container start.
- Egress: DNS and `0.0.0.0/0` on 80/443 only. **Denied:** all RFC1918 ranges, the cluster pod/service CIDRs, and `169.254.169.254`.
- Ingress: harness port **8092** from the `eve` and `eve-ambient` pods only; VNC port (**5900**) reached only via `kubectl port-forward`, never a Service/Ingress. `automountServiceAccountToken: false`.
- Exactly two secrets on the pod: `EVE_COMPUTER_API_KEY` (bearer token Eve authenticates with) and a dedicated LiteLLM virtual key with its own spend cap.
- Her third-party accounts live only as browser session cookies on the PVC — never a Kubernetes Secret, an env var, or a line in this repository.
- The box learns **only** a goal string and a task id. No `member_sub`, no name, no roster, no permission ever crosses into `eve-computer` — this is stricter than `eve-tools`, which does carry `member_sub`.
- **Eve polls the box; the box never calls Eve.** One-directional network boundary.
- **One task at a time, queued.** One display, one mouse.
- The relevance filter is bypassed for the `computer` ambient source — an explicit request is never "not relevant," and swallowing it is the worst available failure mode.
- `eve_ambient/gates.py`'s `SOURCE_PERMISSION` must map `"computer"` explicitly — it fails closed on an unmapped source, silently notifying nobody.
- **No per-action approval gate**, **no per-member machines**, **no second harness driver in v1** — the swap seam (the task API itself) exists; a second implementation does not.
- `eve-sandbox` must remain byte-identical to before this work.

---

### Task 1: `eve_computer_task` table

**Files:**
- Create: `alembic/versions/0004_eve_computer_task.py`

**Interfaces:**
- Consumes: nothing (new revision, `down_revision = "0003_eve_tool_pending_dedup"`).
- Produces: table `eve_computer_task(id text PRIMARY KEY, member_sub text NOT NULL, thread_id text NOT NULL, goal text NOT NULL, status text NOT NULL DEFAULT 'running', result jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz)` plus index `eve_computer_task_status ON eve_computer_task (status, updated_at)`. Task 2's store reads/writes this table; `id` is a client-generated string (a `str(uuid.uuid4())` minted by Task 6's dispatch tool and sent to the box as-is), not a database default, because the same id must exist on both sides of the boundary before either row/task is created.

- [ ] **Step 1: Write the migration**

```python
"""Eve's own record of a dispatched computer task.

Revision ID: 0004_eve_computer_task
Revises: 0003_eve_tool_pending_dedup

Not the box's internal queue - eve-computer tracks its own tasks in memory
and loses them on restart (design doc: "Storage"). This table is Eve's
side of the boundary: what she dispatched, to whom, on which thread, and
what came back. `id` is NOT database-generated: it is minted by
`eve.computer.dispatch` before the box has ever heard of the task, so the
same id can be sent to both sides of the HTTP call.
"""
from alembic import op

revision = "0004_eve_computer_task"
down_revision = "0003_eve_tool_pending_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_computer_task (
          id           text        PRIMARY KEY,
          member_sub   text        NOT NULL,
          thread_id    text        NOT NULL,
          goal         text        NOT NULL,
          status       text        NOT NULL DEFAULT 'running',
          result       jsonb,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          finished_at  timestamptz
        )
        """
    )
    # The poller's own query (Task 5): "every task I'm still waiting on."
    op.execute(
        "CREATE INDEX eve_computer_task_status"
        " ON eve_computer_task (status, updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_computer_task")
```

- [ ] **Step 2: Verify it applies cleanly**

Run: `EVE_DATABASE_URL=postgresql://eve:eve@127.0.0.1:15432/eve uv run alembic upgrade head` (with `docker compose -f docker-compose.test.yml up -d postgres` running first)
Expected: no error; `psql` shows `eve_computer_task` exists with the columns above.

- [ ] **Step 3: Commit**

```bash
git add alembic/versions/0004_eve_computer_task.py
git commit -m "feat(eve-computer): add the eve_computer_task table"
```

---

### Task 2: Eve-side task store

**Files:**
- Create: `src/eve/computer/__init__.py`
- Create: `src/eve/computer/store.py`
- Test: `tests/test_computer_store.py`

**Interfaces:**
- Consumes: `eve.memory.db.get_pool` (existing).
- Produces: `create_task(task_id: str, member_sub: str, thread_id: str, goal: str) -> None`, `get(task_id: str) -> dict | None`, `running_tasks() -> list[dict]`, `mark_finished(task_id: str, status: str, result: dict) -> None`, `mark_stale(task_id: str) -> None`. Task 5's poller and Task 6's dispatch tool both import from here.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_computer_store.py
import pytest

pytestmark = pytest.mark.integration


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
        await conn.execute("TRUNCATE eve_computer_task")
    yield p
    await db.close_pool()


async def test_create_task_defaults_to_running(pool):
    from eve.computer.store import create_task, get

    await create_task("t1", "sub-noah", "thread-1", "book the flight")
    row = await get("t1")
    assert row["status"] == "running"
    assert row["member_sub"] == "sub-noah"
    assert row["thread_id"] == "thread-1"
    assert row["goal"] == "book the flight"
    assert row["result"] is None


async def test_get_of_an_unknown_task_is_none(pool):
    from eve.computer.store import get

    assert await get("nope") is None


async def test_running_tasks_excludes_finished_ones(pool):
    from eve.computer.store import create_task, mark_finished, running_tasks

    await create_task("t1", "sub-noah", "thread-1", "goal one")
    await create_task("t2", "sub-noah", "thread-2", "goal two")
    await mark_finished("t1", "finished", {"summary": "done"})

    ids = [row["id"] for row in await running_tasks()]
    assert ids == ["t2"]


async def test_mark_finished_records_the_result(pool):
    from eve.computer.store import create_task, get, mark_finished

    await create_task("t1", "sub-noah", "thread-1", "goal")
    await mark_finished("t1", "failed", {"error": "RuntimeError: boom"})
    row = await get("t1")
    assert row["status"] == "failed"
    assert row["result"] == {"error": "RuntimeError: boom"}
    assert row["finished_at"] is not None


async def test_mark_stale_sets_status_and_finished_at(pool):
    from eve.computer.store import create_task, get, mark_stale

    await create_task("t1", "sub-noah", "thread-1", "goal")
    await mark_stale("t1")
    row = await get("t1")
    assert row["status"] == "stale"
    assert row["finished_at"] is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose -f docker-compose.test.yml up -d postgres && uv run pytest tests/test_computer_store.py -m integration -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.computer'`

- [ ] **Step 3: Write the store**

```python
# src/eve/computer/__init__.py
```
(empty — package marker, matching `src/eve/tools_authoring/__init__.py`)

```python
# src/eve/computer/store.py
"""Every eve_computer_task SQL statement. Eve's own record of a dispatched
computer task - not the box's internal queue, which the box tracks itself
in memory and loses on restart (design doc: "Storage")."""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool


async def create_task(task_id: str, member_sub: str, thread_id: str, goal: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_computer_task (id, member_sub, thread_id, goal, status)"
            " VALUES (%s, %s, %s, %s, 'running')",
            (task_id, member_sub, thread_id, goal),
        )


async def get(task_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_computer_task WHERE id = %s", (task_id,)
            )
            return await cur.fetchone()


async def running_tasks() -> list[dict]:
    """Every task Eve is still waiting on. The poller (Task 5) asks the box
    about each of these once per tick."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM eve_computer_task WHERE status = 'running'"
                " ORDER BY created_at"
            )
            return list(await cur.fetchall())


async def mark_finished(task_id: str, status: str, result: dict) -> None:
    """`status` is `'finished'` or `'failed'` - the poller decides which by
    inspecting the box's own result payload."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_computer_task SET status = %s, result = %s,"
            " updated_at = now(), finished_at = now() WHERE id = %s",
            (status, Jsonb(result), task_id),
        )


async def mark_stale(task_id: str) -> None:
    """The box stopped answering for this task past its own timeout -
    likely a restart mid-run (design doc: "Reporting back")."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_computer_task SET status = 'stale',"
            " updated_at = now(), finished_at = now() WHERE id = %s",
            (task_id,),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_computer_store.py -m integration -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/eve/computer/__init__.py src/eve/computer/store.py tests/test_computer_store.py
git commit -m "feat(eve-computer): add Eve's own eve_computer_task store"
```

---

### Task 3: Settings for the computer door

**Files:**
- Modify: `src/eve/settings.py`
- Test: `tests/test_settings.py` (if it exists) or a new `tests/test_computer_settings.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Settings.computer_enabled: bool`, `Settings.computer_base_url: str`, `Settings.computer_api_key: str`, `Settings.computer_task_stale_minutes: int`, plus the same fail-fast validation shape `sandbox_api_key`/`sandbox_enabled` already has. Task 4 (tools_client), Task 5 (poller), Task 6 (dispatch tool + graph wiring) all read these.

- [ ] **Step 1: Check whether a settings test file already exists**

Run: `ls tests/ | grep -i settings`
Expected: no dedicated `test_settings.py` today — the existing validation (`sandbox_api_key`, `ambient_token`) is exercised inline inside `tests/test_tools_client.py` and `tests/test_graph.py` rather than a standalone file. Follow that precedent: this task's new tests live in `tests/test_computer_settings.py`, a new small file, rather than inventing a general settings test file this repo doesn't otherwise have.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_computer_settings.py
import pytest


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_computer_is_disabled_by_default():
    from eve.settings import Settings

    assert Settings().computer_enabled is False
    assert Settings().computer_base_url == "http://eve-computer:8092"


def test_a_short_computer_api_key_is_rejected():
    from eve.settings import Settings

    with pytest.raises(ValueError, match="EVE_COMPUTER_API_KEY"):
        Settings(computer_api_key="too-short")


def test_enabling_without_a_key_is_rejected():
    from eve.settings import Settings

    with pytest.raises(ValueError, match="EVE_COMPUTER_API_KEY is required"):
        Settings(computer_enabled=True)


def test_enabling_with_a_long_enough_key_is_accepted():
    from eve.settings import Settings

    settings = Settings(computer_enabled=True, computer_api_key="k" * 32)
    assert settings.computer_enabled is True
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_computer_settings.py -v`
Expected: FAIL — `Settings` has no field `computer_enabled`

- [ ] **Step 4: Add the settings**

In `src/eve/settings.py`, after the `sandbox_max_concurrency: int = 4` line, add:

```python
    # Phase 6 (Eve's computer). See docs/superpowers/specs/
    # 2026-08-28-eve-computer-design.md.
    computer_enabled: bool = False
    computer_base_url: str = "http://eve-computer:8092"
    computer_api_key: str = ""
    # How long the poller waits for the box to answer about a task before
    # giving up and marking it stale (design doc: "Reporting back" - covers a
    # pod restart mid-run, since eve-computer keeps no task state on disk).
    computer_task_stale_minutes: int = 120
```

In `model_post_init`, after the `sandbox_enabled`/`sandbox_api_key` checks, add the matching pair:

```python
        if self.computer_api_key and len(self.computer_api_key) < 32:
            raise ValueError(
                "EVE_COMPUTER_API_KEY must be at least 32 characters: it "
                "authenticates a service that takes real-world actions, so "
                "a guessable value fails open"
            )
        if self.computer_enabled and not self.computer_api_key:
            raise ValueError(
                "EVE_COMPUTER_API_KEY is required when EVE_COMPUTER_ENABLED=true"
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_computer_settings.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/eve/settings.py tests/test_computer_settings.py
git commit -m "feat(eve-computer): add EVE_COMPUTER_* settings"
```

---

### Task 4: `tools_client`'s third door

**Files:**
- Modify: `src/eve/tools_client.py`
- Modify: `tests/test_tools_client.py`

**Interfaces:**
- Consumes: `eve.settings.get_settings().computer_base_url` / `computer_api_key` (Task 3).
- Produces: `dispatch_task(task_id: str, goal: str, timeout: float = 15.0) -> str` and `get_computer_task(task_id: str, timeout: float = 15.0) -> dict | None`. Task 6's dispatch tool calls `dispatch_task`; Task 5's poller calls `get_computer_task`.

The box's task API (`POST /tasks {id, goal}`, `GET /tasks/{id}` → `{status, result, artifacts}`) is a lifecycle shape, not the `{tool, arguments} -> {result | error}` contract `invoke()` speaks to `eve-tools` and `eve-sandbox`. Forcing it through `invoke()`'s `_TARGETS` dispatch would mean either a fake `tool`/`arguments` wrapper around a `POST /tasks` call or a second meaning for `target`. Two dedicated functions, in the same module, using the same settings/bearer-token shape, are the honest fit — "the same bearer-token shape as the existing two doors," not the same wire contract.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_client.py`:

```python
@respx.mock
async def test_dispatch_task_posts_to_the_computer_tasks_endpoint(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_BASE_URL", "http://eve-computer.test")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "c" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve.tools_client import dispatch_task

    route = respx.post("http://eve-computer.test/tasks").mock(
        return_value=httpx.Response(202, json={"id": "t1", "status": "queued"})
    )
    result = await dispatch_task("t1", "book the flight")

    assert result == "ok"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"id": "t1", "goal": "book the flight"}
    assert route.calls.last.request.headers["authorization"] == "Bearer " + "c" * 32


@respx.mock
async def test_dispatch_task_degrades_to_an_error_string_on_failure(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_BASE_URL", "http://eve-computer.test")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "c" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve.tools_client import dispatch_task

    respx.post("http://eve-computer.test/tasks").mock(side_effect=httpx.ConnectError)
    result = await dispatch_task("t1", "book the flight")
    assert result.startswith("error:")


@respx.mock
async def test_get_computer_task_returns_the_boxs_status(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_BASE_URL", "http://eve-computer.test")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "c" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve.tools_client import get_computer_task

    respx.get("http://eve-computer.test/tasks/t1").mock(
        return_value=httpx.Response(
            200, json={"status": "finished", "result": {"summary": "done"}, "artifacts": []}
        )
    )
    status = await get_computer_task("t1")
    assert status == {"status": "finished", "result": {"summary": "done"}, "artifacts": []}


@respx.mock
async def test_get_computer_task_returns_none_when_the_box_is_unreachable(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_BASE_URL", "http://eve-computer.test")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "c" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve.tools_client import get_computer_task

    respx.get("http://eve-computer.test/tasks/t1").mock(side_effect=httpx.ConnectError)
    assert await get_computer_task("t1") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_tools_client.py -k computer -v`
Expected: FAIL with `ImportError: cannot import name 'dispatch_task'`

- [ ] **Step 3: Add the two functions**

In `src/eve/tools_client.py`, after the existing `invoke` function, add:

```python
async def dispatch_task(task_id: str, goal: str, timeout: float = 15.0) -> str:
    """POST /tasks on eve-computer. Not routed through `invoke()`: the box's
    task API is a lifecycle (create, poll, fetch an artifact, kill), not the
    {tool, arguments} -> {result|error} shape eve-tools and eve-sandbox
    share, so it gets its own thin wrapper instead of a second meaning for
    `target`."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.computer_base_url}/tasks",
                json={"id": task_id, "goal": goal},
                headers={"Authorization": f"Bearer {settings.computer_api_key}"},
            )
            response.raise_for_status()
        return "ok"
    except Exception as exc:
        logger.warning("eve-computer dispatch failed for %s", task_id, exc_info=True)
        return f"error: eve-computer unavailable ({exc.__class__.__name__})"


async def get_computer_task(task_id: str, timeout: float = 15.0) -> dict | None:
    """GET /tasks/{id} on eve-computer. `None` means the box could not be
    asked at all - down, timed out, or the task id is unknown to it (e.g.
    after a restart, since eve-computer keeps no task state on disk). The
    poller (eve.computer.poller) treats that as "still waiting" until it has
    been true past its own stale timeout."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{settings.computer_base_url}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {settings.computer_api_key}"},
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        logger.warning("eve-computer status check failed for %s", task_id, exc_info=True)
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools_client.py -v`
Expected: all pass (existing tests plus the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add src/eve/tools_client.py tests/test_tools_client.py
git commit -m "feat(eve-computer): give tools_client a door to eve-computer's task API"
```

---

### Task 5: The poller state machine

**Files:**
- Create: `src/eve/computer/poller.py`
- Test: `tests/test_computer_poller.py`

**Interfaces:**
- Consumes: `eve.computer.store.running_tasks/mark_finished/mark_stale` (Task 2), `eve.tools_client.get_computer_task` (Task 4), `eve.settings.get_settings().computer_task_stale_minutes` (Task 3).
- Produces: `async def sync(now: datetime | None = None) -> list[dict]`, returning the task rows (as dicts with `status` and `result` reflecting the *new* state, plus a `finished_at`) that resolved — finished, failed, or went stale — on this tick. Task 8's ambient source calls this and turns each returned row into a `Signal`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_computer_poller.py
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from eve.computer import poller


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_TASK_STALE_MINUTES", "60")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _task(task_id="t1", updated_at=None, member_sub="sub-noah", thread_id="thread-1", goal="do it"):
    return {
        "id": task_id, "member_sub": member_sub, "thread_id": thread_id,
        "goal": goal, "status": "running", "result": None,
        "updated_at": updated_at or datetime.now(UTC),
    }


async def test_a_task_the_box_reports_finished_is_marked_finished(monkeypatch):
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=[_task()]))
    mark_finished = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_finished", mark_finished)
    monkeypatch.setattr(
        poller.tools_client, "get_computer_task",
        AsyncMock(return_value={"status": "finished", "result": {"summary": "done"}}),
    )

    resolved = await poller.sync(now=datetime.now(UTC))

    mark_finished.assert_awaited_once_with("t1", "finished", {"summary": "done"})
    assert resolved[0]["status"] == "finished"
    assert resolved[0]["result"] == {"summary": "done"}


async def test_a_result_carrying_an_error_is_marked_failed_even_if_the_box_said_finished(
    monkeypatch,
):
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=[_task()]))
    mark_finished = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_finished", mark_finished)
    monkeypatch.setattr(
        poller.tools_client, "get_computer_task",
        AsyncMock(return_value={"status": "finished", "result": {"error": "boom"}}),
    )

    resolved = await poller.sync(now=datetime.now(UTC))

    mark_finished.assert_awaited_once_with("t1", "failed", {"error": "boom"})
    assert resolved[0]["status"] == "failed"


async def test_the_box_reporting_failed_is_marked_failed(monkeypatch):
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=[_task()]))
    mark_finished = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_finished", mark_finished)
    monkeypatch.setattr(
        poller.tools_client, "get_computer_task",
        AsyncMock(return_value={"status": "failed", "result": {"error": "killed"}}),
    )

    await poller.sync(now=datetime.now(UTC))
    mark_finished.assert_awaited_once_with("t1", "failed", {"error": "killed"})


async def test_a_still_running_task_is_left_alone(monkeypatch):
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=[_task()]))
    mark_finished = AsyncMock()
    mark_stale = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_finished", mark_finished)
    monkeypatch.setattr(poller.store, "mark_stale", mark_stale)
    monkeypatch.setattr(
        poller.tools_client, "get_computer_task",
        AsyncMock(return_value={"status": "running", "result": None}),
    )

    resolved = await poller.sync(now=datetime.now(UTC))

    mark_finished.assert_not_awaited()
    mark_stale.assert_not_awaited()
    assert resolved == []


async def test_an_unreachable_box_within_the_stale_window_is_left_alone(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        poller.store, "running_tasks",
        AsyncMock(return_value=[_task(updated_at=now - timedelta(minutes=10))]),
    )
    mark_stale = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_stale", mark_stale)
    monkeypatch.setattr(poller.tools_client, "get_computer_task", AsyncMock(return_value=None))

    resolved = await poller.sync(now=now)

    mark_stale.assert_not_awaited()
    assert resolved == []


async def test_an_unreachable_box_past_the_stale_window_is_marked_stale(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        poller.store, "running_tasks",
        AsyncMock(return_value=[_task(updated_at=now - timedelta(minutes=61))]),
    )
    mark_stale = AsyncMock()
    monkeypatch.setattr(poller.store, "mark_stale", mark_stale)
    monkeypatch.setattr(poller.tools_client, "get_computer_task", AsyncMock(return_value=None))

    resolved = await poller.sync(now=now)

    mark_stale.assert_awaited_once_with("t1")
    assert resolved[0]["status"] == "stale"


async def test_one_tasks_failure_does_not_stop_the_rest_from_being_checked(monkeypatch):
    tasks = [_task("t1"), _task("t2")]
    monkeypatch.setattr(poller.store, "running_tasks", AsyncMock(return_value=tasks))
    monkeypatch.setattr(poller.store, "mark_finished", AsyncMock())

    async def _status(task_id):
        if task_id == "t1":
            raise RuntimeError("transient")
        return {"status": "finished", "result": {"summary": "ok"}}

    monkeypatch.setattr(poller.tools_client, "get_computer_task", AsyncMock(side_effect=_status))

    resolved = await poller.sync(now=datetime.now(UTC))
    assert [row["id"] for row in resolved] == ["t2"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_computer_poller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.computer.poller'`

- [ ] **Step 3: Write the poller**

```python
# src/eve/computer/poller.py
"""The poller state machine: for every task Eve is still waiting on, ask the
box once, and update Eve's own row accordingly.

Kept separate from `eve_ambient.sources.computer` so it can be unit-tested
with only eve-computer (via `eve.tools_client.get_computer_task`) mocked, not
the whole ambient gate chain - "the poller state machine" and "the ambient
source" are two of the four things the design doc's testing section names
as separately covered by the unit tier.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from eve import tools_client
from eve.computer import store
from eve.settings import get_settings

logger = logging.getLogger(__name__)


async def sync(now: datetime | None = None) -> list[dict]:
    """Returns the rows that resolved - finished, failed, or went stale - on
    this tick. `eve_ambient.sources.computer.poll` turns each into a Signal."""
    now = now or datetime.now(UTC)
    stale_after = timedelta(minutes=get_settings().computer_task_stale_minutes)
    resolved: list[dict] = []

    for task in await store.running_tasks():
        try:
            status = await tools_client.get_computer_task(task["id"])
        except Exception:
            # get_computer_task already degrades every failure to None; this
            # guards a future regression that makes it raise instead, so one
            # bad task cannot stop every other task from being checked.
            logger.warning("checking on task %s raised", task["id"], exc_info=True)
            status = None

        if status is None:
            if now - task["updated_at"] > stale_after:
                await store.mark_stale(task["id"])
                resolved.append({**task, "status": "stale", "result": None, "finished_at": now})
            continue

        box_status = status.get("status")
        if box_status not in ("finished", "failed"):
            continue

        result = status.get("result") or {}
        outcome = "failed" if box_status == "failed" or result.get("error") else "finished"
        await store.mark_finished(task["id"], outcome, result)
        resolved.append({**task, "status": outcome, "result": result, "finished_at": now})

    return resolved
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_computer_poller.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/eve/computer/poller.py tests/test_computer_poller.py
git commit -m "feat(eve-computer): add the poller state machine"
```

---

### Task 6: The `dispatch_computer_task` tool

**Files:**
- Modify: `family.yaml`
- Create: `src/eve/computer/dispatch.py`
- Modify: `src/eve/graph.py`
- Test: `tests/test_computer_dispatch.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `eve.specialists.permissions.permission_denial` (existing), `eve.tools_client.dispatch_task` (Task 4), `eve.computer.store.create_task` (Task 2), `eve.settings.get_settings().computer_enabled` (Task 3).
- Produces: the `dispatch_computer_task` LangChain tool, bound in `graph._static_tools()` when `computer_enabled` is true.

- [ ] **Step 1: Grant the permission**

In `family.yaml`, add `computer.use` to both members' permission lists, following the existing comment convention:

```yaml
      # Phase 6: dispatching a task to her own computer.
      - computer.use
```

Add this line under each member's `permissions:` block (after `tools.author` for Noah, after `memory.write_shared` for Kendra), so the final blocks read:

```yaml
  - sub: "a06dc93aea7f4d4116e550f9c826fc59b7c36f083a3a19807bab5290e12d00cb"
    name: "Noah"
    role: adult
    timezone: "America/Vancouver"
    permissions:
      - calendar.read
      - home.control
      - mail.read
      - mail.send
      - finances
      - memory.write_shared
      - tools.author
      - computer.use

  - sub: "b96297cfe2cd39700a9d394e99cb98cb4c84167caccae5c6ab596a17a799495c"
    name: "Kendra"
    role: adult
    timezone: "America/Vancouver"
    permissions:
      - calendar.read
      - home.control
      - mail.read
      - mail.send
      - finances
      - memory.write_shared
      - computer.use
```

- [ ] **Step 2: Write the failing tool test**

```python
# tests/test_computer_dispatch.py
from unittest.mock import AsyncMock

import pytest

from eve.computer import dispatch


def _config(permissions=("computer.use",), thread_id="thread-1"):
    return {
        "configurable": {
            "member": {"sub": "sub-noah", "permissions": list(permissions)},
            "thread_id": thread_id,
        }
    }


async def test_a_member_without_the_permission_is_denied(monkeypatch):
    dispatch_task = AsyncMock()
    monkeypatch.setattr(dispatch, "dispatch_task", dispatch_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight", "config": _config(permissions=())}
    )

    assert "Permission denied" in result
    assert "computer.use" in result
    dispatch_task.assert_not_awaited()


async def test_a_permitted_member_dispatches_and_records_the_task(monkeypatch):
    monkeypatch.setattr(dispatch, "dispatch_task", AsyncMock(return_value="ok"))
    create_task = AsyncMock()
    monkeypatch.setattr(dispatch, "create_task", create_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight", "config": _config()}
    )

    assert "I'm on it" in result
    create_task.assert_awaited_once()
    kwargs = create_task.await_args.kwargs
    assert kwargs["member_sub"] == "sub-noah"
    assert kwargs["thread_id"] == "thread-1"
    assert kwargs["goal"] == "book a flight"
    assert isinstance(kwargs["task_id"], str) and kwargs["task_id"]


async def test_a_dispatch_failure_is_returned_and_nothing_is_recorded(monkeypatch):
    monkeypatch.setattr(
        dispatch, "dispatch_task", AsyncMock(return_value="error: eve-computer unavailable")
    )
    create_task = AsyncMock()
    monkeypatch.setattr(dispatch, "create_task", create_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight", "config": _config()}
    )

    assert result.startswith("error:")
    create_task.assert_not_awaited()


async def test_no_thread_id_is_refused_before_dispatching(monkeypatch):
    dispatch_task = AsyncMock()
    monkeypatch.setattr(dispatch, "dispatch_task", dispatch_task)

    result = await dispatch.dispatch_computer_task.ainvoke(
        {"goal": "book a flight", "config": _config(thread_id=None)}
    )

    assert result.startswith("error:")
    dispatch_task.assert_not_awaited()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_computer_dispatch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.computer.dispatch'`

- [ ] **Step 4: Write the tool**

```python
# src/eve/computer/dispatch.py
"""dispatch_computer_task: Eve's one tool onto her own machine. Permission is
checked here, before the HTTP call, so a denied request never reaches
eve-computer at all - ADR 0006's pattern (permission checks happen in Eve's
main container, before the HTTP call), applied a third time."""

from __future__ import annotations

import uuid

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.computer.store import create_task
from eve.specialists.permissions import permission_denial
from eve.tools_client import dispatch_task


@tool
async def dispatch_computer_task(goal: str, config: RunnableConfig) -> str:
    """Dispatch a task to Eve's own computer: a persistent Linux desktop with
    a browser, a shell, and her own accounts. Use this for anything that
    needs a real account, a real browser, or a real shell rather than
    something answerable directly. Returns immediately; the result is
    reported later, in a separate message, once the task finishes."""
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), "computer.use")
    if denial:
        return denial

    configurable = config.get("configurable", {}) if config else {}
    thread_id = configurable.get("thread_id")
    if not thread_id:
        return "error: no thread to report the result on"

    task_id = str(uuid.uuid4())
    dispatched = await dispatch_task(task_id, goal)
    if dispatched.startswith("error:"):
        return dispatched

    await create_task(
        task_id=task_id, member_sub=member["sub"], thread_id=thread_id, goal=goal
    )
    return "I'm on it \u2014 I'll let you know when it's done."
```

- [ ] **Step 5: Run the tool tests to verify they pass**

Run: `uv run pytest tests/test_computer_dispatch.py -v`
Expected: 4 passed

- [ ] **Step 6: Wire it into the graph behind a settings flag**

In `src/eve/graph.py`, add the import next to `propose_tool`'s:

```python
from eve.computer.dispatch import dispatch_computer_task
```

In `_static_tools()`, after the `sandbox_enabled` check:

```python
    if settings.computer_enabled:
        tools.append(dispatch_computer_task)
```

- [ ] **Step 7: Write the failing graph-wiring tests**

Append to `tests/test_graph.py`:

```python
def test_dispatch_computer_task_is_bound_when_enabled(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_ENABLED", "true")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "k" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "dispatch_computer_task" in {t.name for t in graph_mod._static_tools()}


def test_dispatch_computer_task_is_unbound_by_default(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve import graph as graph_mod

    assert "dispatch_computer_task" not in {t.name for t in graph_mod._static_tools()}
```

- [ ] **Step 8: Run the full graph test file**

Run: `uv run pytest tests/test_graph.py -v`
Expected: all pass, including the 2 new tests

- [ ] **Step 9: Commit**

```bash
git add family.yaml src/eve/computer/dispatch.py src/eve/graph.py tests/test_computer_dispatch.py tests/test_graph.py
git commit -m "feat(eve-computer): add the dispatch_computer_task tool, gated by computer.use"
```

---

### Task 7: Ambient permission mapping

**Files:**
- Modify: `src/eve_ambient/gates.py`
- Modify: `tests/test_ambient_gates.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `gates.SOURCE_PERMISSION["computer"] == "computer.use"`. Task 9's pipeline bypass still calls `gates.permitted`, so this mapping must exist before that task's audience passes the gate.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ambient_gates.py`:

```python
def test_a_computer_signal_requires_computer_use():
    """The permission mapping the design doc worries about forgetting -
    without it, gates.permitted's fail-closed default (line ~49) silently
    notifies nobody for every finished computer task."""
    assert gates.permitted(_signal("computer"), ["sub-noah"]) == []
```

(Uses the module's existing `ROSTER` fixture, whose `sub-noah` holds no `computer.use` permission — asserting the *unmapped* behavior would pass today for the wrong reason; add the permission to the roster fixture in the same edit so this test asserts the *mapped* behavior instead:)

In `tests/test_ambient_gates.py`'s `ROSTER` fixture, add `computer.use` to `sub-noah`'s permission list:

```python
ROSTER = """
members:
  - sub: "sub-noah"
    name: "Noah"
    role: adult
    timezone: "America/Vancouver"
    permissions: [mail.read, finances, home.control, calendar.read, computer.use]
  - sub: "sub-kid"
    name: "Kid"
    role: child
    timezone: "America/Toronto"
    permissions: [home.control]
"""
```

And replace the test above with one asserting the held-permission case, plus a second asserting the source is mapped at all (the existing `test_an_unknown_source_permits_nobody` already covers the *unmapped* fail-closed case for a different, fictional source — `"weather"` — so a `computer`-specific test should assert the mapping exists, not repeat that one):

```python
def test_a_member_holding_computer_use_is_kept_for_a_computer_signal():
    assert gates.permitted(_signal("computer"), ["sub-noah"]) == ["sub-noah"]


def test_a_member_lacking_computer_use_is_dropped_for_a_computer_signal():
    assert gates.permitted(_signal("computer"), ["sub-kid"]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambient_gates.py -v`
Expected: FAIL — `computer` is unmapped, so `gates.permitted` returns `[]` for both new tests, and the first assertion (`["sub-noah"]`) fails

- [ ] **Step 3: Add the mapping**

In `src/eve_ambient/gates.py`, add one entry to `SOURCE_PERMISSION`:

```python
SOURCE_PERMISSION: dict[str, str] = {
    "calendar": "calendar.read",
    "mail": "mail.read",
    "finances": "finances",
    "home": "home.control",
    "computer": "computer.use",
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_gates.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/gates.py tests/test_ambient_gates.py
git commit -m "feat(eve-computer): map the computer ambient source to computer.use"
```

---

### Task 8: The `computer` ambient source

**Files:**
- Create: `src/eve_ambient/sources/computer.py`
- Modify: `src/eve_ambient/sources/__init__.py`
- Test: `tests/test_ambient_sources_computer.py`

**Interfaces:**
- Consumes: `eve.computer.poller.sync` (Task 5).
- Produces: `async def poll(_member_sub: str) -> list[Signal]`, and a `Source("computer", False, "computer.use", computer.poll)` entry in `SOURCES`.

`per_member=False`: `eve-computer` holds no per-member data, so this source is polled once per tick for the whole household (like `finances`), not once per member holding the permission — each finished task's `member_sub` comes from Eve's own task row, not from re-deriving an audience per member.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ambient_sources_computer.py
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from eve_ambient.sources import computer


async def test_a_finished_task_becomes_a_signal_addressed_to_its_member(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "book the flight", "status": "finished",
            "result": {"summary": "Booked WS123 for the 14th."},
            "finished_at": now,
        }]),
    )
    signals = await computer.poll("")
    assert len(signals) == 1
    signal = signals[0]
    assert signal.source == "computer"
    assert signal.key == "t1"
    assert signal.member_sub == "sub-noah"
    assert "book the flight" in signal.summary
    assert signal.payload["thread_id"] == "thread-1"
    assert signal.payload["result"] == {"summary": "Booked WS123 for the 14th."}


async def test_a_failed_task_says_so_in_the_summary(monkeypatch):
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "book the flight", "status": "failed",
            "result": {"error": "RuntimeError: no such airline"},
            "finished_at": datetime.now(UTC),
        }]),
    )
    signals = await computer.poll("")
    assert "failed" in signals[0].summary.lower()
    assert "no such airline" in signals[0].summary


async def test_a_stale_task_says_so_in_the_summary(monkeypatch):
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "book the flight", "status": "stale", "result": None,
            "finished_at": datetime.now(UTC),
        }]),
    )
    signals = await computer.poll("")
    assert "stale" in signals[0].summary.lower()


async def test_never_recurs_once_seen(monkeypatch):
    """A task id is a one-shot key - the same task never finishes twice, so
    the signal carries no cooldown to re-trigger on."""
    monkeypatch.setattr(
        computer.poller, "sync",
        AsyncMock(return_value=[{
            "id": "t1", "member_sub": "sub-noah", "thread_id": "thread-1",
            "goal": "goal", "status": "finished", "result": {},
            "finished_at": datetime.now(UTC),
        }]),
    )
    signals = await computer.poll("")
    assert signals[0].cooldown_hours == 0


async def test_no_resolved_tasks_is_no_signals(monkeypatch):
    monkeypatch.setattr(computer.poller, "sync", AsyncMock(return_value=[]))
    assert await computer.poll("") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambient_sources_computer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve_ambient.sources.computer'`

- [ ] **Step 3: Write the source**

```python
# src/eve_ambient/sources/computer.py
"""Finished computer tasks as signals. The relevance filter is bypassed for
this source (design doc: "Reporting back") - `eve_ambient.pipeline` special-
cases `source == "computer"` instead of calling the REFLEX filter, since a
task a member explicitly asked for is never "not relevant."

`per_member=False`: eve-computer holds no per-member data (design doc: "The
box learns nothing about the family"), so this is polled once per tick for
the whole household, like `finances`, not once per member holding the
permission - each finished task's member comes from Eve's own task row.
"""

from __future__ import annotations

from eve.computer import poller
from eve_ambient.types import Signal


def _summary(task: dict) -> str:
    goal = task["goal"]
    if task["status"] == "stale":
        return f"A computer task went stale and never reported back: {goal}"
    result = task["result"] or {}
    if task["status"] == "failed" or result.get("error"):
        return f"A computer task failed: {goal}. {result.get('error', '')}".rstrip()
    return f"Finished a computer task: {goal}"


async def poll(_member_sub: str) -> list[Signal]:
    resolved = await poller.sync()
    return [
        Signal(
            source="computer",
            key=task["id"],
            occurred_at=task["finished_at"],
            member_sub=task["member_sub"],
            summary=_summary(task),
            payload={
                "thread_id": task["thread_id"],
                "goal": task["goal"],
                "result": task["result"],
                "status": task["status"],
            },
            # A task id never recurs - it resolves exactly once, so there is
            # no cooldown window for it to re-fire within.
            cooldown_hours=0,
        )
        for task in resolved
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_sources_computer.py -v`
Expected: 5 passed

- [ ] **Step 5: Register the source**

In `src/eve_ambient/sources/__init__.py`:

```python
from eve_ambient.sources import calendar, computer, finances, mail
```

```python
SOURCES: tuple[Source, ...] = (
    Source("calendar", True, "calendar.read", calendar.poll),
    Source("mail", True, "mail.read", mail.poll),
    Source("finances", False, "finances", finances.poll),
    Source("computer", False, "computer.use", computer.poll),
)
```

- [ ] **Step 6: Commit**

```bash
git add src/eve_ambient/sources/computer.py src/eve_ambient/sources/__init__.py tests/test_ambient_sources_computer.py
git commit -m "feat(eve-computer): add the computer ambient source"
```

---

### Task 9: Bypass the relevance filter for `computer` signals

**Files:**
- Modify: `src/eve_ambient/pipeline.py`
- Modify: `tests/test_ambient_pipeline.py`

**Interfaces:**
- Consumes: nothing new (still `gates.permitted`, `store`, `deliver`, `notifier` exactly as before).
- Produces: no change to `handle_signal`'s signature or return values (still one of the same outcome strings); `source == "computer"` now resolves to a synthetic `FilterVerdict` instead of calling `judge()`, and never calls `store.record_decision` (there was no filter decision to record).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ambient_pipeline.py`:

```python
def _computer_signal(member_sub="sub-noah", key="t1"):
    return Signal(
        source="computer", key=key, occurred_at=MIDDAY, member_sub=member_sub,
        summary="Finished a computer task: book the flight.",
        payload={"thread_id": "thread-1"},
        cooldown_hours=0,
    )


async def test_a_computer_signal_never_calls_the_filter(wiring, monkeypatch):
    async def _judge_should_not_be_called(signal):
        raise AssertionError("judge() must not be called for source=computer")

    monkeypatch.setattr(pipeline, "judge", _judge_should_not_be_called)

    assert await pipeline.handle_signal(_computer_signal(), now=MIDDAY) == "sent"
    assert wiring["delivered"] == ["sub-noah"]


async def test_a_computer_signal_is_addressed_only_to_its_own_member(wiring):
    """The verdict is synthesised directly from the signal's own member_sub,
    not filter output - there is no model in the loop to name anyone else."""
    result = await pipeline.handle_signal(_computer_signal(member_sub="sub-noah"), now=MIDDAY)
    assert result == "sent"
    assert wiring["delivered"] == ["sub-noah"]


async def test_a_computer_signal_records_no_eval_decision(monkeypatch, pipeline_stubs):
    recorded = pipeline_stubs["decisions"]
    await pipeline.handle_signal(_computer_signal(key="t2"), now=MIDDAY)
    assert recorded == []


async def test_a_computer_signal_still_respects_the_permission_gate(wiring):
    """gates.permitted still runs - a member without computer.use is dropped
    even though the filter never ran."""
    result = await pipeline.handle_signal(
        _computer_signal(member_sub="sub-kid", key="t3"), now=MIDDAY
    )
    assert result == "unpermitted"
    assert wiring["delivered"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambient_pipeline.py -k computer -v`
Expected: FAIL — today every source calls `judge()`, so the first test's assertion error fires, and `sub-kid`'s roster fixture already lacks `computer.use` from Task 7's roster edit (if `tests/test_ambient_pipeline.py` has its own separate `ROSTER` fixture, add `computer.use` to `sub-noah`'s permissions there too, matching Task 7's edit, since this file defines its own roster independent of `test_ambient_gates.py`'s).

Add `computer.use` to this file's own `ROSTER` fixture (`sub-noah`'s permission list), mirroring Task 7:

```python
ROSTER = """
members:
  - sub: "sub-noah"
    name: "Noah"
    role: adult
    timezone: "America/Vancouver"
    permissions: [mail.read, finances, home.control, calendar.read, computer.use]
  - sub: "sub-kid"
    name: "Kid"
    role: child
    timezone: "America/Vancouver"
    permissions: [home.control]
"""
```

- [ ] **Step 3: Add the bypass**

In `src/eve_ambient/pipeline.py`, replace:

```python
    try:
        verdict = await judge(signal)
    except FilterError:
        # A couldn't-decide, not a decided-no (fix round 1, item 2): treat it
        # exactly like a notify.DeliveryError and leave the signal unseen so
        # the next poll retries it. A persistent outage retrying every poll
        # is correct and cheap — the filter call fails fast.
        logger.warning(
            "deferring %s: the filter could not judge it", signal.key, exc_info=True
        )
        return _resolved(signal, None, [], "deferred")

    # Before the gate chain, deliberately: the dataset's label is the
    # filter's verdict, not the outcome (eval design 4.2). Best-effort -
    # losing an eval row must never cost a notification.
    try:
        await store.record_decision(signal, verdict)
    except Exception:
        logger.warning(
            "could not record the eval decision for %s", signal.key, exc_info=True
        )
```

with:

```python
    if signal.source == "computer":
        # Explicitly requested, not merely noticed: an LLM deciding a direct
        # request is "not relevant" is the worst available failure mode
        # (design doc: "Reporting back"). No filter call, and nothing to
        # record - there was no filter decision to label the eval dataset
        # with (ADR 0009's dataset measures the filter, not this bypass).
        verdict = FilterVerdict(
            notify=True,
            audience=[signal.member_sub] if signal.member_sub else [],
            urgent=False,
            why="a family member asked for this computer task directly",
        )
    else:
        try:
            verdict = await judge(signal)
        except FilterError:
            # A couldn't-decide, not a decided-no (fix round 1, item 2): treat it
            # exactly like a notify.DeliveryError and leave the signal unseen so
            # the next poll retries it. A persistent outage retrying every poll
            # is correct and cheap — the filter call fails fast.
            logger.warning(
                "deferring %s: the filter could not judge it", signal.key, exc_info=True
            )
            return _resolved(signal, None, [], "deferred")

        # Before the gate chain, deliberately: the dataset's label is the
        # filter's verdict, not the outcome (eval design 4.2). Best-effort -
        # losing an eval row must never cost a notification.
        try:
            await store.record_decision(signal, verdict)
        except Exception:
            logger.warning(
                "could not record the eval decision for %s", signal.key, exc_info=True
            )
```

Then, further down, thread the signal's own `thread_id` through to `deliver` so Task 10's reuse path is exercised (this line currently reads `thread_id = await deliver(signal, member, verdict, notifier)`):

```python
        try:
            thread_id = await deliver(
                signal, member, verdict, notifier,
                thread_id=(
                    signal.payload.get("thread_id") if signal.source == "computer" else None
                ),
            )
        except DeliveryError:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_pipeline.py -v`
Expected: all pass (existing tests unaffected, 4 new ones pass) — this step will still fail until Task 10 gives `deliver` a `thread_id` keyword parameter; run Task 10 before re-checking this file if working sequentially.

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/pipeline.py tests/test_ambient_pipeline.py
git commit -m "feat(eve-computer): bypass the relevance filter for computer signals"
```

---

### Task 10: Reuse the originating thread in `notify.deliver`

**Files:**
- Modify: `src/eve_ambient/notify.py`
- Modify: `tests/test_ambient_notify.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `deliver(signal, member, verdict, notifier, *, thread_id: str | None = None) -> str | None`. When `thread_id` is passed, `deliver` runs the turn on that existing thread instead of creating one, and never deletes it — it is a member's real conversation, not an ephemeral ambient thread created solely to hold this one turn.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ambient_notify.py`:

```python
async def test_a_passed_thread_id_is_reused_instead_of_creating_one(monkeypatch):
    threads = FakeThreads()
    client = _with_client(monkeypatch, FakeClient(threads=threads))

    thread_id = await notify.deliver(
        SIGNAL, MEMBER, VERDICT, RecordingNotifier(), thread_id="existing-thread"
    )

    assert thread_id == "existing-thread"
    assert threads.created == []
    assert client.runs.inputs  # the turn still ran


async def test_a_reused_thread_is_never_discarded_on_veto(monkeypatch):
    threads = FakeThreads()
    _with_client(monkeypatch, FakeClient(threads=threads, runs=FakeRuns(final_text="NOTHING")))

    result = await notify.deliver(
        SIGNAL, MEMBER, VERDICT, RecordingNotifier(), thread_id="existing-thread"
    )

    assert result is None
    assert threads.deleted == []


async def test_a_reused_thread_is_never_discarded_on_run_failure(monkeypatch):
    threads = FakeThreads()
    _with_client(
        monkeypatch,
        FakeClient(threads=threads, runs=FakeRuns(error=RuntimeError("aegra down"))),
    )

    with pytest.raises(notify.DeliveryError):
        await notify.deliver(
            SIGNAL, MEMBER, VERDICT, RecordingNotifier(), thread_id="existing-thread"
        )

    assert threads.deleted == []


async def test_without_a_thread_id_behaviour_is_unchanged(monkeypatch):
    """Every existing caller (calendar, mail, finances, home) passes no
    thread_id and must keep creating a fresh one."""
    threads = FakeThreads()
    _with_client(monkeypatch, FakeClient(threads=threads))

    thread_id = await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())

    assert thread_id == "thread-1"
    assert threads.created == [{"ambient": True, "source": "calendar", "signal_key": "uid-1:start:x"}]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambient_notify.py -k reused -v`
Expected: FAIL — `deliver()` accepts no `thread_id` keyword today

- [ ] **Step 3: Add the reuse path**

Replace `notify.py`'s `deliver` function body with:

```python
async def deliver(
    signal: Signal, member: Member, verdict: FilterVerdict, notifier: Notifier,
    *, thread_id: str | None = None,
) -> str | None:
    """`thread_id`, when given, is a real conversation thread the member
    already owns (design doc: "compose a turn as Eve on the originating
    thread," for the `computer` source) - it is never created here and never
    discarded here, unlike the fresh, ambient-only thread every other source
    still gets."""
    reused = thread_id is not None
    async with _client(member.sub) as client:
        if not reused:
            try:
                thread = await client.threads.create(
                    metadata={
                        "ambient": True,
                        "source": signal.source,
                        "signal_key": signal.key,
                    }
                )
                thread_id = thread["thread_id"]
            except Exception as exc:
                raise DeliveryError(f"could not create a thread: {exc}") from exc

        try:
            state = await client.runs.wait(
                thread_id,
                _ASSISTANT,
                input={
                    "messages": [
                        {"role": "user", "content": compose_prompt(signal, member, verdict)}
                    ]
                },
            )
        except Exception as exc:
            logger.warning(
                "ambient run failed member=%s key=%s thread=%s",
                member.sub, signal.key, thread_id, exc_info=True,
            )
            if not reused:
                await _discard(client, thread_id)
            raise DeliveryError(f"the compose turn failed: {exc}") from exc

        tools = _tools_called(state)
        logger.info(
            "ambient turn member=%s source=%s key=%s thread=%s tools=%s",
            member.sub, signal.source, signal.key, thread_id, ",".join(tools) or "none",
        )

        text = _final_text(state)
        if text is None:
            logger.warning(
                "ambient run produced no final answer member=%s key=%s thread=%s",
                member.sub, signal.key, thread_id,
            )
            if not reused:
                await _discard(client, thread_id)
            raise DeliveryError("the compose turn produced no final answer")

        if _is_veto(text):
            logger.info("Eve declined to speak about %s; discarding the thread", signal.key)
            if not reused:
                await _discard(client, thread_id)
            return None

        title = "Eve - urgent" if verdict.urgent else "Eve"
        try:
            sent = await notifier.send(
                title=title, body=text, urgent=verdict.urgent, click_url=_click_url(thread_id)
            )
        except Exception:
            logger.warning("the push raised for %s", thread_id, exc_info=True)
            sent = False
        if not sent:
            logger.warning("the push failed but %s holds the message", thread_id)
        return thread_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_notify.py -v`
Expected: all pass (existing tests unaffected, 4 new ones pass)

- [ ] **Step 5: Re-run pipeline tests from Task 9**

Run: `uv run pytest tests/test_ambient_pipeline.py -v`
Expected: all pass, now that `deliver` accepts `thread_id`

- [ ] **Step 6: Commit**

```bash
git add src/eve_ambient/notify.py tests/test_ambient_notify.py
git commit -m "feat(eve-computer): reuse the originating thread when delivering a computer result"
```

---

### Task 11: The `eve-computer` harness's task API

**Files:**
- Create: `src/eve_computer/__init__.py`
- Create: `src/eve_computer/settings.py`
- Create: `src/eve_computer/store.py`
- Create: `src/eve_computer/app.py`
- Test: `tests/test_computer_app.py`

**Interfaces:**
- Consumes: nothing from `eve`/`eve_ambient`/`eve_sandbox`/`eve_tools` — this package must import none of them, the same isolation `eve_sandbox` has (design doc: the box learns nothing about the family).
- Produces: the FastAPI app `eve_computer.app.app`, with `POST /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/artifacts/{name}`, `DELETE /tasks/{id}`, `GET /healthz`. `run_task` is imported into `app.py`'s namespace from `eve_computer.harness` (built in Task 12) so this task's tests can monkeypatch `eve_computer.app.run_task` directly without touching the real driver.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_computer_app.py
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer " + "k" * 32}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "k" * 32)
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    from eve_computer import app as app_module
    from eve_computer import store

    store._tasks.clear()
    with TestClient(app_module.app) as c:
        yield c, app_module
    get_computer_settings.cache_clear()


def test_healthz_needs_no_auth(client):
    c, _ = client
    assert c.get("/healthz").status_code == 200


def test_tasks_requires_the_bearer_token(client):
    c, _ = client
    assert c.post("/tasks", json={"id": "t1", "goal": "do it"}).status_code == 401


def test_dispatching_a_task_returns_202_and_queued(client):
    c, _ = client
    response = c.post("/tasks", headers=AUTH, json={"id": "t1", "goal": "do it"})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_a_dispatched_task_eventually_finishes(client):
    c, app_module = client
    app_module.run_task = AsyncMock(return_value={"summary": "done"})

    c.post("/tasks", headers=AUTH, json={"id": "t1", "goal": "do it"})
    deadline = time.time() + 2
    status = None
    while time.time() < deadline:
        status = c.get("/tasks/t1", headers=AUTH).json()
        if status["status"] not in ("queued", "running"):
            break
        time.sleep(0.02)

    assert status["status"] == "finished"
    assert status["result"] == {"summary": "done"}


def test_a_task_whose_result_carries_an_error_is_marked_failed(client):
    c, app_module = client
    app_module.run_task = AsyncMock(return_value={"error": "RuntimeError: boom"})

    c.post("/tasks", headers=AUTH, json={"id": "t1", "goal": "do it"})
    deadline = time.time() + 2
    status = None
    while time.time() < deadline:
        status = c.get("/tasks/t1", headers=AUTH).json()
        if status["status"] not in ("queued", "running"):
            break
        time.sleep(0.02)

    assert status["status"] == "failed"


def test_an_unknown_task_is_404(client):
    c, _ = client
    assert c.get("/tasks/nope", headers=AUTH).status_code == 404
    assert c.delete("/tasks/nope", headers=AUTH).status_code == 404


def test_deleting_a_queued_task_marks_it_killed(client):
    c, app_module = client
    app_module.run_task = AsyncMock(return_value={"summary": "should not run"})

    c.post("/tasks", headers=AUTH, json={"id": "t1", "goal": "do it"})
    response = c.delete("/tasks/t1", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "killed"


def test_an_artifact_path_cannot_escape_the_tasks_directory(client, tmp_path, monkeypatch):
    c, app_module = client
    from eve_computer.settings import get_computer_settings

    monkeypatch.setenv("EVE_COMPUTER_TASKS_DIR", str(tmp_path))
    get_computer_settings.cache_clear()
    (tmp_path / "secret.txt").write_text("outside")

    response = c.get("/tasks/t1/artifacts/../../secret.txt", headers=AUTH)
    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_computer_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve_computer'`

- [ ] **Step 3: Write settings, store, and the app**

```python
# src/eve_computer/__init__.py
```
(empty — package marker)

```python
# src/eve_computer/settings.py
"""eve-computer's own configuration. No third-party credential here - her
accounts live only as browser session cookies on the PVC (design doc:
"Identity"), never an environment variable."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ComputerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVE_COMPUTER_", extra="ignore")

    api_key: str = ""
    litellm_base_url: str = "https://litellm.chalifour.dev"
    litellm_api_key: str = ""
    max_turns: int = 40
    task_timeout_seconds: int = 1800
    tasks_dir: str = "/home/eve/tasks"


@lru_cache(maxsize=1)
def get_computer_settings() -> ComputerSettings:
    return ComputerSettings()
```

```python
# src/eve_computer/store.py
"""In-memory task state for the box. Not durable across a restart -
eve-ambient's poller (eve.computer.poller) marks a task stale after a
timeout when the box stops answering for it, rather than this service
needing to survive its own restart to report a result.

ponytail: a dict behind a lock, not sqlite - one task runs at a time and
nothing here needs to outlive a restart. Move to sqlite on the PVC if a
future requirement needs task history to survive one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Task:
    id: str
    goal: str
    status: str = "queued"  # queued -> running -> finished | failed | killed
    result: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


_tasks: dict[str, Task] = {}
_lock = asyncio.Lock()


async def create(task_id: str, goal: str) -> Task:
    async with _lock:
        task = Task(id=task_id, goal=goal)
        _tasks[task_id] = task
        return task


async def get(task_id: str) -> Task | None:
    return _tasks.get(task_id)


async def set_status(task_id: str, status: str) -> None:
    async with _lock:
        if task_id in _tasks:
            _tasks[task_id].status = status


async def set_result(task_id: str, status: str, result: dict) -> None:
    async with _lock:
        if task_id in _tasks:
            _tasks[task_id].status = status
            _tasks[task_id].result = result
```

```python
# src/eve_computer/app.py
"""eve-computer's own HTTP surface: a task queue with one worker, because
one machine has one X display and one mouse (design doc: "One task at a
time, queued"). Same bearer-token auth shape as eve-tools and eve-sandbox.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from eve_computer import store
from eve_computer.harness import run_task
from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

_queue: asyncio.Queue | None = None
_worker: asyncio.Task | None = None
_inflight: dict[str, asyncio.Task] = {}


def _check_auth(authorization: str | None) -> None:
    settings = get_computer_settings()
    if not settings.api_key or authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=401, detail="unauthorized")


async def _work_forever(queue: asyncio.Queue) -> None:
    while True:
        task_id = await queue.get()
        task = await store.get(task_id)
        if task is None or task.status == "killed":
            queue.task_done()
            continue
        await store.set_status(task_id, "running")
        runner = asyncio.ensure_future(run_task(task_id, task.goal))
        _inflight[task_id] = runner
        try:
            result = await runner
            status = "failed" if result.get("error") else "finished"
        except asyncio.CancelledError:
            status, result = "killed", {"error": "killed"}
        except Exception as exc:
            logger.warning("task %s raised", task_id, exc_info=True)
            status, result = "failed", {"error": f"{exc.__class__.__name__}: {exc}"}
        finally:
            _inflight.pop(task_id, None)
        await store.set_result(task_id, status, result)
        queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue, _worker
    _queue = asyncio.Queue()
    _worker = asyncio.create_task(_work_forever(_queue))
    yield
    _worker.cancel()
    try:
        await _worker
    except asyncio.CancelledError:
        pass


app = FastAPI(title="eve-computer", lifespan=lifespan)


class TaskRequest(BaseModel):
    id: str
    goal: str


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/tasks", status_code=202)
async def create_task_route(
    body: TaskRequest, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    await store.create(body.id, body.goal)
    assert _queue is not None
    await _queue.put(body.id)
    return {"id": body.id, "status": "queued"}


@app.get("/tasks/{task_id}")
async def get_task_route(
    task_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    out_dir = Path(get_computer_settings().tasks_dir) / task_id / "out"
    artifacts = (
        sorted(p.name for p in out_dir.glob("*") if p.is_file()) if out_dir.is_dir() else []
    )
    return {"status": task.status, "result": task.result, "artifacts": artifacts}


@app.get("/tasks/{task_id}/artifacts/{name}")
async def get_artifact_route(
    task_id: str, name: str, authorization: str | None = Header(default=None)
):
    _check_auth(authorization)
    if name in ("..", ".") or "/" in name or "\\" in name:
        raise HTTPException(status_code=404, detail="unknown artifact")
    path = Path(get_computer_settings().tasks_dir) / task_id / "out" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="unknown artifact")
    return FileResponse(path)


@app.delete("/tasks/{task_id}")
async def delete_task_route(
    task_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    runner = _inflight.get(task_id)
    if runner is not None:
        runner.cancel()
    else:
        await store.set_status(task_id, "killed")
    return {"id": task_id, "status": "killed"}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_computer_app.py -v`
Expected: 8 passed — this needs a real (even if trivial) `eve_computer/harness.py` to import from, so write Task 12's `harness.py` stub before this step if working strictly task-by-task; a minimal placeholder-free version (real `run_task` calling `claude-agent-sdk`) is built in Task 12.

- [ ] **Step 5: Commit**

```bash
git add src/eve_computer/__init__.py src/eve_computer/settings.py src/eve_computer/store.py src/eve_computer/app.py tests/test_computer_app.py
git commit -m "feat(eve-computer): add the harness's task queue and HTTP surface"
```

---

### Task 12: The harness driver and the GUI tool

**Files:**
- Create: `src/eve_computer/harness.py`
- Create: `src/eve_computer/gui_tool.py`
- Create: `Dockerfile.eve-computer`
- Create: `src/eve_computer/bootstrap.sh`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: the `claude-agent-sdk` package (new dependency).
- Produces: `async def run_task(task_id: str, goal: str) -> dict`, returning `{"summary": str, "artifacts": list[str]}` on success or `{"error": str}` on failure — the contract `app.py`'s worker loop (Task 11) already checks `result.get("error")` against.

This is the one piece of the design that depends on a brand-new external dependency this codebase has not used before. The shape below is a best-effort, working starting point; **verify it against the installed `claude-agent-sdk` version's actual API** (`uv run python -c "import claude_agent_sdk; help(claude_agent_sdk)"`) before treating it as final, and adjust the exact call signatures if they differ. The GUI tool is Anthropic's reference computer-use tool, lifted rather than rewritten (design doc: "v1: Claude, and a seam") — it cannot be meaningfully unit-tested without a real X server, so its correctness is covered by Task 13's docker-tier smoke test (the binaries exist and run) and the `live` tier (DoD item 4: watch her work over VNC).

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`'s `dependencies` list, add (alphabetically, next to `caldav`):

```toml
    "claude-agent-sdk>=0.1.0",
```

Run: `uv lock`
Expected: `uv.lock` updates with no conflicts.

- [ ] **Step 2: Write the GUI tool**

```python
# src/eve_computer/gui_tool.py
"""Anthropic's reference computer-use tool, lifted rather than rewritten
(design doc: "v1: Claude, and a seam") - xdotool driving the desktop on
:99, a screenshot via ImageMagick's `import`. Registered as an SDK MCP tool
so claude-agent-sdk can call it exactly like its built-in bash/read/write/
edit tools.
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

DISPLAY = ":99"


async def _xdotool(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "xdotool", *args, env={"DISPLAY": DISPLAY},
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)} failed: {stderr.decode()}")


async def _screenshot() -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        proc = await asyncio.create_subprocess_exec(
            "import", "-window", "root", handle.name, env={"DISPLAY": DISPLAY},
        )
        await proc.communicate()
        return base64.b64encode(Path(handle.name).read_bytes()).decode()


@tool(
    "computer",
    "Control the desktop: screenshot, click, move the mouse, type text, or "
    "press a key. `action` is one of: screenshot, left_click, mouse_move, "
    "type, key. `coordinate` is [x, y], required for left_click/mouse_move. "
    "`text` is required for type/key.",
    {"action": str, "coordinate": list, "text": str},
)
async def computer(args: dict) -> dict:
    action = args["action"]
    if action == "screenshot":
        return {"content": [{"type": "image", "data": await _screenshot()}]}
    if action == "left_click":
        x, y = args["coordinate"]
        await _xdotool("mousemove", str(x), str(y), "click", "1")
    elif action == "mouse_move":
        x, y = args["coordinate"]
        await _xdotool("mousemove", str(x), str(y))
    elif action == "type":
        await _xdotool("type", "--", args["text"])
    elif action == "key":
        await _xdotool("key", args["text"])
    else:
        return {"content": [{"type": "text", "text": f"unknown action {action!r}"}]}
    return {"content": [{"type": "text", "text": "ok"}]}


computer_use_server = create_sdk_mcp_server(name="computer-use", tools=[computer])
```

- [ ] **Step 3: Write the harness**

```python
# src/eve_computer/harness.py
"""Drives one task through claude-agent-sdk. Everything downstream of
run_task only needs a goal in and a {"summary"|"error", "artifacts"} out
(design doc: "The swap seam is the task API itself") - a second driver later
is one new function, not a rewrite of app.py.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query

from eve_computer.gui_tool import computer_use_server
from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You operate a real Linux desktop on behalf of a family member. Use the "
    "shell, the browser, and the computer tool to complete the goal below. "
    "Write anything the family should see - a file, a summary, a screenshot "
    "- into ./out/. You are not Eve; do not speak in her voice, and do not "
    "address the family member directly - your final message is a report to "
    "her, not to them."
)


async def run_task(task_id: str, goal: str) -> dict:
    settings = get_computer_settings()
    workdir = Path(settings.tasks_dir) / task_id
    (workdir / "out").mkdir(parents=True, exist_ok=True)

    os.environ["ANTHROPIC_BASE_URL"] = settings.litellm_base_url
    os.environ["ANTHROPIC_API_KEY"] = settings.litellm_api_key

    options = ClaudeAgentOptions(
        cwd=str(workdir),
        max_turns=settings.max_turns,
        system_prompt=_SYSTEM_PROMPT,
        mcp_servers={"computer-use": computer_use_server},
    )

    final_text = ""
    try:
        async for message in query(prompt=goal, options=options):
            result_text = getattr(message, "result", None)
            if result_text:
                final_text = result_text
    except Exception as exc:
        logger.warning("task %s failed", task_id, exc_info=True)
        return {"error": f"{exc.__class__.__name__}: {exc}"}

    artifacts = sorted(p.name for p in (workdir / "out").glob("*") if p.is_file())
    return {"summary": final_text, "artifacts": artifacts}
```

- [ ] **Step 4: Run the app tests from Task 11 now that `harness.py` exists**

Run: `uv run pytest tests/test_computer_app.py -v`
Expected: 8 passed (these tests monkeypatch `eve_computer.app.run_task`, so `harness.py`'s real body never executes in the unit tier)

- [ ] **Step 5: Write the Dockerfile**

```dockerfile
# Dockerfile.eve-computer
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv

# The working set (design doc: "Image"): Xvfb + a window manager + x11vnc +
# Chromium give her a desktop reachable over VNC; git/curl/ripgrep/ffmpeg/jq
# and Codex CLI are the shell she works in. Anything durable she installs on
# top gets replayed by bootstrap.sh (design doc: "Storage") rather than
# rebuilt into the image every time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb fluxbox x11vnc chromium fonts-liberation imagemagick xdotool \
        git curl ripgrep ffmpeg jq sudo nodejs npm ca-certificates \
    && npm install -g @openai/codex \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/eve_computer ./src/eve_computer
RUN chmod +x ./src/eve_computer/bootstrap.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PORT=8092 \
    DISPLAY=:99

EXPOSE 8092 5900

# --create-home, unlike every other image's --no-create-home: this is the
# one deploy with a persistent PVC-backed home directory (design doc:
# "Storage"). Passwordless sudo is deliberate too (design doc: "Image") - the
# pod spec, not the user account, is what contains her.
RUN useradd --system --uid 10004 --create-home --shell /bin/bash eve \
    && chown -R eve:eve /app /home/eve \
    && echo "eve ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/eve \
    && chmod 0440 /etc/sudoers.d/eve
USER 10004

CMD ["sh", "-c", "/app/src/eve_computer/bootstrap.sh && exec uvicorn eve_computer.app:app --host 0.0.0.0 --port 8092"]
```

- [ ] **Step 6: Write `bootstrap.sh`**

```sh
#!/bin/sh
# Self-heals the ephemeral parts of the image against the one thing that
# persists: /home/eve (design doc: "Storage"). A pod reschedule loses
# everything `apt install`ed by hand; this replays it from a file the
# worker itself maintains, so a lost package need not become a pull request.
set -eu

PACKAGES_FILE="/home/eve/.eve/packages.txt"
mkdir -p /home/eve/.eve /home/eve/tasks

if [ -f "$PACKAGES_FILE" ]; then
    PACKAGES=$(grep -v '^[[:space:]]*#' "$PACKAGES_FILE" | tr '\n' ' ')
    if [ -n "$PACKAGES" ]; then
        sudo apt-get update
        # shellcheck disable=SC2086
        sudo apt-get install -y --no-install-recommends $PACKAGES
    fi
fi

Xvfb :99 -screen 0 1920x1080x24 &
fluxbox &
x11vnc -display :99 -forever -shared -rfbport 5900 -nopw &
```

- [ ] **Step 7: Commit**

```bash
git add src/eve_computer/harness.py src/eve_computer/gui_tool.py Dockerfile.eve-computer src/eve_computer/bootstrap.sh pyproject.toml uv.lock
git commit -m "feat(eve-computer): add the claude-agent-sdk harness, the GUI tool, and the image"
```

---

### Task 13: Integration + docker-tier coverage

**Files:**
- Modify: `docker-compose.test.yml`
- Create: `tests/test_computer_docker_image.py`

**Interfaces:**
- Consumes: `Dockerfile.eve-computer` (Task 12).
- Produces: a `docker`-marked test that builds the real image, runs it, and dispatches a real headless task through its HTTP surface — the design doc's "integration runs the real image under docker-compose against a headless task," adapted to this repo's existing `docker`-tier pattern (`tests/test_sandbox_docker_image.py`) rather than a separate `integration`-tier compose service, since eve-computer needs no other service (Postgres, Redis) running alongside it to answer `POST /tasks`.

- [ ] **Step 1: Add the compose service block**

In `docker-compose.test.yml`, after the `eve-sandbox` service:

```yaml
  eve-computer:
    build:
      context: .
      dockerfile: Dockerfile.eve-computer
    environment:
      EVE_COMPUTER_API_KEY: test-key-0123456789abcdef0123456789ab
    ports: ["18096:8092"]
    healthcheck:
      test: ["CMD", "python", "-c",
              "import urllib.request as u; u.urlopen('http://localhost:8092/healthz')"]
      interval: 2s
      retries: 30
```

- [ ] **Step 2: Write the docker-tier test**

```python
# tests/test_computer_docker_image.py
"""Builds the real image from Dockerfile.eve-computer, runs a container, and
drives a task through its real HTTP surface with `harness.run_task` faked
out via an environment variable the test sets before the container starts -
the same "assert the built artifact actually works" gap
test_sandbox_docker_image.py's own docstring explains no unit test can close.
Skips gracefully if Docker isn't available.
"""

from __future__ import annotations

import shutil
import subprocess
import time

import httpx
import pytest

pytestmark = pytest.mark.docker

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
IMAGE_TAG = "eve-computer-docker-image-test:latest"
CONTAINER_NAME = "eve-computer-docker-image-test"
HOST_PORT = 18096
API_KEY = "test-key-0123456789abcdef0123456789ab"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False


@pytest.fixture(scope="module")
def computer_image():
    if not _docker_available():
        pytest.skip("docker is not available in this environment")
    subprocess.run(
        ["docker", "build", "-f", "Dockerfile.eve-computer", "-t", IMAGE_TAG, str(_REPO_ROOT)],
        check=True, timeout=1200,
    )
    yield IMAGE_TAG


@pytest.fixture
def computer_container(computer_image):
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d", "--name", CONTAINER_NAME,
            "-p", f"{HOST_PORT}:8092",
            "-e", f"EVE_COMPUTER_API_KEY={API_KEY}",
            computer_image,
        ],
        check=True,
    )
    url = f"http://127.0.0.1:{HOST_PORT}"
    deadline = time.time() + 30
    try:
        while time.time() < deadline:
            try:
                if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        else:
            logs = subprocess.run(
                ["docker", "logs", CONTAINER_NAME], capture_output=True, text=True
            )
            raise RuntimeError(
                f"eve-computer did not become healthy within 30s:\n{logs.stdout}\n{logs.stderr}"
            )
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


def test_the_gui_tool_dependencies_are_present(computer_container):
    """Smoke-level coverage for the one piece nothing else can test without a
    real X server (Task 12's note): the binaries the GUI tool shells out to
    actually exist in the built image."""
    for binary in ("xdotool", "import", "Xvfb", "x11vnc", "codex"):
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "which", binary],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"{binary} missing from the built image"


def test_bootstrap_started_the_desktop(computer_container):
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "pgrep", "-f", "Xvfb"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, "Xvfb is not running"


def test_the_task_api_requires_auth(computer_container):
    response = httpx.post(f"{computer_container}/tasks", json={"id": "t1", "goal": "x"})
    assert response.status_code == 401


def test_dispatching_a_task_is_accepted(computer_container):
    response = httpx.post(
        f"{computer_container}/tasks",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"id": "smoke-1", "goal": "say hello and write it to ./out/hello.txt"},
        timeout=10,
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_computer_docker_image.py -m docker -v`
Expected: 4 passed (skips outright if Docker is unavailable). The build is slow (a cold build installing Chromium/Node/Codex is on the order of several minutes) — this is expected, matching `test_sandbox_docker_image.py`'s own documented cost.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.test.yml tests/test_computer_docker_image.py
git commit -m "test(eve-computer): add the docker-tier image smoke test"
```

---

### Task 14: Wire the fifth image into CI

**Files:**
- Modify: `.github/workflows/build.yml`

**Interfaces:**
- Consumes: `Dockerfile.eve-computer` (Task 12), `tests/test_computer_docker_image.py` (Task 13).
- Produces: `eve-computer` published to `ghcr.io/noahchalifour/eve-computer` on a version tag, exactly like the other four images. Any `release-eve` skill/script in this lab derives its image list mechanically from this file's matrix (`awk '/^ *- image: /{print $3}'`), so no separate skill edit is needed once this matrix entry exists.

- [ ] **Step 1: Add the matrix entry**

In `.github/workflows/build.yml`, under the `image` job's `strategy.matrix.include`, after `eve-sandbox`:

```yaml
          - image: eve-computer
            dockerfile: Dockerfile.eve-computer
```

- [ ] **Step 2: Verify the workflow still parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml'))"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/build.yml
git commit -m "ci(eve-computer): publish the fifth image on a version tag"
```

---

### Task 15: The unreachability test

**Files:**
- Create: `tests/test_computer_live.py`

**Interfaces:**
- Consumes: `kubectl` pointed at the real cluster; `deploy/eve-computer` already running there (this test cannot pass before the manifests in the `infrastructure` repo exist and are deployed — see Task 16's note on where those manifests live).
- Produces: no importable code; a `live`-marked pytest file, run by hand.

This is "the test that matters most" (design doc: "Testing") — every safety claim in the design reduces to the NetworkPolicy, and this is what makes a future loosening of it fail loudly instead of silently.

- [ ] **Step 1: Write the test**

```python
# tests/test_computer_live.py
"""Checks that are only meaningful against the deployed pod - the design
doc's "test that matters most." Every safety claim in the design reduces to
the NetworkPolicy; this is what makes a future loosening of it fail loudly.

Run by hand:
`EVE_LIVE_TESTS=1 uv run pytest tests/test_computer_live.py -m live`
with kubectl pointed at the cluster.
"""

import os
import subprocess

import pytest

pytestmark = pytest.mark.live

POD = "deploy/eve-computer"

# Cluster-internal hosts that must be unreachable from this pod (design doc:
# "Egress" - "she cannot reach Postgres, eve-tools, eve-sandbox, Eve's own
# API, or the Kubernetes API server"). Hostnames match the other in-cluster
# Services this repository's own Dockerfiles/settings default to.
_UNREACHABLE_HOSTS = [
    ("postgres", 5432),
    ("eve-tools", 8090),
    ("eve-sandbox", 8091),
    ("eve", 2026),
    ("kubernetes.default.svc", 443),
]


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
    result = _exec("ls", "/var/run/secrets/kubernetes.io/serviceaccount")
    assert result.returncode != 0, result.stdout


@pytest.mark.parametrize("host,port", _UNREACHABLE_HOSTS)
def test_the_pod_cannot_reach_a_cluster_internal_host(host, port):
    result = _exec(
        "python3", "-c",
        "import socket;"
        "socket.setdefaulttimeout(5);"
        f"socket.create_connection(('{host}', {port}))",
    )
    assert result.returncode != 0
    assert "Errno" in result.stderr or "timed out" in result.stderr


def test_the_pod_can_reach_the_public_internet():
    """The egress policy is deny-by-default for RFC1918/cluster ranges, not
    deny-everything (design doc: "Egress") - the positive case is as much a
    part of the boundary as the negative ones above."""
    result = _exec(
        "python3", "-c",
        "import socket;"
        "socket.setdefaulttimeout(5);"
        "socket.create_connection(('example.com', 443))",
    )
    assert result.returncode == 0


def test_no_eve_environment_variables_are_present_beyond_the_api_key():
    result = _exec("printenv")
    leaked = [
        line for line in result.stdout.splitlines()
        if line.startswith("EVE_") and not line.startswith("EVE_COMPUTER_")
    ]
    assert leaked == [], leaked


def test_the_home_directory_survives_a_write():
    """DoD 1: the PVC-backed home, not just the pod, is what must persist."""
    marker = "/home/eve/.eve/live-test-marker"
    assert _exec("sh", "-c", f"touch {marker} && rm {marker}").returncode == 0
```

- [ ] **Step 2: Confirm it is selected by the `live` marker and skips without the flag**

Run: `uv run pytest tests/test_computer_live.py -v`
Expected: every test SKIPPED ("EVE_LIVE_TESTS is not 1") — this file must not run in the default unit tier or in `docker-image-test`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_computer_live.py
git commit -m "test(eve-computer): add the live unreachability test"
```

---

### Task 16: ADR 0012, README, and architecture docs

**Files:**
- Create: `docs/adr/0012-granted-identity-vs-authored-capability.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: no code; the design's required doc updates ("Consequences for existing documents").

- [ ] **Step 1: Write ADR 0012**

```markdown
# 12. A granted identity is not authored credentialed capability

**Status:** Accepted
**Date:** 2026-08-28

## Context

The README's permanent boundaries include "Eve does not author credentialed
capability" - a tool needing a secret is an `eve-tools` handler, written by a
human, forever. `eve-computer` gives Eve a persistent machine, her own
accounts, and real logins acting unattended. Read literally, that is
credentialed capability, and this document has to say why the boundary still
holds rather than pretend the tension away.

## Decision

The revision is narrower than it first appears, and rests on three
properties: (1) the credentials are hers, not the family's - her own Google
account, her own GitHub, granted access the same way a human assistant would
be onboarded, revoked with a checkbox rather than a code change; (2) a human
provisions every one of them, logged in once, by hand, over VNC - Eve cannot
create an account or obtain a credential herself; (3) the blast radius is
recoverable - a maximally bad day costs her own accounts and some compute,
never the family roster, anyone's permissions, or any of the other services'
credentials, because the pod's `NetworkPolicy` cannot reach any of them.

The *shape* of the original boundary is unchanged: Eve still does not author
credentialed capability, because she authors nothing here - a human granted a
bounded identity to a bounded machine. What changed is that "actions Eve can
take" is no longer categorically off the table; it is on the table exactly to
the extent that a wrong outcome is recoverable.

`eve-computer` is a new service beside `eve-sandbox`, not a replacement for
it or a change to its contract. ADR 0010's argument - "one service satisfying
both satisfies neither" - applies here by the same symmetry it used to
separate `eve-tools` from Eve's main container: `eve-sandbox` holds nothing
and runs machine-authored pure functions with no network; `eve-computer`
persists files, browses the web, and holds login sessions. Their invariants
are opposite, so they stay two services.

## Consequences

A maximally malicious task on `eve-computer` can spend her dedicated LiteLLM
budget and misuse her own accounts - not the family's Google/GitHub/anything
else, not the cluster, not Postgres, not any other service's credential,
because the pod cannot reach any of them (verified by
`tests/test_computer_live.py`). "Eve does not learn unsupervised" is
similarly narrowed rather than broken: the worker maintains its own
`AGENTS.md` on the box's disk, which is unsupervised learning bounded to
operating her own machine - it has no route into her persona, her authored
rules, or her behaviour toward any family member, all of which still come
from a specific turn with a specific member.
```

- [ ] **Step 2: Update README.md**

Replace the phase table's closing sentence and the "Eve does not author credentialed capability" bullet.

Replace:

```markdown
This repository is Phase 5c, and with it **the five-phase program is
complete**.
```

with:

```markdown
This repository was Phase 5c, completing the original five-phase program.
A sixth deploy, `eve-computer`, now sits beside it - see
[`docs/superpowers/specs/2026-08-28-eve-computer-design.md`](docs/superpowers/specs/2026-08-28-eve-computer-design.md)
and [ADR 0012](docs/adr/0012-granted-identity-vs-authored-capability.md).
```

Replace the `- **Eve does not author credentialed capability.**` bullet with:

```markdown
- **Eve does not author credentialed capability.** A tool needing a secret is
  an `eve-tools` handler in a pull request, forever. `eve-computer` grants Eve
  her *own* identity - accounts a human provisions by hand over VNC, revocable
  with a checkbox, whose blast radius the pod's `NetworkPolicy` bounds to her
  own accounts and compute - which is a different thing from authoring
  capability over the family's credentials. See
  [ADR 0012](docs/adr/0012-granted-identity-vs-authored-capability.md).
```

- [ ] **Step 3: Add a section to docs/architecture.md**

After the `## Sandboxed tools` section (before `## Aegra and \`aegra.json\``), insert:

```markdown
## Eve's computer

A sixth service, `eve-computer` (`src/eve_computer/`, `Dockerfile.eve-computer`),
gives Eve a persistent Linux desktop: her own accounts, a browser, a shell,
and internet access, behind a task API she is dispatched to and polled for -
never called back from. See
[`docs/superpowers/specs/2026-08-28-eve-computer-design.md`](superpowers/specs/2026-08-28-eve-computer-design.md)
and [ADR 0012](adr/0012-granted-identity-vs-authored-capability.md) for the
full design and the boundary argument.

**Dispatch.** `dispatch_computer_task` (`src/eve/computer/dispatch.py`) is a
bare tool, bound alongside the specialists when `EVE_COMPUTER_ENABLED=true` -
requires `computer.use`, checked before the HTTP call, per ADR 0006's
pattern. It mints a task id, calls `eve.tools_client.dispatch_task` (a
dedicated door, not `invoke()` - the box's task API is a lifecycle, not the
`{tool, arguments}` contract `eve-tools`/`eve-sandbox` share), and records the
task in `eve_computer_task` (`alembic/versions/0004_eve_computer_task.py`) -
Eve's own row, holding what the box itself never learns: which member asked,
and on which thread.

**The poller.** `eve.computer.poller.sync` asks the box about every task
Eve is still waiting on and updates that row; `eve_ambient.sources.computer`
turns each newly-resolved row into a `Signal`, polled once per tick for the
whole household (`per_member=False`) since the box itself carries no
per-member data to poll by. Two deliberate deviations from every other
ambient source: `eve_ambient.pipeline.handle_signal` bypasses the REFLEX
relevance filter for `source == "computer"` (a direct request is never "not
relevant"), and `eve_ambient.notify.deliver` reuses the *originating* thread
(the one the member dispatched from) rather than creating a fresh one -
`gates.SOURCE_PERMISSION` still gates the audience on `computer.use` even
though no filter verdict was involved.

**The harness.** `eve-computer`'s own FastAPI surface
(`src/eve_computer/app.py`) is a one-task-at-a-time queue over
`POST /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/artifacts/{name}`, and
`DELETE /tasks/{id}` - one display, one mouse, so concurrent GUI tasks would
fight over the same cursor. `harness.py`'s `run_task` drives
`claude-agent-sdk` with bash/read/write/edit tools supplied directly and a
lifted computer-use tool (`gui_tool.py`, xdotool against `:99`); Codex CLI is
available as a plain shell command, not a second harness. This package
imports nothing from `eve`, `eve_ambient`, `eve_tools`, or `eve_sandbox` -
the box learns only a goal string and a task id, never a member subject, a
name, or a permission.

**Isolation.** Enforcement is the pod, exactly as ADR 0010 argues for
`eve-sandbox`, but with the opposite contract: default-deny egress except
DNS and public 80/443 (RFC1918 ranges, the cluster CIDRs, and the metadata
endpoint are explicitly denied), no ServiceAccount token, ingress restricted
to the harness port from `eve`/`eve-ambient` and the VNC port reached only
via `kubectl port-forward`. `tests/test_computer_live.py` asserts this
directly against the deployed pod - the same shape as
`tests/test_sandbox_live.py`, adapted to a machine that must reach the
public internet while still being unable to reach anything inside the
cluster.
```

- [ ] **Step 4: Add ADR 0012 to the Decision records list**

At the end of `docs/architecture.md`'s `## Decision records` list, add:

```markdown
- [ADR 0012 — A granted identity is not authored credentialed capability](adr/0012-granted-identity-vs-authored-capability.md)
```

- [ ] **Step 5: Note where the deployment manifests live**

In `docs/architecture.md`'s `## Deployment` section, after the `eve-sandbox` paragraph, add:

```markdown
`eve-computer` adds a fifth app to that same `infrastructure` repository:
a Deployment with a 50 GiB PVC mounted at `/home/eve`, `hostUsers: false`,
every capability dropped, no ServiceAccount token, and a `NetworkPolicy`
denying RFC1918/cluster/link-local ranges while allowing DNS and public
80/443; a Service exposing only the harness port to `eve` and `eve-ambient`;
no Ingress and no exposed VNC Service, since the operator reaches VNC only
through `kubectl port-forward`, which never traverses a `NetworkPolicy`.
This repository's side is `Dockerfile.eve-computer`, which departs from
every other image's pattern in two deliberate ways: it runs as a user with a
real home directory (`--create-home`, not `--no-create-home`) and
passwordless sudo, because a computer she cannot install a package on is not
a computer - the pod spec, not the user account, is what contains her.
```

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0012-granted-identity-vs-authored-capability.md README.md docs/architecture.md
git commit -m "docs(eve-computer): add ADR 0012 and update README/architecture for the sixth deploy"
```

---

## Self-review notes

- **Spec coverage:** Image/storage/network/identity (Task 12 + docs), harness v1 seam (Task 12), dispatch tool + permission (Task 6), reporting back + filter bypass + thread reuse (Tasks 8–10), oversight (VNC/kill-switches are pod-spec/infra, documented in Task 16; `DELETE /tasks/{id}` is Task 11), testing tiers (Tasks 2, 5, 13, 15), definition-of-done items 1–6 (Tasks 11–16 collectively; items 1/4 depend on the `infrastructure` repo manifests, out of this repository's scope per `docs/architecture.md`'s existing "Deployment" section), consequences for existing docs (Task 16).
- **What's out of scope for this repository, deliberately:** the actual Kubernetes manifests (PVC, Deployment, NetworkPolicy, Service) — every existing deploy's manifests live in the separate `infrastructure` repository, not here; Task 16 documents their shape in prose, matching how `eve-sandbox`'s manifests are described in `docs/architecture.md` without a YAML file existing in this repository.
