# Background Memory Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the `extract` node's work off the turn's critical path into a background task that the *next* turn on the same thread joins before it reads memory, so a turn ends when Eve stops talking instead of when her bookkeeping finishes.

**Architecture:** `extract` becomes a thin spawner that hands `_run_extraction` to a per-thread task registry (`eve.memory.pending`) and returns `{}` immediately. `recall` joins that thread's pending task — bounded by a budget, exactly like the embedding arm in ADR 0002 — before it reads memory. The wait therefore lands in the gap where a member is typing their next message rather than in front of the reply they just asked for, and ordering is preserved: turn N+1 can never read memory that turn N's writes have not yet reached.

**Tech Stack:** Python 3.12, LangGraph, asyncio, OpenTelemetry, pytest (`asyncio_mode = "auto"`), psycopg_pool.

**Spec:** None — this plan originates from a working session, not a design doc. ADR 0010 (Task 4) is the durable record of the decision. The constraint it interacts with is `docs/adr/0002-no-llm-before-first-token.md`; the memory subsystem it modifies is specified in `docs/superpowers/specs/2026-08-18-eve-memory.md`.

## Global Constraints

- **A degraded turn is a complete turn.** Every new failure path (join timeout, extraction crash, registry miss) must leave a turn that still answers. Nothing added here may raise into the graph.
- **Bounded and cancellable, never unbounded.** Any new wait carries an explicit budget from settings, mirroring `memory_recall_embed_budget_ms`.
- **No LLM call may precede the first streamed token** (ADR 0002). This plan must not add one to `load_context` or `recall`. The join added to `recall` is a wait on an *already-running* task, not a new call — but it does sit in front of the first token, so it must be bounded.
- **`extract` returns `{}` in every path.** Verified at `src/eve/memory/extract.py:204,226,239`. It contributes no state to the graph, which is what makes detaching it safe. Do not change this.
- **The registry must hold strong references to its tasks.** `asyncio` keeps only weak references to tasks; an unreferenced task can be garbage-collected mid-flight and silently lose its writes.
- **Settings default:** `memory_extract_background` defaults to **`True`**. Do not copy the off-by-default convention from `ambient_enabled` / `self_authoring_enabled` — those are off because they act without being asked, a safety rationale. This is a latency change to an existing path, and defaulting it off would leave it permanently untested.
- Python 3.12: `asyncio.TimeoutError` is an alias of the builtin `TimeoutError`; catch `TimeoutError`.

## Background: what actually blocks, verified

Do not re-derive this; it was checked against the installed `aegra_api` 0.10.3 and the OTel SDK.

1. `extract` sits between `eve` and `END` (`src/eve/graph.py:156-158`). `graph.astream()` (`aegra_api/services/langgraph_service.py:333`) does not finish until the graph reaches `END`, so the SSE stream stays open and the run row stays non-terminal for extraction's full duration — one REFLEX LLM call, plus embeddings, plus DB writes, plus a second REFLEX call for the digest on every 6th turn.
2. The streamed tokens are **not** delayed. `eve` has already finished. What is delayed is the turn being *done*: in `scripts/chat.py:50` the `async for` loop does not exit, so the `you>` prompt hangs; in a UI the spinner stays up.
3. **Aegra does not serialize runs per thread.** There is no `multitask_strategy` and no conflict rejection in `aegra_api/api/runs.py`; `set_thread_status(..., "busy")` (`run_preparation.py:251`) is a status field only. So the block is on turn *completion*, not on the next turn's admission — though a human client that waits for the stream to end pays it before every message anyway.
4. **Setting an attribute on an ended OTel span is silently dropped** (confirmed: emits `Setting attribute on ended span.` and the attribute map stays empty). A detached extraction therefore loses every `eve.extract.*` and `eve.authoring.*` attribute unless it opens its own span. Since `eve.authoring.rules_written` is the design doc's named signal for "authoring never fires at all", losing it would be a silent observability regression. Task 2 opens a new span for exactly this reason.
5. The DB pool (`src/eve/memory/db.py:186-204`) is module-level and app-scoped with `max_size=5`, so a task outliving its run may safely use it.

## File Structure

- **Create** `src/eve/memory/pending.py` — per-thread background task registry. Owns strong references, spawn/join/drain. Imports nothing from `eve` (no settings, no store), so both `extract` and `recall` can depend on it without a cycle.
- **Create** `tests/test_memory_pending.py` — registry unit tests.
- **Create** `docs/adr/0010-extraction-is-detached-and-joined.md` — the decision record.
- **Modify** `src/eve/settings.py` — two new fields.
- **Modify** `src/eve/memory/extract.py` — split `extract` into a spawner plus `_run_extraction`; open an explicit span.
- **Modify** `src/eve/memory/recall.py` — join the pending extraction before reading memory.
- **Modify** `tests/test_memory_extract.py` — drain after each `extract()` call (9 sites).
- **Modify** `tests/test_skills_integration.py:210` — same drain (1 site).
- **Modify** `tests/test_memory_recall.py` — join-ordering and budget tests.
- **Modify** `docs/architecture.md` — update the graph description.

---

### Task 1: The pending-extraction registry

**Files:**
- Create: `src/eve/memory/pending.py`
- Create: `tests/test_memory_pending.py`
- Modify: `src/eve/settings.py` (after line 79, in the Memory block)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `eve.memory.pending.spawn(thread_id: str | None, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]`
  - `eve.memory.pending.join(thread_id: str | None, budget_s: float) -> bool` — `True` if there was nothing to wait for or it finished; `False` if the budget ran out.
  - `eve.memory.pending.drain() -> None` — await every in-flight task. For tests and shutdown.
  - `eve.memory.pending.clear() -> None` — drop all references without awaiting. For test isolation only.
  - `Settings.memory_extract_background: bool` (default `True`), `Settings.memory_extract_join_budget_ms: int` (default `5000`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory_pending.py`:

```python
import asyncio

import pytest

from eve.memory import pending


@pytest.fixture(autouse=True)
def _clean_registry():
    pending.clear()
    yield
    pending.clear()


async def test_spawn_runs_the_coroutine_in_the_background():
    ran = asyncio.Event()

    async def work():
        ran.set()

    pending.spawn("t1", work())
    assert not ran.is_set()  # not yet - spawn must not await
    await pending.drain()
    assert ran.is_set()


async def test_join_waits_for_the_pending_task():
    order = []

    async def work():
        await asyncio.sleep(0.01)
        order.append("extraction")

    pending.spawn("t1", work())
    assert await pending.join("t1", 1.0) is True
    order.append("recall")
    assert order == ["extraction", "recall"]


async def test_join_with_nothing_pending_returns_immediately():
    assert await pending.join("t1", 1.0) is True
    assert await pending.join(None, 1.0) is True


async def test_join_gives_up_at_the_budget_without_killing_the_task():
    """The budget bounds the WAIT, not the work. A slow extraction must still
    land - it is the next turn's patience that ran out, not the writes."""
    finished = asyncio.Event()

    async def slow():
        await asyncio.sleep(0.05)
        finished.set()

    task = pending.spawn("t1", slow())
    assert await pending.join("t1", 0.005) is False
    assert not task.cancelled()
    await pending.drain()
    assert finished.is_set()


async def test_a_failing_task_does_not_raise_into_join():
    async def boom():
        raise RuntimeError("gemini is down")

    pending.spawn("t1", boom())
    assert await pending.join("t1", 1.0) is True


async def test_a_second_spawn_does_not_strand_the_first():
    """Two runs can overlap on one thread - Aegra does not prevent it. The
    older task must keep a strong reference or the GC may take it mid-write."""
    done = []

    async def work(name):
        await asyncio.sleep(0.01)
        done.append(name)

    pending.spawn("t1", work("first"))
    pending.spawn("t1", work("second"))
    await pending.drain()
    assert sorted(done) == ["first", "second"]


async def test_an_anonymous_task_is_still_awaited_by_drain():
    ran = asyncio.Event()

    async def work():
        ran.set()

    pending.spawn(None, work())
    await pending.drain()
    assert ran.is_set()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_memory_pending.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.memory.pending'`

- [ ] **Step 3: Write the registry**

Create `src/eve/memory/pending.py`:

```python
"""Per-thread registry for background memory extraction.

`extract` hands its work here and returns, so a turn ends when Eve stops
talking rather than when her bookkeeping finishes. The next turn on the same
thread joins the pending task before it reads memory, which is what keeps
"detached" from meaning "eventually consistent": the wait lands in the gap
where a member is typing, not in front of the reply they just asked for.

This module imports nothing from the rest of `eve`, so both `extract` (which
spawns) and `recall` (which joins) may depend on it without a cycle.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# thread_id -> the newest extraction still in flight for that thread.
_pending: dict[str, asyncio.Task[None]] = {}
# Tasks not reachable by thread id: those spawned without one, and older
# tasks displaced by a second spawn on the same thread. Held for one reason
# only - asyncio keeps just a WEAK reference to a running task, so a task
# nobody else references can be garbage-collected mid-flight and lose the
# writes it was about to make.
_detached: set[asyncio.Task[None]] = set()


def _log_failure(task: asyncio.Task[None]) -> None:
    """Consume the exception so asyncio does not report it as never-retrieved.

    Extraction already swallows its own failures; this catches anything that
    escapes the coroutine entirely, which would otherwise surface only as a
    "Task exception was never retrieved" line at GC time.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("background extraction failed", exc_info=exc)


def _hold(task: asyncio.Task[None]) -> None:
    _detached.add(task)
    task.add_done_callback(_detached.discard)


def spawn(thread_id: str | None, coro: Coroutine[Any, Any, None]) -> asyncio.Task[None]:
    """Run `coro` in the background, keyed by thread so `join` can find it."""
    task = asyncio.create_task(coro)
    task.add_done_callback(_log_failure)

    if thread_id is None:
        _hold(task)
        return task

    previous = _pending.get(thread_id)
    if previous is not None and not previous.done():
        # Two runs raced on one thread (Aegra does not serialize them), or a
        # join timed out and the turn moved on. Only the newest is joinable
        # by thread id; the older one still needs a reference to survive.
        _hold(previous)

    _pending[thread_id] = task

    def _release(finished: asyncio.Task[None]) -> None:
        # Only clear the slot if it is still OURS. A later spawn may have
        # replaced it, and popping unconditionally would drop a live task's
        # only strong reference.
        if _pending.get(thread_id) is finished:
            del _pending[thread_id]

    task.add_done_callback(_release)
    return task


async def join(thread_id: str | None, budget_s: float) -> bool:
    """Wait for this thread's pending extraction.

    Returns True if there was nothing to wait for or it finished (including
    by failing - a failed extraction is still finished), False if the budget
    ran out. The budget bounds the WAIT, not the work: the task is shielded,
    so giving up on it does not cancel the writes it is partway through.
    """
    task = _pending.get(thread_id) if thread_id else None
    if task is None or task.done():
        return True
    try:
        await asyncio.wait_for(asyncio.shield(task), budget_s)
    except TimeoutError:
        return False
    except Exception:
        # Logged by _log_failure. A broken extraction must not break the
        # turn that merely waited for it.
        return True
    return True


async def drain() -> None:
    """Await every in-flight extraction. For tests and orderly shutdown."""
    tasks = [*_pending.values(), *_detached]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def clear() -> None:
    """Drop every reference without awaiting. Test isolation only."""
    _pending.clear()
    _detached.clear()
```

- [ ] **Step 4: Add the settings**

In `src/eve/settings.py`, insert after line 79 (`memory_digest_every_n_turns: int = 6`), inside the Memory block:

```python
    # Extraction runs after Eve's last token, so its latency never delays a
    # word she says - but it does delay the turn ENDING, because the run is
    # only complete when the graph reaches END. That holds the SSE stream and
    # the client's "done" open for a REFLEX call plus writes. Backgrounding it
    # moves that wait into the gap where the member is typing (ADR 0010).
    memory_extract_background: bool = True
    # How long the next turn waits for the previous turn's extraction before
    # reading memory anyway. Generous next to the 120ms embed budget because
    # it is normally already satisfied - a human had to type in between. When
    # it is not, the degrade is one turn of slightly stale candidates.
    memory_extract_join_budget_ms: int = 5000
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_memory_pending.py -q`
Expected: PASS (7 tests)

- [ ] **Step 6: Verify the settings load**

Run: `uv run python -c "from eve.settings import get_settings; s = get_settings(); print(s.memory_extract_background, s.memory_extract_join_budget_ms)"`
Expected: `True 5000`

- [ ] **Step 7: Commit**

```bash
git add src/eve/memory/pending.py tests/test_memory_pending.py src/eve/settings.py
git commit -m "feat(memory): add a per-thread registry for background extraction"
```

---

### Task 2: Detach extraction behind the registry

**Files:**
- Modify: `src/eve/memory/extract.py:196-238` (split `extract`, add a span)
- Modify: `tests/test_memory_extract.py` (9 call sites + a fixture)
- Modify: `tests/test_skills_integration.py:210` (1 call site)

**Interfaces:**
- Consumes: `pending.spawn`, `pending.drain`, `pending.clear`, `Settings.memory_extract_background` from Task 1.
- Produces: `eve.memory.extract.extract(state, config) -> dict` — unchanged signature, still returns `{}`, now returns before the work is done. `eve.memory.extract._run_extraction(state, config) -> None` — the actual work, awaitable directly by tests.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_memory_extract.py`:

```python
async def test_extract_returns_before_the_work_finishes(monkeypatch, recorded):
    """The point of the whole change: the node returns, the turn ends, and
    the writes land afterwards."""
    from eve.memory import pending
    from eve.memory.types import Extraction

    released = asyncio.Event()

    async def overlapping(sub, subjects, layer, limit=10):
        return []

    class SlowModel:
        def with_structured_output(self, schema):
            return self

        def with_config(self, **_kwargs):
            return self

        async def ainvoke(self, messages):
            await released.wait()
            return Extraction(operations=[
                Operation(op="add", layer="episodic", kind="event",
                          content="Cooper had his shots."),
            ])

    monkeypatch.setattr(extract_mod, "overlapping", overlapping)
    monkeypatch.setattr(extract_mod, "get_model", lambda tier: SlowModel())

    state = {
        "member": MEMBER_SHARED,
        "messages": [HumanMessage("Cooper had his shots."), AIMessage("Noted.")],
    }
    assert await extract_mod.extract(state, {"configurable": {"thread_id": "t1"}}) == {}
    assert recorded["add"] == []  # still blocked in the model call

    released.set()
    await pending.drain()
    assert len(recorded["add"]) == 1


async def test_the_background_flag_off_keeps_extraction_inline(monkeypatch, recorded):
    """The kill switch has to actually switch. With it off the writes must be
    visible the moment the node returns, with no drain."""
    from eve.memory.types import Extraction

    async def overlapping(sub, subjects, layer, limit=10):
        return []

    class FakeModel:
        def with_structured_output(self, schema):
            return self

        def with_config(self, **_kwargs):
            return self

        async def ainvoke(self, messages):
            return Extraction(operations=[
                Operation(op="add", layer="episodic", kind="event",
                          content="Cooper had his shots."),
            ])

    monkeypatch.setattr(extract_mod, "overlapping", overlapping)
    monkeypatch.setattr(extract_mod, "get_model", lambda tier: FakeModel())
    monkeypatch.setattr(
        extract_mod,
        "get_settings",
        lambda: SimpleNamespace(
            memory_extract_background=False,
            memory_digest_every_n_turns=0,
        ),
    )

    state = {
        "member": MEMBER_SHARED,
        "messages": [HumanMessage("Cooper had his shots."), AIMessage("Noted.")],
    }
    await extract_mod.extract(state, {"configurable": {"thread_id": "t1"}})
    assert len(recorded["add"]) == 1


async def test_a_detached_extraction_records_its_own_span(monkeypatch, recorded):
    """Attributes set on an ended span are silently dropped, and the run's
    span HAS ended by the time a detached extraction runs. Without a fresh
    span, eve.authoring.rules_written - the design doc's named signal for
    'authoring never fires' - would read as permanently absent."""
    from eve.memory import pending
    from eve.memory.types import Extraction

    spans = []

    class FakeSpan:
        def set_attribute(self, key, value):
            spans.append((key, value))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeTracer:
        def start_as_current_span(self, name):
            spans.append(("span.name", name))
            return FakeSpan()

    async def overlapping(sub, subjects, layer, limit=10):
        return []

    class FakeModel:
        def with_structured_output(self, schema):
            return self

        def with_config(self, **_kwargs):
            return self

        async def ainvoke(self, messages):
            return Extraction(operations=[])

    monkeypatch.setattr(extract_mod, "overlapping", overlapping)
    monkeypatch.setattr(extract_mod, "get_model", lambda tier: FakeModel())
    monkeypatch.setattr(extract_mod, "_tracer", FakeTracer())

    await extract_mod.extract(
        {"member": MEMBER_SHARED,
         "messages": [HumanMessage("hi"), AIMessage("hello")]},
        {"configurable": {"thread_id": "t1"}},
    )
    await pending.drain()
    assert ("span.name", "eve.extract") in spans
    assert ("eve.authoring.rules_written", 0) in spans
```

Add `import asyncio` to the top of `tests/test_memory_extract.py` (line 1 area, alongside the existing imports), and add this autouse fixture just below the `recorded` fixture:

```python
@pytest.fixture(autouse=True)
def _clean_pending():
    """No background extraction may leak from one test into the next."""
    from eve.memory import pending

    pending.clear()
    yield
    pending.clear()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_memory_extract.py -q -k "returns_before or background_flag or own_span"`
Expected: FAIL — `test_extract_returns_before_the_work_finishes` fails because `extract` still awaits its work (the `assert recorded["add"] == []` trips, or it deadlocks on `released` — either is the expected red); `own_span` fails with `AttributeError: _tracer`.

- [ ] **Step 3: Split the node and add the span**

In `src/eve/memory/extract.py`, add the tracer near the module logger (after line 29, `logger = logging.getLogger(__name__)`):

```python
_tracer = trace.get_tracer("eve.memory.extract")
```

Add the import alongside the other `eve.memory` imports (after line 24):

```python
from eve.memory import pending
```

Replace the whole of `extract` (currently lines 196-238) with:

```python
async def extract(state: dict, config: RunnableConfig) -> dict:
    """End the turn, then do the bookkeeping.

    The node returns `{}` immediately and the real work runs in the
    background, because the run is only complete when the graph reaches END -
    an in-graph extraction holds the SSE stream, and so the client's "done",
    open for a REFLEX call plus embeddings plus writes. `recall` joins this
    task on the next turn before it reads memory, so detaching costs no
    ordering (ADR 0010).
    """
    if not get_settings().memory_extract_background:
        await _run_extraction(state, config)
        return {}
    thread_id = config.get("configurable", {}).get("thread_id")
    pending.spawn(thread_id, _run_extraction(state, config))
    return {}


async def _run_extraction(state: dict, config: RunnableConfig) -> None:
    """Extract memory after Eve's answer without allowing failures to fail a turn.

    Opens its OWN span rather than writing to the ambient one. When this runs
    detached, the run's span has already ended, and OpenTelemetry silently
    drops attributes set on an ended span - every `eve.extract.*` and
    `eve.authoring.*` number would read as absent. The task inherits the
    context at creation time, so this span still parents correctly.
    """
    member = state["member"]
    thread_id = config.get("configurable", {}).get("thread_id")
    run_id = config.get("configurable", {}).get("run_id")
    human, ai = _last_exchange(state["messages"])
    if not human:
        return

    with _tracer.start_as_current_span("eve.extract") as span:
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
            result = await model.with_config(tags=[TAG_NOSTREAM]).ainvoke(
                [HumanMessage(prompt)]
            )
            rule_ids = {m.id for m in candidates if getattr(m, "layer", None) == "rule"}
            operations, rejected = _filter_authored(
                list(result.operations), human, rule_ids
            )
            counts = await apply_operations(operations, member, thread_id, run_id)
            rules_written = sum(
                1 for op in operations if getattr(op, "layer", None) == "rule"
            )
        except Exception:
            logger.warning("extraction failed for thread %s", thread_id, exc_info=True)
            span.set_attribute("eve.extract.failed", True)
            return

        for op_name in ("add", "supersede", "reinforce", "forget", "evict"):
            span.set_attribute(f"eve.extract.ops.{op_name}", counts.get(op_name, 0))
        # Design doc section 9: the plausible failure of this phase is that
        # authoring never fires at all. These two numbers are how that is
        # detected, and how a firing guard is distinguished from a silent model.
        span.set_attribute("eve.authoring.rules_written", rules_written)
        span.set_attribute("eve.authoring.rules_rejected", rejected)

    await _maybe_refresh_digest(state, thread_id)
    await _mark_replied_if_a_reply(human, thread_id)
```

- [ ] **Step 4: Drain at the existing call sites**

`extract` no longer does the work inline, so the 10 existing call sites must drain. In `tests/test_memory_extract.py`, add `await pending.drain()` immediately after each of the 9 `extract_mod.extract(...)` calls (lines 169, 198, 238, 273, 406, 440, 493, 542, 591 pre-edit). For the two that assert on the return value, keep the assertion and drain after:

```python
    assert await extract_mod.extract(state, {"configurable": {}}) == {}
    await pending.drain()
```

For `_run_extract` (the shared helper, pre-edit line 273), the return must survive the drain:

```python
    result = await extract_mod.extract(state, {"configurable": {"thread_id": "t1"}})
    await pending.drain()
    return result
```

Add `from eve.memory import pending` to the imports at the top of the file.

In `tests/test_skills_integration.py:210`, do the same:

```python
    await extract_mod.extract(state, {"configurable": {"thread_id": "t1"}})
    await pending.drain()
```

adding `from eve.memory import pending` to that file's imports.

- [ ] **Step 5: Verify no circular import**

`eve/memory/__init__.py` does `from eve.memory.extract import extract`, so `extract.py` now runs `from eve.memory import pending` while the `eve.memory` package is still partially initialized. Python resolves this via its submodule fallback, but it is exactly the kind of thing that works until someone reorders an import, so pin it:

Run: `uv run python -c "import eve.memory; import eve.graph; from eve.memory import pending; print('ok', pending.join)"`
Expected: `ok <function join at ...>` — not `ImportError: cannot import name 'pending' from partially initialized module`.

If it does fail, use `from eve.memory import pending as pending` → replace with a late import inside `extract()` rather than restructuring `__init__.py`.

- [ ] **Step 6: Run the full extract and skills suites**

Run: `uv run pytest tests/test_memory_extract.py tests/test_skills_integration.py -q`
Expected: PASS (31 in `test_memory_extract.py` — the original 28 plus 3 new — and the skills suite unchanged)

- [ ] **Step 7: Commit**

```bash
git add src/eve/memory/extract.py tests/test_memory_extract.py tests/test_skills_integration.py
git commit -m "feat(memory): run extraction in the background instead of in the graph"
```

---

### Task 3: Join the pending extraction before recall reads memory

**Files:**
- Modify: `src/eve/memory/recall.py:71-96` (join before the reads)
- Modify: `tests/test_memory_recall.py` (ordering and budget tests)

**Interfaces:**
- Consumes: `pending.join`, `Settings.memory_extract_join_budget_ms` from Task 1; the detached spawn from Task 2.
- Produces: the span attribute `eve.recall.extract_joined: bool` — `False` when the join gave up at its budget, which is the number that says whether the join is ever actually costing anything.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_memory_recall.py`:

```python
async def test_recall_joins_the_pending_extraction_before_reading(monkeypatch, wired):
    """The previous turn's writes must be visible to this turn's reads, or
    'what did I just tell you' misses the thing it was just told."""
    order = []

    async def fake_join(thread_id, budget_s):
        order.append(("join", thread_id))
        return True

    original = recall_mod.load_always_on

    async def tracking_always_on(sub, thread_id, *, include_rules=False):
        order.append(("read", thread_id))
        return await original(sub, thread_id, include_rules=include_rules)

    monkeypatch.setattr(recall_mod.pending, "join", fake_join)
    monkeypatch.setattr(recall_mod, "load_always_on", tracking_always_on)

    await memory_recall(_state(), CONFIG)
    assert order == [("join", "t1"), ("read", "t1")]


async def test_recall_joins_with_the_configured_budget(monkeypatch, wired):
    seen = {}

    async def fake_join(thread_id, budget_s):
        seen["budget"] = budget_s
        return True

    monkeypatch.setattr(recall_mod.pending, "join", fake_join)
    monkeypatch.setattr(
        recall_mod,
        "get_settings",
        lambda: SimpleNamespace(
            memory_extract_join_budget_ms=2500,
            memory_token_budget=1200,
            self_authoring_enabled=False,
            memory_recall_embed_budget_ms=120,
        ),
    )

    await memory_recall(_state(), CONFIG)
    assert seen["budget"] == 2.5


async def test_a_stalled_extraction_does_not_hang_the_turn(monkeypatch, wired):
    """A degraded turn is a complete turn. If the previous extraction is
    wedged, this turn ships with slightly stale candidates rather than
    waiting on it forever."""
    async def fake_join(thread_id, budget_s):
        return False

    monkeypatch.setattr(recall_mod.pending, "join", fake_join)

    result = await memory_recall(_state(), CONFIG)
    assert result["memory"] is not None
    assert result["memory"].profile  # the always-on layers are untouched
```

Add `from types import SimpleNamespace` to the imports at the top of `tests/test_memory_recall.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_memory_recall.py -q -k "joins or stalled"`
Expected: FAIL — `AttributeError: module 'eve.memory.recall' has no attribute 'pending'`

- [ ] **Step 3: Add the join**

In `src/eve/memory/recall.py`, add to the imports (after line 23, `from eve.memory.embed import embed_query`):

```python
from eve.memory import pending
```

Add the budget helper next to `_budget_seconds` (after line 50):

```python
def _join_budget_seconds() -> float:
    return get_settings().memory_extract_join_budget_ms / 1000.0
```

In `recall`, insert the join between `query = _last_human_text(...)` (line 76) and the `embed_task = (` block (line 81), so it reads:

```python
    query = _last_human_text(state["messages"])

    # Join the previous turn's background extraction before anything reads
    # memory. This is what makes detaching extraction free rather than
    # eventually-consistent: turn N's facts are visible to turn N+1, and the
    # wait normally costs nothing because a member had to type in between.
    #
    # BEFORE the embedding task, not after: the embed budget is measured from
    # task creation (see _embed_within_budget), so a join started first would
    # burn the vector arm's whole 120ms on every turn that actually waited.
    joined = await pending.join(thread_id, _join_budget_seconds())

    # Start the clock on the embedding BEFORE the lexical query, so the two
    # overlap. The lexical round trip is a few milliseconds of the budget the
    # embedding would otherwise have had entirely to itself.
    embed_task = (
        asyncio.create_task(_embed_within_budget(query))
        if query.strip()
        else None
    )
```

Thread `joined` through to the span. Change the `_record_span` call (line 131) to:

```python
        _record_span(profile, household, episodic, rules, vector_used, latency_ms, joined)
```

and extend `_record_span`'s signature (line 155) and body:

```python
def _record_span(
    profile: list[Memory],
    household: list[Memory],
    episodic: list[Memory],
    rules: list[Memory],
    vector_used: bool,
    latency_ms: float,
    joined: bool,
) -> None:
```

Add inside `_record_span`, next to the other attributes:

```python
    # False means this turn gave up waiting on the previous turn's extraction
    # and may be reading slightly stale candidates. If this is ever non-zero
    # in practice, the join budget is too tight or extraction is too slow.
    span.set_attribute("eve.recall.extract_joined", joined)
```

- [ ] **Step 4: Run the recall suite**

Run: `uv run pytest tests/test_memory_recall.py -q`
Expected: PASS (all existing tests plus the 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/eve/memory/recall.py tests/test_memory_recall.py
git commit -m "feat(memory): join the pending extraction before recall reads"
```

---

### Task 4: End-to-end proof, ADR, and docs

**Files:**
- Modify: `tests/test_memory_recall.py` (one end-to-end ordering test, no mocked `join`)
- Create: `docs/adr/0010-extraction-is-detached-and-joined.md`
- Modify: `docs/architecture.md` (the graph description and the import graph)
- Modify: `src/eve/graph.py:1-16` (the module docstring's claim about `extract`)

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: no new code interfaces. This task pins the whole-graph invariant and records the decision.

- [ ] **Step 1: Write the end-to-end ordering test**

Task 3's tests monkeypatch `pending.join`, so they prove the wiring but not the behaviour. This one spawns a real task and mocks nothing — it is the test that catches the join silently becoming a no-op. Append to `tests/test_memory_recall.py` (it already has the `wired` fixture, `_state`, `CONFIG`, and `import asyncio`):

```python
async def test_a_real_pending_extraction_delays_the_next_turns_read(
    monkeypatch, wired
):
    """The invariant the whole design rests on, with nothing mocked out: a
    real extraction is in flight and `recall` must not read past it. This is
    what would catch detaching quietly becoming fire-and-forget."""
    from eve.memory import pending

    events = []

    async def slow_write():
        await asyncio.sleep(0.02)
        events.append("write")

    original = recall_mod.load_always_on

    async def tracking(sub, thread_id, *, include_rules=False):
        events.append("read")
        return await original(sub, thread_id, include_rules=include_rules)

    monkeypatch.setattr(recall_mod, "load_always_on", tracking)
    pending.clear()
    try:
        pending.spawn("t1", slow_write())
        await memory_recall(_state(), CONFIG)
        assert events == ["write", "read"]
    finally:
        pending.clear()
```

- [ ] **Step 2: Run it, then prove it is a real guard**

Run: `uv run pytest tests/test_memory_recall.py -q -k "real_pending"`
Expected: PASS with Tasks 1-3 applied.

A passing test that would also pass without the feature is worthless, so confirm it bites: temporarily comment out the `joined = await pending.join(...)` line in `src/eve/memory/recall.py` (and hardcode `joined = True` so the file still runs), re-run, and check it FAILS with `assert ['read', 'write'] == ['write', 'read']`. Restore the line.

- [ ] **Step 3: Write the ADR**

Create `docs/adr/0010-extraction-is-detached-and-joined.md`:

```markdown
# 10. Memory extraction is detached from the turn and joined by the next one

**Status:** Accepted
**Date:** 2026-08-28

## Context

`extract` ran as a graph node between `eve` and `END`. Its latency never
delayed a word Eve said - `eve` has already finished streaming by the time it
starts - but it did delay the turn *ending*. A LangGraph run is complete only
when the graph reaches `END`, so an in-graph extraction holds the SSE stream,
and therefore the client's "done", open for a REFLEX model call plus
embeddings plus writes, and a second REFLEX call for the digest on every sixth
turn. In `scripts/chat.py` this shows up as the `you>` prompt hanging after
Eve has visibly finished; in a UI it is a spinner that will not stop.

Aegra does not serialize runs per thread - there is no `multitask_strategy`
and no conflict rejection, and the thread's `busy` status is a field, not a
lock - so this was never a hard block on the next turn. It is a block on the
turn looking finished, which a human client pays before every message anyway.

The obvious fix, fire-and-forget, trades that latency for a correctness
problem: turn N+1's `recall` could read memory before turn N's writes land, so
"what did I just tell you" would miss the thing it was just told. That is the
same failure ADR 0002 rejected concurrent recall for, one turn later.

## Decision

Extraction runs in a background task, registered per thread in
`eve.memory.pending`. `recall` joins that thread's pending task before it
reads memory.

The wait therefore moves off the reply path and into the gap where a member is
typing their next message. Ordering is preserved exactly: turn N+1 cannot read
memory that turn N's writes have not reached. In the common case the join
costs nothing, because a human took longer to type than Gemini took to
extract.

The join is bounded by `EVE_MEMORY_EXTRACT_JOIN_BUDGET_MS` (default 5000) and
degrades the same way the embedding arm does: if the budget runs out, the turn
proceeds with slightly stale candidates rather than hanging.
`eve.recall.extract_joined` in Langfuse reports how often that happens.
`EVE_MEMORY_EXTRACT_BACKGROUND=false` puts extraction back in the graph.

The budget bounds the *wait*, not the *work*: the joined task is shielded, so
a turn giving up on it does not cancel the writes it is partway through.

## Consequences

The registry holds strong references to every task it spawns. `asyncio` keeps
only a weak one, so an unreferenced task can be garbage-collected mid-flight
and lose its writes - the single most likely way for this design to fail
silently.

A detached extraction opens its own OpenTelemetry span. The run's span has
ended by the time it runs, and OTel silently drops attributes set on an ended
span, so without this every `eve.extract.*` and `eve.authoring.*` number -
including `eve.authoring.rules_written`, the named signal for "authoring never
fires at all" - would read as permanently absent.

**Accepted loss:** an extraction in flight when the process stops is lost.
That costs one turn's facts, in a path that already tolerates total failure
(extraction catches everything and returns). Draining on shutdown would need a
lifespan hook `eve` does not own - Aegra owns the app - and is not worth
standing one up for this. Revisit if deploys become frequent enough to notice.

The pool is `max_size=5` (`memory/db.py`). Detached extractions each take a
connection, so a burst of concurrent turns contends with recall for it. Not a
problem at family scale; it is the number to look at first if it becomes one.
```

- [ ] **Step 4: Update the graph docstring**

In `src/eve/graph.py`, replace this sentence in the module docstring (line 7-8):

```
`extract` runs after the answer has streamed, so its
latency is invisible.
```

with:

```
`extract` runs after the answer has streamed and hands its
work to a background task, so it delays neither the answer nor the end of the
turn; the next turn on the thread joins it before reading memory (ADR 0010).
```

- [ ] **Step 5: Update the architecture doc**

Two edits in `docs/architecture.md`.

Replace lines 42-45:

```markdown
- **`extract`** (`src/eve/memory/extract.py`) runs after the answer has streamed.
  The `REFLEX` model produces structured add, reinforce, supersede, and forget
  operations; valid writes, digest refresh, embeddings, and cap eviction are
  applied best-effort so extraction failure cannot erase a completed answer.
```

with:

```markdown
- **`extract`** (`src/eve/memory/extract.py`) runs after the answer has streamed,
  and hands its work to a background task rather than doing it in the graph — a
  run is complete only at `END`, so an in-graph extraction held the client's
  stream open for a model call plus writes. The `REFLEX` model produces
  structured add, reinforce, supersede, and forget operations; valid writes,
  digest refresh, embeddings, and cap eviction are applied best-effort so
  extraction failure cannot erase a completed answer. The next turn on the
  thread joins the pending task in `recall` before reading memory, so detaching
  costs no ordering — see [ADR 0010](adr/0010-extraction-is-detached-and-joined.md).
```

Then replace lines 112-113:

```markdown
`embed`/`ranking`/`store`/`types` -> `recall`, while `extract` depends on
`embed`, `store`, `types`, `models`, and `settings`.
```

with:

```markdown
`embed`/`ranking`/`store`/`types`/`pending` -> `recall`, while `extract` depends
on `embed`, `store`, `types`, `pending`, `models`, and `settings`. `pending`
imports nothing internal, which is what lets both `recall` and `extract` depend
on it without a cycle.
```

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q -x --ignore=tests/test_live_models.py --ignore=tests/test_ambient_live.py`
Expected: PASS, no regressions. (The two ignored files hit live model endpoints.)

- [ ] **Step 7: Commit**

```bash
git add tests/test_memory_recall.py docs/adr/0010-extraction-is-detached-and-joined.md docs/architecture.md src/eve/graph.py
git commit -m "docs(memory): ADR 0010 and the end-to-end ordering guard for detached extraction"
```

---

## Manual verification

After Task 4, confirm the change does what it was built to do:

```bash
uv run aegra dev          # in one terminal
uv run python scripts/chat.py   # in another
```

Send a message. The `you>` prompt should return as soon as Eve's last token
lands, rather than a beat or two later. Send a second message immediately and
confirm Eve still knows what you told her in the first — that is the join
doing its job. Then check Langfuse for `eve.extract.ops.*` and
`eve.authoring.rules_written` on the detached span: if those are missing, the
span fix in Task 2 regressed.
