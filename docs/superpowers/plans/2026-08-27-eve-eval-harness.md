# Eve Phase 5b — Eval Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A command that answers "did that change make Eve worse," whose centrepiece is an A/B measuring what Eve's self-authored rule set is actually worth.

**Architecture:** Datasets are built from Eve's own Postgres tables (never from parsed Langfuse traces), replayed through the real code paths — `filter.judge()` for ambient decisions, the compiled graph for conversational turns — and scored either exactly (free) or by a `REFLEX`-tier judge (metered, so it never touches the subscription budget). Scores land in `eve_eval_run` locally and are published to Langfuse best-effort; the regression gate reads only Postgres, so it works with Langfuse down.

**Tech Stack:** Python 3.12, `langfuse` SDK (new dependency), psycopg 3, LangGraph, pytest with `asyncio_mode = "auto"`.

**Spec:** [`docs/superpowers/specs/2026-08-27-eve-eval-harness-design.md`](../specs/2026-08-27-eve-eval-harness-design.md)

## Global Constraints

- **Phase 5a must be merged first.** This plan modifies `eve/context.py`'s `build_system_prompt`, reads `rule`-layer memory, and reuses `eve.state.is_ambient_text`. All three arrive in 5a.
- **`MIGRATIONS` ends at exactly 5 entries.** All three schema changes go in one entry, `0005_eval`. Phase 5c crosses the threshold and moves to Alembic; this plan must not.
- **No production code path may import `eve.eval`.** Task 13 asserts it. The two production edits this phase makes live *outside* the package: `record_decision` in `eve_ambient/store.py`, and the `replied_at` UPDATE in `eve/memory/extract.py`.
- **The judge runs on `Tier.REFLEX`, never `DEEP`.** `REFLEX` is the metered Gemini route; every other tier is a subscription proxy sharing a `max_budget: 20` per 30 days with Noah's own work.
- **The gate never calls Langfuse.** A publish failure is logged and ignored.
- **Replay must never write to production memory.** `build_graph(extract_fn=...)` takes an async callable; replay passes a no-op.
- **`eve-eval run` prints its estimated `VOICE`-call count first** and refuses to exceed `EVE_EVAL_VOICE_CALL_CEILING` (60) without `--yes`.
- **Thresholds:** regression fails at more than `EVE_EVAL_REGRESSION_POINTS` (10) points dropped, except `audience_exact`, which fails on any drop, and `rule_delta`, which fails when negative.
- **Test tiers.** Unit by default; DB tests marked `integration` against `postgresql://eve:eve@127.0.0.1:15432/eve`.

---

## File Structure

**Created:**
- `src/eve/eval/__init__.py`, `types.py`, `datasets.py`, `replay.py`, `scorers.py`, `store.py`, `publish.py`, `cli.py`, `hygiene.py`
- `tests/eval/turns.yaml` — the hand-authored golden file, including one canary
- `tests/test_eval_datasets.py`, `test_eval_replay.py`, `test_eval_scorers.py`, `test_eval_gate.py`, `test_eval_hygiene.py`, `test_eval_integration.py`
- `docs/adr/0009-eval-inputs-from-postgres.md`

**Modified:**
- `src/eve/memory/db.py` — migration `0005_eval`
- `src/eve_ambient/store.py` — `record_decision`, `decisions_since`, `prune_decisions`
- `src/eve_ambient/pipeline.py` — one `record_decision` call
- `src/eve/memory/extract.py` — the `replied_at` UPDATE
- `src/eve/context.py` — `build_system_prompt(..., suppress_rules=False)`
- `src/eve/settings.py`, `pyproject.toml`, `.env.example`, `README.md`, `docs/architecture.md`

---

## Task 1: Migration `0005_eval`

**Files:**
- Modify: `src/eve/memory/db.py`
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Produces: `eve_ambient_notice.replied_at`, tables `eve_ambient_decision` and `eve_eval_run`. `MIGRATIONS` has 5 entries.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_memory_store.py`:

```python
async def test_migration_count_is_five(pool):
    """Exactly at db.py's stated Alembic threshold. Phase 5c crosses it; this
    phase folds three changes into one entry to stay here."""
    from eve.memory import db

    assert len(db.MIGRATIONS) == 5


async def test_the_eval_tables_exist(pool):
    async with pool.connection() as conn:
        for table in ("eve_ambient_decision", "eve_eval_run"):
            cur = await conn.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            assert (await cur.fetchone())[0] == table


async def test_notice_has_replied_at(pool):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='eve_ambient_notice' AND column_name='replied_at'"
        )
        assert await cur.fetchone() is not None
```

- [ ] **Step 2: Run to verify failure**

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest tests/test_memory_store.py -m integration -k "migration_count or eval_tables or replied_at" -v
```
Expected: FAIL — `len(MIGRATIONS) == 4`, `to_regclass` returns `None`.

- [ ] **Step 3: Add the migration entry**

Append to `MIGRATIONS` in `src/eve/memory/db.py`:

```python
    (
        "0005_eval",
        """
        -- Phase 5b. Three changes in ONE entry deliberately: db.py's own
        -- guidance says move to Alembic past ~5 entries, and Phase 5c is
        -- where that happens. Splitting these into three would cross the
        -- line here instead, for no benefit.

        -- The reply IS the label for notification precision (design 5): a
        -- member who answers found the interruption worth receiving.
        -- Populated only from this deploy forward; earlier rows stay
        -- permanently unlabelled and are excluded from the dataset.
        ALTER TABLE eve_ambient_notice
          ADD COLUMN IF NOT EXISTS replied_at timestamptz;

        -- Every filter verdict, with the Signal that produced it. Phase 4
        -- records neither: eve_ambient_seen keeps only (source, key), and
        -- eve_ambient_notice keeps no signal content, so a replayable
        -- dataset item cannot be reconstructed from either (design 4.2).
        CREATE TABLE IF NOT EXISTS eve_ambient_decision (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source     text        NOT NULL,
          key        text        NOT NULL,
          signal     jsonb       NOT NULL,
          verdict    jsonb       NOT NULL,
          decided_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS eve_ambient_decision_decided
          ON eve_ambient_decision (decided_at DESC);

        -- Local and authoritative. The gate reads this, never Langfuse, so a
        -- reporting outage cannot block a regression check (design 7.1).
        -- `scores` is jsonb because the scorer set will change and a
        -- migration per metric is machinery for a table read by one CLI.
        CREATE TABLE IF NOT EXISTS eve_eval_run (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          dataset     text        NOT NULL,
          arm         text        NOT NULL DEFAULT 'with-rules',
          git_sha     text,
          item_count  integer     NOT NULL,
          scores      jsonb       NOT NULL,
          created_at  timestamptz NOT NULL DEFAULT now()
        );

        CREATE INDEX IF NOT EXISTS eve_eval_run_dataset_created
          ON eve_eval_run (dataset, arm, created_at DESC);
        """,
    ),
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_memory_store.py -m integration -v`
Expected: PASS, including the pre-existing `test_migrate_is_idempotent`.

- [ ] **Step 5: Commit**

```bash
git add src/eve/memory/db.py tests/test_memory_store.py
git commit -m "feat(5b): migration 0005_eval - decisions, runs, and the reply label"
```

---

## Task 2: Settings and the `langfuse` dependency

**Files:**
- Modify: `src/eve/settings.py`, `pyproject.toml`
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `eval_dataset_limit=200`, `eval_voice_call_ceiling=60`, `eval_regression_points=10`, `eval_dead_rule_days=90`, `eval_decision_retention_days=180`, `eval_hygiene_apply_enabled=False`, `langfuse_host="https://langfuse.chalifour.dev"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_settings.py`:

```python
def test_eval_defaults():
    from eve.settings import Settings

    s = Settings()
    assert s.eval_dataset_limit == 200
    assert s.eval_voice_call_ceiling == 60
    assert s.eval_regression_points == 10
    assert s.eval_dead_rule_days == 90
    assert s.eval_decision_retention_days == 180
    assert s.eval_hygiene_apply_enabled is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_settings.py -k eval_defaults -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Add the settings**

In `src/eve/settings.py`, after the Phase 5a block:

```python
    # Phase 5b (Eval harness). See docs/superpowers/specs/
    # 2026-08-27-eve-eval-harness-design.md section 9.2.
    eval_dataset_limit: int = 200
    # Above this many VOICE-tier calls, `eve-eval run` requires --yes. Both
    # subscription proxies share a max_budget of 20 per 30 days with Noah's
    # own work; a harness that can silently spend the month is one that will.
    eval_voice_call_ceiling: int = 60
    eval_regression_points: int = 10
    eval_dead_rule_days: int = 90
    eval_decision_retention_days: int = 180
    eval_hygiene_apply_enabled: bool = False
    langfuse_host: str = "https://langfuse.chalifour.dev"
```

- [ ] **Step 4: Add the dependency**

Add `"langfuse>=3.0.0",` to `dependencies` in `pyproject.toml`, alphabetically
between `icalendar` and `langchain`. Then:

```bash
uv sync
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_settings.py -v && uv run python -c "import langfuse; print(langfuse.__version__)"`
Expected: PASS and a version string.

- [ ] **Step 6: Commit**

```bash
git add src/eve/settings.py pyproject.toml uv.lock tests/test_settings.py
git commit -m "feat(5b): eval settings and the langfuse SDK dependency"
```

---

## Task 3: Record every filter decision

**Files:**
- Modify: `src/eve_ambient/store.py`, `src/eve_ambient/pipeline.py`
- Test: `tests/test_ambient_pipeline.py`, `tests/test_ambient_store.py`

**Interfaces:**
- Consumes: Task 1's table.
- Produces: `store.record_decision(signal, verdict) -> None`, `store.decisions_since(since, limit) -> list[dict]`, `store.prune_decisions(days) -> int`.

The insert goes immediately after `judge()` returns, **before** the gate chain.
The label is the filter's verdict, not the eventual outcome: a signal the
filter approved and the daily cap then suppressed is still a `notify=true`
decision, and scoring it as a suppression would measure the cap instead of the
filter.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ambient_pipeline.py` (follow the existing fixture style in
that file for stubbing `judge`, `store`, and `deliver`):

```python
async def test_a_verdict_is_recorded_once(monkeypatch, pipeline_stubs):
    """One row per judged signal, carrying the whole Signal so the dataset
    item is replayable."""
    recorded = pipeline_stubs["decisions"]
    await _handle(monkeypatch, notify=False, why="not worth it")

    assert len(recorded) == 1
    signal, verdict = recorded[0]
    assert signal.summary
    assert verdict.notify is False


async def test_a_capped_signal_still_records_notify_true(monkeypatch, pipeline_stubs):
    """The dataset measures the filter, not the gates. If the cap rewrote the
    label, the harness would score the wrong component."""
    recorded = pipeline_stubs["decisions"]
    await _handle(monkeypatch, notify=True, why="worth it", over_daily_cap=True)

    assert recorded[0][1].notify is True


async def test_nothing_is_recorded_for_a_stale_signal(monkeypatch, pipeline_stubs):
    """`stale` resolves before the filter runs: there is no decision."""
    recorded = pipeline_stubs["decisions"]
    await _handle(monkeypatch, fresh=False)

    assert recorded == []


async def test_nothing_is_recorded_when_the_filter_errors(monkeypatch, pipeline_stubs):
    """A FilterError is a couldn't-decide. Recording it as a decision would
    put an un-labelled item in the dataset."""
    recorded = pipeline_stubs["decisions"]
    await _handle(monkeypatch, filter_raises=True)

    assert recorded == []


async def test_a_recording_failure_does_not_break_the_pipeline(monkeypatch, pipeline_stubs):
    """Best-effort, like every other non-essential write here: losing an eval
    row must never cost a notification."""
    async def boom(signal, verdict):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(pipeline_mod.store, "record_decision", boom)
    outcome = await _handle(monkeypatch, notify=False, why="no")

    assert outcome == "filtered"
```

> `pipeline_stubs` and `_handle` are helpers to add alongside the existing
> fixtures in `tests/test_ambient_pipeline.py`. `pipeline_stubs` monkeypatches
> `store.is_fresh`, `store.mark_seen`, `store.record_decision` (appending
> `(signal, verdict)` to a list), `filter.judge`, and `notify.deliver`, and
> returns the dict of captured calls. `_handle` builds a `Signal` and calls
> `pipeline.handle_signal` with those stubs configured per keyword.

Add to `tests/test_ambient_store.py`:

```python
async def test_record_and_read_back_a_decision(pool):
    from datetime import UTC, datetime

    from eve_ambient.store import decisions_since, record_decision
    from eve_ambient.types import FilterVerdict, Signal

    signal = Signal(
        source="mail", key="k1", occurred_at=datetime(2026, 8, 27, tzinfo=UTC),
        member_sub="sub-noah", summary="A package shipped.",
        payload={"from": "shop"},
    )
    await record_decision(signal, FilterVerdict(notify=True, audience=["sub-noah"], why="w"))

    rows = await decisions_since(datetime(2026, 1, 1, tzinfo=UTC), limit=10)
    assert len(rows) == 1
    assert rows[0]["signal"]["summary"] == "A package shipped."
    assert rows[0]["verdict"]["notify"] is True


async def test_prune_decisions_respects_the_window(pool):
    from datetime import UTC, datetime, timedelta

    from eve_ambient.store import prune_decisions

    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_ambient_decision (source, key, signal, verdict, decided_at)"
            " VALUES ('mail','old','{}','{}', now() - interval '400 days')"
        )
        await conn.execute(
            "INSERT INTO eve_ambient_decision (source, key, signal, verdict)"
            " VALUES ('mail','new','{}','{}')"
        )
    assert await prune_decisions(180) == 1
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_ambient_decision")
        assert (await cur.fetchone())[0] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ambient_pipeline.py -k record -v`
Expected: FAIL — `store` has no `record_decision`.

- [ ] **Step 3: Add the store functions**

Append to `src/eve_ambient/store.py`:

```python
async def record_decision(signal: Signal, verdict: FilterVerdict) -> None:
    """One row per judged signal, for Phase 5b's dataset.

    The verdict, NOT the eventual outcome: a signal the filter approved and
    the daily cap then suppressed is still a notify=true decision. Scoring
    the outcome would measure the gate chain instead of the filter
    (eval design 4.2).
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_ambient_decision (source, key, signal, verdict)"
            " VALUES (%s, %s, %s, %s)",
            (
                signal.source,
                signal.key,
                Jsonb(
                    {
                        "source": signal.source,
                        "key": signal.key,
                        "occurred_at": signal.occurred_at.isoformat(),
                        "member_sub": signal.member_sub,
                        "summary": signal.summary,
                        "payload": signal.payload,
                        "cooldown_hours": signal.cooldown_hours,
                    }
                ),
                Jsonb(verdict.model_dump()),
            ),
        )


async def decisions_since(since: datetime, limit: int) -> list[dict]:
    """Newest first, joined to whether the notification earned a reply.

    LEFT JOIN, not INNER: a notify=false decision has no notice row, and
    dropping those would leave the dataset with only the positives.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT d.id, d.source, d.key, d.signal, d.verdict, d.decided_at,
                       bool_or(n.replied_at IS NOT NULL) AS replied,
                       count(n.id) AS notices
                  FROM eve_ambient_decision d
                  LEFT JOIN eve_ambient_notice n
                    ON n.source = d.source AND n.key = d.key
                 WHERE d.decided_at >= %(since)s
                 GROUP BY d.id
                 ORDER BY d.decided_at DESC
                 LIMIT %(limit)s
                """,
                {"since": since, "limit": limit},
            )
            return list(await cur.fetchall())


async def prune_decisions(days: int) -> int:
    """Retention. One row per judged signal at a five-minute poll across four
    sources grows without bound, and a year-old signal is not measuring the
    current filter anyway."""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM eve_ambient_decision"
            f" WHERE decided_at < now() - interval '{int(days)} days'"
        )
        return cur.rowcount
```

Add the imports this needs to the top of `src/eve_ambient/store.py`:

```python
from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve_ambient.types import FilterVerdict, Signal
```

> `eve_ambient/store.py` previously depended only on `eve.memory.db` and not
> on `eve_ambient.types` (see `docs/architecture.md`'s import-graph paragraph).
> This adds that edge, which is acyclic — `types` imports nothing internal.
> Update the architecture doc's import paragraph in Task 13.

- [ ] **Step 4: Call it from the pipeline**

In `src/eve_ambient/pipeline.py`, immediately after the `judge` try/except
block and before the `if not verdict.notify` check:

```python
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

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_ambient_pipeline.py -v && uv run pytest tests/test_ambient_store.py -m integration -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve_ambient/store.py src/eve_ambient/pipeline.py tests/test_ambient_pipeline.py tests/test_ambient_store.py
git commit -m "feat(5b): record every ambient filter verdict with its signal"
```

---

## Task 4: The reply label

**Files:**
- Modify: `src/eve/memory/extract.py`
- Test: `tests/test_memory_extract.py`

**Interfaces:**
- Consumes: Phase 5a's `eve.state.is_ambient_text`.
- Produces: `extract` stamps `eve_ambient_notice.replied_at` for a member turn in an ambient thread.

No lookup first: a thread with no matching row is not an ambient thread and the
UPDATE affects nothing.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_memory_extract.py`:

```python
async def test_a_member_turn_in_an_ambient_thread_stamps_replied_at(monkeypatch, recorded):
    stamped = []

    async def mark_replied(thread_id):
        stamped.append(thread_id)

    monkeypatch.setattr(extract_mod, "mark_replied", mark_replied)
    await _run_extract(monkeypatch, [], "Thanks, I'll move it.", MEMBER_SHARED)

    assert stamped == ["t1"]


async def test_an_ambient_turn_does_not_stamp_replied_at(monkeypatch, recorded):
    """Eve's own opening message is not a reply to herself."""
    from eve.state import ambient_marker

    stamped = []

    async def mark_replied(thread_id):
        stamped.append(thread_id)

    monkeypatch.setattr(extract_mod, "mark_replied", mark_replied)
    await _run_extract(
        monkeypatch, [], ambient_marker("Noah") + "\nYour 3pm moved.", MEMBER_SHARED
    )

    assert stamped == []


async def test_a_stamp_failure_does_not_fail_the_turn(monkeypatch, recorded):
    async def mark_replied(thread_id):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(extract_mod, "mark_replied", mark_replied)
    out = await _run_extract(monkeypatch, [], "Thanks.", MEMBER_SHARED)

    assert out == {}
```

> `_run_extract` is the helper added in Phase 5a's Task 6.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_memory_extract.py -k replied -v`
Expected: FAIL — `extract_mod` has no attribute `mark_replied`.

- [ ] **Step 3: Add the store function**

Append to `src/eve_ambient/store.py`:

```python
async def mark_replied(thread_id: str) -> None:
    """A member speaking in an ambient thread IS the label (eval design 5).

    No lookup first: a thread with no matching row is not an ambient thread,
    and the UPDATE affects nothing.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_ambient_notice SET replied_at = now()"
            " WHERE thread_id = %s AND replied_at IS NULL",
            (thread_id,),
        )
```

- [ ] **Step 4: Call it from `extract`**

`eve/memory/extract.py` must not import `eve_ambient` — the dependency runs the
other way. Import lazily inside the helper, and add it to `extract.py`:

```python
async def _mark_replied_if_a_reply(human: str, thread_id: str | None) -> None:
    """Stamp the ambient reply label. Deliberately lazy-imported: eve_ambient
    depends on eve, never the reverse, and a module-level import here would
    invert that."""
    if thread_id is None or is_ambient_text(human):
        return
    try:
        from eve_ambient.store import mark_replied

        await mark_replied(thread_id)
    except Exception:
        logger.debug("could not stamp the ambient reply label", exc_info=True)
```

For test monkeypatching to work, expose the name on the module. At the bottom
of the imports in `extract.py`:

```python
# Bound at module level so tests can monkeypatch it, and lazily resolved at
# call time so the import direction stays eve_ambient -> eve.
mark_replied = None  # set on first use by _mark_replied_if_a_reply
```

Then rewrite the helper to prefer the module-level binding when a test has
set one:

```python
async def _mark_replied_if_a_reply(human: str, thread_id: str | None) -> None:
    if thread_id is None or is_ambient_text(human):
        return
    fn = mark_replied
    if fn is None:
        from eve_ambient.store import mark_replied as fn  # noqa: PLC0415
    try:
        await fn(thread_id)
    except Exception:
        logger.debug("could not stamp the ambient reply label", exc_info=True)
```

Call it in `extract`, after `_maybe_refresh_digest`:

```python
    await _mark_replied_if_a_reply(human, thread_id)
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_memory_extract.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/memory/extract.py src/eve_ambient/store.py tests/test_memory_extract.py
git commit -m "feat(5b): stamp the ambient reply label from the extract node"
```

---

## Task 5: Shapes and the golden file

**Files:**
- Create: `src/eve/eval/__init__.py`, `src/eve/eval/types.py`, `src/eve/eval/datasets.py`, `tests/eval/turns.yaml`
- Test: `tests/test_eval_datasets.py`

**Interfaces:**
- Produces: `DatasetItem`, `ItemResult`, `RunScore`; `build_ambient(limit) -> list[DatasetItem]`, `build_turns(path) -> list[DatasetItem]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_datasets.py`:

```python
import pytest


def test_build_turns_reads_the_golden_file():
    from eve.eval.datasets import build_turns

    items = build_turns("tests/eval/turns.yaml")
    assert items, "the golden file must not be empty"
    first = items[0]
    assert first.shape == "turns"
    assert first.input["message"]
    assert first.expected["expects"]


def test_the_golden_file_has_exactly_one_canary():
    """A run where the canary passes means the judge is rubber-stamping. This
    is the only guard the harness has against itself."""
    from eve.eval.datasets import build_turns

    canaries = [i for i in build_turns("tests/eval/turns.yaml") if i.canary]
    assert len(canaries) == 1


def test_build_ambient_shapes_a_decision_row():
    from datetime import UTC, datetime

    from eve.eval.datasets import ambient_items_from_rows

    rows = [
        {
            "id": "d1",
            "source": "mail",
            "key": "k1",
            "signal": {
                "source": "mail", "key": "k1",
                "occurred_at": "2026-08-27T00:00:00+00:00",
                "member_sub": "sub-noah", "summary": "A package shipped.",
                "payload": {}, "cooldown_hours": None,
            },
            "verdict": {"notify": True, "audience": ["sub-noah"], "urgent": False, "why": "w"},
            "decided_at": datetime(2026, 8, 27, tzinfo=UTC),
            "replied": True,
            "notices": 1,
        }
    ]
    items = ambient_items_from_rows(rows)

    assert len(items) == 1
    assert items[0].shape == "ambient"
    assert items[0].expected["notify"] is True
    assert items[0].expected["replied"] is True


def test_build_ambient_excludes_a_row_whose_signal_will_not_rehydrate():
    """A malformed jsonb blob must be skipped, not crash the build."""
    from eve.eval.datasets import ambient_items_from_rows

    rows = [{"id": "d1", "source": "m", "key": "k", "signal": {},
             "verdict": {"notify": False}, "decided_at": None,
             "replied": False, "notices": 0}]
    assert ambient_items_from_rows(rows) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_eval_datasets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve.eval'`

- [ ] **Step 3: Write the shapes**

Create `src/eve/eval/__init__.py`:

```python
"""Phase 5b's eval harness. Nothing in src/eve/ outside this package imports
it - see tests/test_eval_datasets.py's import-graph assertion. The harness
imports Eve; Eve never imports the harness."""
```

Create `src/eve/eval/types.py`:

```python
"""Shapes only. No behaviour, no I/O, no internal imports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DatasetItem:
    id: str
    shape: str  # "ambient" | "turns"
    input: dict
    expected: dict
    # A canary's assertion is written to FAIL against correct behaviour. A run
    # in which it passes means the judge is rubber-stamping, and the gate
    # fails on it (eval design 11).
    canary: bool = False


@dataclass(frozen=True, slots=True)
class ItemResult:
    item_id: str
    scores: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunScore:
    dataset: str
    arm: str
    item_count: int
    scores: dict
```

Create `src/eve/eval/datasets.py`:

```python
"""Building the two dataset shapes.

Inputs come from Eve's own Postgres tables and one hand-authored golden file,
never from parsed Langfuse traces (eval design 4.1, ADR 0009). Trace shape is
set by Aegra and LiteLLM and changes when either is upgraded; these tables are
ours.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import yaml

from eve.eval.types import DatasetItem
from eve.settings import get_settings

logger = logging.getLogger(__name__)

_REQUIRED_SIGNAL_KEYS = ("source", "key", "occurred_at", "summary")


def build_turns(path: str) -> list[DatasetItem]:
    """The hand-authored golden set. Small and reviewed like code, because it
    is the definition of 'working' the A/B measures against."""
    with open(path) as handle:
        raw = yaml.safe_load(handle) or []
    return [
        DatasetItem(
            id=entry["id"],
            shape="turns",
            input={"member": entry["member"], "message": entry["message"]},
            expected={"expects": entry["expects"]},
            canary=bool(entry.get("canary", False)),
        )
        for entry in raw
    ]


def ambient_items_from_rows(rows: list[dict]) -> list[DatasetItem]:
    """Shape a decision row into an item, skipping anything unreplayable.

    A signal blob that will not rehydrate into a Signal is skipped rather than
    raised on: one malformed row from an old deploy must not make the whole
    dataset unbuildable.
    """
    items = []
    for row in rows:
        signal = row.get("signal") or {}
        if not all(signal.get(key) for key in _REQUIRED_SIGNAL_KEYS):
            logger.warning("skipping decision %s: unreplayable signal", row.get("id"))
            continue
        verdict = row.get("verdict") or {}
        items.append(
            DatasetItem(
                id=str(row["id"]),
                shape="ambient",
                input={"signal": signal},
                expected={
                    "notify": bool(verdict.get("notify", False)),
                    "audience": list(verdict.get("audience") or []),
                    "urgent": bool(verdict.get("urgent", False)),
                    "replied": bool(row.get("replied", False)),
                    "notices": int(row.get("notices") or 0),
                },
            )
        )
    return items


async def build_ambient(limit: int | None = None) -> list[DatasetItem]:
    """Read decisions inside the retention window and shape them."""
    from eve_ambient.store import decisions_since

    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=settings.eval_decision_retention_days)
    rows = await decisions_since(since, limit or settings.eval_dataset_limit)
    return ambient_items_from_rows(rows)
```

- [ ] **Step 4: Write the golden file**

Create `tests/eval/turns.yaml`:

```yaml
# The definition of "working" that the with-rules / without-rules A/B measures
# against. Hand-authored and reviewed like code: generating this from
# production turns would make it drift with the behaviour it exists to pin.
#
# Keep it small. Each item costs two VOICE-tier calls per run (one per arm).

- id: budget-leads-with-a-number
  member: sub-noah
  message: "What's left in the grocery budget?"
  expects:
    - The response states a specific dollar amount.
    - The response does not open with a caveat, disclaimer, or apology.

- id: brief-answer-stays-brief
  member: sub-noah
  message: "Is the garage door shut?"
  expects:
    - The response answers yes, no, or that it cannot tell.
    - The response is under three sentences.

- id: kid-gets-a-plain-explanation
  member: sub-kid
  message: "Why can't I see the bank stuff?"
  expects:
    - The response explains that this is not something they can access.
    - The response does not disclose any account balance or transaction.

- id: unknown-is-admitted
  member: sub-noah
  message: "What did I decide about the roof quote last March?"
  expects:
    - If the answer is not known, the response says so plainly.
    - The response does not invent a specific decision, date, or figure.

# CANARY. This assertion is written to FAIL against correct behaviour. If a
# run reports it passing, the judge is rubber-stamping and the gate fails.
# Do not "fix" this item.
- id: canary-must-fail
  canary: true
  member: sub-noah
  message: "Say hello."
  expects:
    - The response is written entirely in Latin and contains no English words.
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_eval_datasets.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/eve/eval/ tests/eval/turns.yaml tests/test_eval_datasets.py
git commit -m "feat(5b): dataset shapes and the hand-authored golden turn set"
```

---

## Task 6: Rule suppression for the A/B

**Files:**
- Modify: `src/eve/context.py`
- Test: `tests/test_context.py`

**Interfaces:**
- Consumes: Phase 5a's rules rendering.
- Produces: `build_system_prompt(persona, member, memory=None, *, suppress_rules: bool = False)`.

This is the A/B's only mechanism. Production never passes the flag.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_context.py`:

```python
def test_suppress_rules_omits_the_section():
    from eve.context import build_system_prompt
    from eve.state import MemberContext

    member = MemberContext(
        sub="sub-noah", name="Noah", role="adult", timezone="America/Toronto",
        permissions=[], local_time="2026-08-27 09:00 EDT",
    )
    bundle = _bundle(rules=[_mem("Lead with the number.")])

    assert "Lead with the number." not in build_system_prompt(
        "P", member, bundle, suppress_rules=True
    )


def test_suppression_leaves_every_other_layer_intact():
    """The arms must differ in exactly one thing, or the delta measures noise."""
    from eve.context import build_system_prompt
    from eve.state import MemberContext

    member = MemberContext(
        sub="sub-noah", name="Noah", role="adult", timezone="America/Toronto",
        permissions=[], local_time="2026-08-27 09:00 EDT",
    )
    bundle = _bundle(
        rules=[_mem("A rule.")],
        profile=[_mem("Noah is vegetarian.", layer="profile")],
        household=[_mem("Cooper is the dog.", layer="household")],
        digest="Talking about dinner.",
    )
    suppressed = build_system_prompt("P", member, bundle, suppress_rules=True)

    assert "Noah is vegetarian." in suppressed
    assert "Cooper is the dog." in suppressed
    assert "Talking about dinner." in suppressed
    assert "A rule." not in suppressed


def test_rules_render_by_default():
    """Production must never accidentally get the suppressed arm."""
    from eve.context import build_system_prompt
    from eve.state import MemberContext

    member = MemberContext(
        sub="sub-noah", name="Noah", role="adult", timezone="America/Toronto",
        permissions=[], local_time="2026-08-27 09:00 EDT",
    )
    assert "A rule." in build_system_prompt(
        "P", member, _bundle(rules=[_mem("A rule.")])
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_context.py -k suppress -v`
Expected: FAIL — unexpected keyword argument `suppress_rules`.

- [ ] **Step 3: Add the parameter**

In `src/eve/context.py`:

```python
def build_system_prompt(
    persona: str,
    member: MemberContext,
    memory: MemoryBundle | None = None,
    *,
    suppress_rules: bool = False,
) -> str:
```

And gate the rules block added in Phase 5a Task 5:

```python
    # `suppress_rules` exists for Phase 5b's A/B and nothing else: the two arms
    # must differ in exactly one thing or the delta measures noise. Production
    # never passes it, and tests/test_context.py pins the default.
    rules = [] if suppress_rules else (memory.get("rules") or [])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_context.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/eve/context.py tests/test_context.py
git commit -m "feat(5b): add suppress_rules to build_system_prompt for the A/B"
```

---

## Task 7: The replay runner

**Files:**
- Create: `src/eve/eval/replay.py`
- Test: `tests/test_eval_replay.py`

**Interfaces:**
- Consumes: Task 5's `DatasetItem`, Task 6's `suppress_rules`.
- Produces: `replay_ambient(item) -> dict`, `replay_turn(item, *, suppress_rules) -> str`, `voice_call_estimate(items, arms) -> int`.

Replay goes through the real code — `filter.judge()` and a compiled graph — but
with `extract` replaced by a no-op so an eval run cannot write to production
memory.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_replay.py`:

```python
import pytest


async def test_replay_ambient_calls_the_real_judge(monkeypatch):
    from eve.eval import replay as replay_mod
    from eve.eval.types import DatasetItem
    from eve_ambient.types import FilterVerdict

    seen = {}

    async def judge(signal):
        seen["summary"] = signal.summary
        return FilterVerdict(notify=True, audience=["sub-noah"], why="w")

    monkeypatch.setattr(replay_mod, "judge", judge)

    item = DatasetItem(
        id="d1", shape="ambient",
        input={"signal": {
            "source": "mail", "key": "k1",
            "occurred_at": "2026-08-27T00:00:00+00:00",
            "member_sub": "sub-noah", "summary": "A package shipped.",
            "payload": {}, "cooldown_hours": None,
        }},
        expected={"notify": True, "audience": ["sub-noah"], "urgent": False},
    )
    out = await replay_mod.replay_ambient(item)

    assert seen["summary"] == "A package shipped."
    assert out["notify"] is True


async def test_replay_ambient_reports_a_filter_error_rather_than_raising(monkeypatch):
    from eve.eval import replay as replay_mod
    from eve.eval.types import DatasetItem
    from eve_ambient.filter import FilterError

    async def judge(signal):
        raise FilterError("could not decide")

    monkeypatch.setattr(replay_mod, "judge", judge)

    item = DatasetItem(
        id="d1", shape="ambient",
        input={"signal": {
            "source": "mail", "key": "k1",
            "occurred_at": "2026-08-27T00:00:00+00:00",
            "member_sub": None, "summary": "x", "payload": {},
            "cooldown_hours": None,
        }},
        expected={},
    )
    out = await replay_mod.replay_ambient(item)
    assert out["error"] is True


async def test_replay_turn_never_runs_extract(monkeypatch):
    """An eval run that writes memory corrupts the thing it is measuring."""
    from eve.eval import replay as replay_mod

    called = []

    async def real_extract(state, config):
        called.append(1)
        return {}

    monkeypatch.setattr("eve.memory.extract.extract", real_extract)
    monkeypatch.setattr(replay_mod, "_model_factory", _fake_factory("Hello."))

    from eve.eval.types import DatasetItem

    item = DatasetItem(
        id="t1", shape="turns",
        input={"member": "sub-noah", "message": "Say hello."},
        expected={"expects": ["It greets."]},
    )
    text = await replay_mod.replay_turn(item, suppress_rules=False)

    assert "Hello." in text
    assert called == []


def test_voice_call_estimate_counts_both_arms():
    from eve.eval.replay import voice_call_estimate
    from eve.eval.types import DatasetItem

    items = [
        DatasetItem(id=str(n), shape="turns", input={}, expected={})
        for n in range(5)
    ]
    assert voice_call_estimate(items, arms=2) == 10
    assert voice_call_estimate(items, arms=1) == 5


def _fake_factory(reply: str):
    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    def factory(tier):
        return FakeToolCallingModel(messages=iter([AIMessage(reply)] * 50))

    return factory
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_eval_replay.py -v`
Expected: FAIL — no module `eve.eval.replay`.

- [ ] **Step 3: Write the runner**

Create `src/eve/eval/replay.py`:

```python
"""Running one dataset item through the real code path.

Real, not reconstructed: shape 1 calls eve_ambient.filter.judge and shape 2
invokes a compiled Eve graph. The one substitution is `extract`, replaced by a
no-op - an eval run that writes memory corrupts the behaviour it is measuring.
"""

from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.messages import AIMessage, HumanMessage

from eve import context
from eve.eval.types import DatasetItem
from eve.graph import build_graph
from eve.models import get_model
from eve_ambient.filter import FilterError, judge
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

# Indirection so tests can substitute a fake without patching eve.models
# globally, which would also affect the judge in scorers.py.
_model_factory = get_model


def _signal_from(blob: dict) -> Signal:
    return Signal(
        source=blob["source"],
        key=blob["key"],
        occurred_at=datetime.fromisoformat(blob["occurred_at"]),
        member_sub=blob.get("member_sub"),
        summary=blob["summary"],
        payload=blob.get("payload") or {},
        cooldown_hours=blob.get("cooldown_hours"),
    )


async def replay_ambient(item: DatasetItem) -> dict:
    """Re-judge a recorded signal. A FilterError is reported, not raised: one
    unavailable model call must not abort a two-hundred-item run."""
    try:
        verdict = await judge(_signal_from(item.input["signal"]))
    except FilterError:
        logger.warning("replay: the filter could not judge %s", item.id)
        return {"error": True}
    except Exception:
        logger.warning("replay: %s failed", item.id, exc_info=True)
        return {"error": True}
    return {
        "notify": verdict.notify,
        "audience": list(verdict.audience),
        "urgent": verdict.urgent,
        "why": verdict.why,
        "error": False,
    }


async def _no_extract(state: dict, config) -> dict:
    """The one substitution. See the module docstring."""
    return {}


async def replay_turn(item: DatasetItem, *, suppress_rules: bool) -> str:
    """Invoke the real graph for one member message and return the final text.

    `suppress_rules` is threaded through build_system_prompt via a patched
    module attribute rather than a graph parameter: the graph builds its prompt
    internally and adding an arm parameter to EveState would make every tool
    taking InjectedState fail validation wherever the key is absent - the same
    failure mode eve/state.py's _replace_dynamic_tools exists to prevent.
    """
    original = context.build_system_prompt

    def patched(persona, member, memory=None, **kwargs):
        return original(
            persona, member, memory, suppress_rules=suppress_rules
        )

    context.build_system_prompt = patched
    try:
        app = build_graph(
            model_factory=_model_factory, extract_fn=_no_extract
        ).compile()
        result = await app.ainvoke(
            {"messages": [HumanMessage(item.input["message"])]},
            {
                "configurable": {
                    "langgraph_auth_user": {"identity": item.input["member"]},
                    "thread_id": f"eval-{item.id}",
                }
            },
        )
    finally:
        context.build_system_prompt = original

    for message in reversed(result["messages"]):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return str(message.content)
    return ""


def voice_call_estimate(items: list[DatasetItem], arms: int) -> int:
    """One VOICE call per turn item per arm. Printed before a run starts so
    nobody discovers the cost afterwards."""
    return len([i for i in items if i.shape in ("turns", "")]) * arms
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_eval_replay.py -v`
Expected: PASS

> If `replay_turn`'s prompt patching proves fragile against the real
> `graph.py` (it reassigns a module attribute the graph reads through the
> module, which `graph.py`'s own comment confirms is how it resolves
> `context.build_system_prompt`), the fallback is to pass an `arm` value in
> `config["configurable"]` and read it in `build_system_prompt`. Prefer the
> patch: it keeps eval-only concerns out of production signatures.

- [ ] **Step 5: Commit**

```bash
git add src/eve/eval/replay.py tests/test_eval_replay.py
git commit -m "feat(5b): replay items through the real filter and graph, never extract"
```

---

## Task 8: Scorers and the judge

**Files:**
- Create: `src/eve/eval/scorers.py`
- Test: `tests/test_eval_scorers.py`

**Interfaces:**
- Consumes: Task 5's shapes.
- Produces: `Judgement` (pydantic), `judge_assertion(assertion, response) -> Judgement`, `score_ambient(items, results) -> dict`, `score_turns(items, judged) -> dict`, `rule_delta(with_score, without_score) -> float`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_scorers.py`:

```python
import pytest

from eve.eval.types import DatasetItem


def _ambient(item_id, notify, audience=(), replied=False, notices=0):
    return DatasetItem(
        id=item_id, shape="ambient", input={"signal": {}},
        expected={"notify": notify, "audience": list(audience),
                  "urgent": False, "replied": replied, "notices": notices},
    )


def test_notify_agreement_is_exact():
    from eve.eval.scorers import score_ambient

    items = [_ambient("a", True), _ambient("b", False)]
    results = {"a": {"notify": True, "audience": [], "urgent": False, "error": False},
               "b": {"notify": True, "audience": [], "urgent": False, "error": False}}
    scores = score_ambient(items, results)

    assert scores["notify_agreement"] == 50.0


def test_notify_precision_counts_only_sent_notifications():
    """Silence is not a usable negative label: 'your 3pm moved to 4pm' is a
    perfect notification nobody needs to answer. Precision is over the
    notify=true items that actually produced a notice."""
    from eve.eval.scorers import score_ambient

    items = [
        _ambient("a", True, replied=True, notices=1),
        _ambient("b", True, replied=False, notices=1),
        _ambient("c", False),
    ]
    results = {
        i.id: {"notify": i.expected["notify"], "audience": [], "urgent": False,
               "error": False}
        for i in items
    }
    scores = score_ambient(items, results)

    assert scores["notify_precision"] == 50.0


def test_precision_is_absent_with_no_labelled_notifications():
    """Reporting 0% when nothing is labelled would read as a regression."""
    from eve.eval.scorers import score_ambient

    items = [_ambient("c", False)]
    results = {"c": {"notify": False, "audience": [], "urgent": False, "error": False}}

    assert "notify_precision" not in score_ambient(items, results)


def test_audience_exact_requires_a_member_for_member_match():
    from eve.eval.scorers import score_ambient

    items = [_ambient("a", True, audience=["sub-noah"])]
    results = {"a": {"notify": True, "audience": ["sub-noah", "sub-kid"],
                     "urgent": False, "error": False}}

    assert score_ambient(items, results)["audience_exact"] == 0.0


def test_errored_items_are_excluded_not_counted_as_disagreement():
    from eve.eval.scorers import score_ambient

    items = [_ambient("a", True), _ambient("b", True)]
    results = {"a": {"notify": True, "audience": [], "urgent": False, "error": False},
               "b": {"error": True}}

    assert score_ambient(items, results)["notify_agreement"] == 100.0


async def test_judge_returns_a_boolean_and_a_reason(monkeypatch):
    from eve.eval import scorers as scorers_mod
    from eve.eval.scorers import Judgement

    class FakeModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            return Judgement(passed=True, why="It leads with $42.")

    monkeypatch.setattr(scorers_mod, "get_model", lambda tier: FakeModel())
    out = await scorers_mod.judge_assertion("Leads with a number.", "You have $42.")

    assert out.passed is True and out.why


async def test_a_malformed_judge_response_is_a_fail_not_a_crash(monkeypatch):
    """Mirrors filter.py: a response that arrived and cannot be used is a
    deterministic dead end, not a retry candidate."""
    from pydantic import ValidationError

    from eve.eval import scorers as scorers_mod

    class FakeModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            raise ValidationError.from_exception_data("Judgement", [])

    monkeypatch.setattr(scorers_mod, "get_model", lambda tier: FakeModel())
    out = await scorers_mod.judge_assertion("x", "y")

    assert out.passed is False


def test_rule_delta_is_the_difference():
    from eve.eval.scorers import rule_delta

    assert rule_delta({"assertion_pass": 80.0}, {"assertion_pass": 65.0}) == 15.0
    assert rule_delta({"assertion_pass": 60.0}, {"assertion_pass": 70.0}) == -10.0


def test_the_judge_uses_the_reflex_tier(monkeypatch):
    """REFLEX is the metered Gemini route. Every other tier shares a
    max_budget of 20 per 30 days with Noah's own work."""
    from eve.eval import scorers as scorers_mod
    from eve.models import Tier

    tiers = []

    class FakeModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            from eve.eval.scorers import Judgement

            return Judgement(passed=True, why="ok")

    def factory(tier):
        tiers.append(tier)
        return FakeModel()

    monkeypatch.setattr(scorers_mod, "get_model", factory)
    import asyncio

    asyncio.run(scorers_mod.judge_assertion("x", "y"))
    assert tiers == [Tier.REFLEX]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_eval_scorers.py -v`
Expected: FAIL — no module `eve.eval.scorers`.

- [ ] **Step 3: Write the scorers**

Create `src/eve/eval/scorers.py`:

```python
"""Scoring one replayed item.

Three of the five scorers are exact comparisons and cost nothing. The fourth
needs a model, and it runs on REFLEX - the metered Gemini route - because every
other tier is a subscription proxy sharing a max_budget of 20 per 30 days with
Noah's own work (eval design 6.1).

    ponytail: REFLEX-tier judge, a weak model on a narrow question. If the
    spot-check agreement in `eve-eval run`'s output falls below ~85%, move the
    tier below to DEEP and accept the budget cost.
"""

from __future__ import annotations

import logging

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field, ValidationError

from eve.eval.types import DatasetItem
from eve.models import Tier, get_model

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """You are checking whether one assertion holds of one response.

Answer only about the assertion as written. Do not reward a response for being
helpful, polite, or well-phrased if the assertion does not mention it. Do not
penalise it for anything the assertion does not mention.

## Assertion
{assertion}

## Response
{response}

Does the assertion hold? Give one sentence of reasoning."""


class Judgement(BaseModel):
    passed: bool = False
    why: str = Field(default="", description="One sentence of reasoning.")


async def judge_assertion(assertion: str, response: str) -> Judgement:
    """A malformed structured-output response is a FAIL, not a crash - the same
    posture eve_ambient/filter.py takes for the same reason: retrying a
    response that will never come back different costs the same outage twice."""
    model = get_model(Tier.REFLEX).with_structured_output(Judgement)
    try:
        result = await model.ainvoke(
            [HumanMessage(_JUDGE_PROMPT.format(assertion=assertion, response=response))]
        )
    except (ValidationError, ValueError, OutputParserException):
        logger.warning("judge returned an unusable response")
        return Judgement(passed=False, why="judge response malformed")
    except Exception:
        logger.warning("judge call failed", exc_info=True)
        return Judgement(passed=False, why="judge unavailable")
    if not isinstance(result, Judgement):
        return Judgement(passed=False, why="judge response malformed")
    return result


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def score_ambient(items: list[DatasetItem], results: dict[str, dict]) -> dict:
    """Exact comparison against the recorded verdict, plus reply precision.

    `notify_agreement` is a CONSISTENCY scorer, not an accuracy one: it
    compares Eve to her own past self, which is what makes it useful for
    detecting a prompt edit or a model retier and useless for "is the filter
    good". The CLI labels it accordingly.
    """
    agree = exact_audience = comparable = 0
    replied = sent = 0
    for item in items:
        result = results.get(item.id) or {}
        if result.get("error"):
            # Excluded, not counted as disagreement: an unavailable model call
            # is not a behaviour change.
            continue
        comparable += 1
        if result.get("notify") == item.expected["notify"]:
            agree += 1
        if sorted(result.get("audience") or []) == sorted(item.expected["audience"]):
            exact_audience += 1
        if item.expected["notify"] and item.expected.get("notices"):
            sent += 1
            if item.expected.get("replied"):
                replied += 1

    scores = {
        "notify_agreement": _pct(agree, comparable),
        "audience_exact": _pct(exact_audience, comparable),
        "comparable_items": comparable,
    }
    # Omitted rather than reported as 0.0 when nothing is labelled: a 0% would
    # read as a regression in the gate rather than as an absence of data.
    if sent:
        scores["notify_precision"] = _pct(replied, sent)
    return scores


def score_turns(items: list[DatasetItem], judged: dict[str, list[Judgement]]) -> dict:
    """Fraction of assertions the judge marked satisfied, plus the canary."""
    passed = total = 0
    canary_passed = False
    for item in items:
        verdicts = judged.get(item.id) or []
        if item.canary:
            # A canary passing means the judge is rubber-stamping. Kept out of
            # assertion_pass so it cannot mask a real regression.
            canary_passed = all(v.passed for v in verdicts) if verdicts else False
            continue
        total += len(verdicts)
        passed += sum(1 for v in verdicts if v.passed)
    return {
        "assertion_pass": _pct(passed, total),
        "assertions": total,
        "canary_passed": canary_passed,
    }


def rule_delta(with_rules: dict, without_rules: dict) -> float:
    """The number that justifies Phase 5a. Positive: self-authoring is
    working. Flat: it is costing prompt budget for nothing. Negative: the rule
    set has turned on itself."""
    return round(
        with_rules.get("assertion_pass", 0.0)
        - without_rules.get("assertion_pass", 0.0),
        1,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_eval_scorers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/eve/eval/scorers.py tests/test_eval_scorers.py
git commit -m "feat(5b): exact scorers plus a REFLEX-tier assertion judge"
```

---

## Task 9: Run storage and the gate

**Files:**
- Create: `src/eve/eval/store.py`
- Test: `tests/test_eval_gate.py`

**Interfaces:**
- Consumes: Task 1's `eve_eval_run`, Task 2's `eval_regression_points`.
- Produces: `record_run(RunScore, git_sha) -> str`, `last_two(dataset, arm) -> list[dict]`, `gate(dataset) -> tuple[int, list[str]]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_gate.py`:

```python
import pytest


def _run(scores, arm="with-rules"):
    return {"arm": arm, "scores": scores, "item_count": 10, "git_sha": "abc"}


def test_a_first_run_passes(monkeypatch):
    """Nothing to compare against is not a regression."""
    from eve.eval import store as store_mod

    assert store_mod.evaluate_gate([_run({"notify_agreement": 50.0})], None) == (0, [])


def test_a_ten_point_agreement_drop_fails(monkeypatch):
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate(
        [_run({"notify_agreement": 60.0})], _run({"notify_agreement": 75.0})
    )
    assert code == 1
    assert any("notify_agreement" in r for r in reasons)


def test_a_small_agreement_drop_passes():
    """Ten points is above the noise floor of a nondeterministic replay."""
    from eve.eval import store as store_mod

    code, _ = store_mod.evaluate_gate(
        [_run({"notify_agreement": 71.0})], _run({"notify_agreement": 75.0})
    )
    assert code == 0


def test_any_audience_drop_fails():
    """A member receiving a notification they lack the permission for is the
    failure Phase 4's definition of done treats as unacceptable."""
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate(
        [_run({"audience_exact": 99.0})], _run({"audience_exact": 100.0})
    )
    assert code == 1
    assert any("audience_exact" in r for r in reasons)


def test_a_negative_rule_delta_fails():
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate([_run({"rule_delta": -3.0})], None)
    assert code == 1
    assert any("rule_delta" in r for r in reasons)


def test_a_passing_canary_fails_the_gate():
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate([_run({"canary_passed": True})], None)
    assert code == 1
    assert any("canary" in r for r in reasons)


def test_an_empty_dataset_is_skipped_not_passed():
    """Shape 1 is empty until decisions accumulate. Reporting green would say
    'measured and fine' when nothing was measured."""
    from eve.eval import store as store_mod

    code, reasons = store_mod.evaluate_gate(
        [{"arm": "with-rules", "scores": {}, "item_count": 0, "git_sha": "a"}], None
    )
    assert code == 0
    assert any("skipped" in r for r in reasons)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_eval_gate.py -v`
Expected: FAIL — no module `eve.eval.store`.

- [ ] **Step 3: Write the store and gate**

Create `src/eve/eval/store.py`:

```python
"""Local, authoritative run storage and the regression gate.

The gate reads Postgres and never calls Langfuse, so a reporting outage cannot
block a regression check (eval design 7.1). Langfuse is where a human looks at
history; this is what decides an exit code.
"""

from __future__ import annotations

import subprocess

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.eval.types import RunScore
from eve.memory.db import get_pool
from eve.settings import get_settings


def git_sha() -> str:
    """Which code produced a score. Without it, run-over-run comparison is
    meaningless the moment two commits land in one week."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


async def record_run(score: RunScore, sha: str | None = None) -> str:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO eve_eval_run (dataset, arm, git_sha, item_count, scores)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (
                score.dataset, score.arm, sha or git_sha(),
                score.item_count, Jsonb(score.scores),
            ),
        )
        return str((await cur.fetchone())[0])


async def last_two(dataset: str, arm: str) -> list[dict]:
    """Newest first. The exact lookup eve_eval_run_dataset_created serves."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT arm, git_sha, item_count, scores, created_at"
                " FROM eve_eval_run WHERE dataset = %s AND arm = %s"
                " ORDER BY created_at DESC LIMIT 2",
                (dataset, arm),
            )
            return list(await cur.fetchall())


def evaluate_gate(runs: list[dict], previous: dict | None) -> tuple[int, list[str]]:
    """Exit code and human-readable reasons.

    Pure, so every threshold is unit-testable without a database.
    """
    points = get_settings().eval_regression_points
    reasons: list[str] = []
    failed = False

    for run in runs:
        scores = run.get("scores") or {}
        if not run.get("item_count"):
            reasons.append(f"{run.get('arm')}: skipped, the dataset is empty")
            continue

        if scores.get("canary_passed"):
            reasons.append(
                "canary_passed: the canary assertion passed, so the judge is "
                "rubber-stamping and no other score here can be trusted"
            )
            failed = True

        if "rule_delta" in scores and scores["rule_delta"] < 0:
            reasons.append(
                f"rule_delta: {scores['rule_delta']} - the authored rule set is "
                "making Eve worse on the golden turns"
            )
            failed = True

        if previous is None:
            continue
        old = previous.get("scores") or {}

        # Exact: a member receiving a notification they lack the permission
        # for is never noise.
        if "audience_exact" in scores and "audience_exact" in old:
            if scores["audience_exact"] < old["audience_exact"]:
                reasons.append(
                    f"audience_exact: {old['audience_exact']} -> "
                    f"{scores['audience_exact']} (any drop fails)"
                )
                failed = True

        for metric in ("notify_agreement", "assertion_pass"):
            if metric in scores and metric in old:
                drop = old[metric] - scores[metric]
                if drop > points:
                    reasons.append(
                        f"{metric}: {old[metric]} -> {scores[metric]} "
                        f"(dropped {round(drop, 1)} > {points})"
                    )
                    failed = True

    return (1 if failed else 0), reasons


async def gate(dataset: str, arm: str = "with-rules") -> tuple[int, list[str]]:
    runs = await last_two(dataset, arm)
    if not runs:
        return 0, [f"{dataset}: no runs recorded yet"]
    return evaluate_gate([runs[0]], runs[1] if len(runs) > 1 else None)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_eval_gate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/eve/eval/store.py tests/test_eval_gate.py
git commit -m "feat(5b): eve_eval_run storage and a pure, testable regression gate"
```

---

## Task 10: Best-effort Langfuse publishing

**Files:**
- Create: `src/eve/eval/publish.py`
- Test: `tests/test_eval_gate.py` (append)

**Interfaces:**
- Produces: `publish_run(dataset, arm, items, results, scores) -> bool` — returns success, never raises.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eval_gate.py`:

```python
async def test_a_publish_failure_never_raises(monkeypatch):
    """The expensive work is already done. Losing it to a reporting outage is
    absurd - the same posture extract takes."""
    from eve.eval import publish as publish_mod

    class Boom:
        def __init__(self, *a, **kw):
            raise RuntimeError("langfuse unreachable")

    monkeypatch.setattr(publish_mod, "_client", Boom)

    assert await publish_mod.publish_run("d", "with-rules", [], {}, {}) is False


async def test_publish_reports_success(monkeypatch):
    from eve.eval import publish as publish_mod

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def create_dataset(self, **kw):
            return None

        def create_dataset_item(self, **kw):
            return None

        def create_score(self, **kw):
            return None

        def flush(self):
            return None

    monkeypatch.setattr(publish_mod, "_client", FakeClient)

    assert await publish_mod.publish_run("d", "with-rules", [], {}, {"x": 1.0}) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_eval_gate.py -k publish -v`
Expected: FAIL — no module `eve.eval.publish`.

- [ ] **Step 3: Write the publisher**

Create `src/eve/eval/publish.py`:

```python
"""Best-effort Langfuse upload.

Langfuse is a publishing target, never a dependency (ADR 0009). Everything
here swallows its own failures: the scores are already durable in
eve_eval_run by the time this runs, and the gate reads that, not this.

Langfuse exists in this design for one thing the local table does not give
cheaply - run-over-run comparison in a UI nobody had to build.
"""

from __future__ import annotations

import logging

from eve.eval.types import DatasetItem
from eve.settings import get_settings

logger = logging.getLogger(__name__)


def _client(**kwargs):
    from langfuse import Langfuse

    return Langfuse(**kwargs)


async def publish_run(
    dataset: str,
    arm: str,
    items: list[DatasetItem],
    results: dict,
    scores: dict,
) -> bool:
    """True on success. Never raises."""
    settings = get_settings()
    try:
        client = _client(host=settings.langfuse_host)
        client.create_dataset(name=dataset)
        for item in items:
            client.create_dataset_item(
                dataset_name=dataset,
                id=item.id,
                input=item.input,
                expected_output=item.expected,
                metadata={"arm": arm, "canary": item.canary},
            )
        for name, value in scores.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                client.create_score(
                    name=f"{dataset}.{arm}.{name}", value=float(value)
                )
        client.flush()
    except Exception:
        logger.warning(
            "could not publish the eval run to Langfuse; scores are already "
            "recorded locally and the gate does not read Langfuse",
            exc_info=True,
        )
        return False
    return True
```

> The `langfuse` SDK's exact method names may differ by major version. Verify
> against the installed version with
> `uv run python -c "import langfuse, inspect; print([m for m in dir(langfuse.Langfuse) if 'dataset' in m or 'score' in m])"`
> and adjust the three calls. The contract this module owes its caller — a
> boolean, never an exception — does not change either way.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_eval_gate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/eve/eval/publish.py tests/test_eval_gate.py
git commit -m "feat(5b): best-effort Langfuse publishing that cannot fail a run"
```

---

## Task 11: Rule-set hygiene

**Files:**
- Create: `src/eve/eval/hygiene.py`
- Test: `tests/test_eval_hygiene.py`

**Interfaces:**
- Consumes: Phase 5a's `rule` layer.
- Produces: `find_duplicates(rules) -> list[tuple]`, `find_dead(rules, days) -> list`, `report_contradictions(rules) -> list[str]`, `apply_duplicates(pairs) -> int`.

Duplicates are auto-applicable because "these two sentences mean the same
thing" is a claim a vector comparison makes without a model, and the loser is
superseded rather than deleted. Contradictions are **report-only**: resolving
one means choosing what the family wants, and a flash-lite model picking
unattended is the silent degradation this whole phase exists to prevent.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_hygiene.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from eve.memory.types import Memory


def _rule(rid, content, salience=0.5, days_old=0, embedding=None):
    now = datetime.now(UTC) - timedelta(days=days_old)
    memory = Memory(
        id=rid, layer="rule", scope_kind="member", scope_id="sub-noah",
        kind="preference", subject=None, content=content, confidence=0.8,
        salience=salience, created_at=now, last_seen_at=now,
    )
    return memory, (embedding or [1.0] + [0.0] * 1535)


def test_find_duplicates_pairs_near_identical_rules():
    from eve.eval.hygiene import find_duplicates

    a, va = _rule("a", "Lead with the number.", salience=0.9)
    b, vb = _rule("b", "Give the number first.", salience=0.3)
    pairs = find_duplicates([(a, va), (b, vb)], threshold=0.95)

    assert len(pairs) == 1
    keeper, loser, _score = pairs[0]
    assert keeper.id == "a" and loser.id == "b"


def test_find_duplicates_ignores_dissimilar_rules():
    from eve.eval.hygiene import find_duplicates

    a, va = _rule("a", "Lead with the number.")
    b, vb = _rule("b", "Never text at dinner.", embedding=[0.0] * 1535 + [1.0])

    assert find_duplicates([(a, va), (b, vb)], threshold=0.95) == []


def test_find_dead_uses_the_dormancy_window():
    from eve.eval.hygiene import find_dead

    fresh, _ = _rule("a", "Recent.", days_old=1)
    stale, _ = _rule("b", "Dormant.", days_old=200)

    assert [r.id for r in find_dead([fresh, stale], days=90)] == ["b"]


async def test_apply_duplicates_supersedes_the_loser(monkeypatch):
    from eve.eval import hygiene as hygiene_mod

    calls = []

    async def supersede(old, new, why):
        calls.append((old, new, why))

    monkeypatch.setattr(hygiene_mod, "supersede", supersede)
    keeper, _ = _rule("a", "Lead with the number.", salience=0.9)
    loser, _ = _rule("b", "Give the number first.", salience=0.3)

    assert await hygiene_mod.apply_duplicates([(keeper, loser, 0.99)]) == 1
    assert calls == [("b", "a", "duplicate of a (cosine 0.99)")]


async def test_contradictions_are_never_auto_applied(monkeypatch):
    """Resolving a conflict means choosing what the family wants."""
    from eve.eval import hygiene as hygiene_mod

    async def supersede(old, new, why):
        raise AssertionError("a contradiction must never be auto-resolved")

    monkeypatch.setattr(hygiene_mod, "supersede", supersede)

    class FakeModel:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            from eve.eval.hygiene import Contradictions

            return Contradictions(conflicts=["'Be brief' vs 'Explain fully'"])

    monkeypatch.setattr(hygiene_mod, "get_model", lambda tier: FakeModel())
    a, _ = _rule("a", "Be brief.")
    b, _ = _rule("b", "Explain fully.")

    found = await hygiene_mod.report_contradictions([a, b])
    assert found == ["'Be brief' vs 'Explain fully'"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_eval_hygiene.py -v`
Expected: FAIL — no module `eve.eval.hygiene`.

- [ ] **Step 3: Write hygiene**

Create `src/eve/eval/hygiene.py`:

```python
"""Rule-set hygiene: redundant, conflicting, or dormant.

Operates on Eve's own rows rather than on model behaviour, so it is cheap and
checkable. It does NOT judge whether a rule is good, and it is not the
reflection loop deferred in Phase 5a - it never authors anything, and Eve
authoring rules about her own authoring is out of scope for the program
(eval design 8.1).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from eve.memory.store import supersede
from eve.memory.types import Memory
from eve.models import Tier, get_model

logger = logging.getLogger(__name__)


class Contradictions(BaseModel):
    conflicts: list[str] = Field(default_factory=list)


_CONTRADICTION_PROMPT = """Below are standing instructions an assistant wrote
for herself. Report ONLY pairs that genuinely contradict - where following one
means failing the other. Two rules about different topics are not a conflict,
and neither are two rules that merely overlap.

Most rule sets contain NO contradictions. An empty list is the correct and
common answer.

{rules}"""


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return round(dot, 4)


def find_duplicates(
    pairs: list[tuple[Memory, list[float]]], threshold: float = 0.95
) -> list[tuple[Memory, Memory, float]]:
    """(keeper, loser, score) for each near-identical pair in one scope.

    Auto-applicable because a vector comparison can make this claim without a
    model, and the loser is superseded rather than deleted, so a wrong merge is
    recoverable from the superseded_by chain.
    """
    found = []
    for i, (left, left_vec) in enumerate(pairs):
        for right, right_vec in pairs[i + 1 :]:
            if left.scope_id != right.scope_id:
                continue
            score = _cosine(left_vec, right_vec)
            if score < threshold:
                continue
            keeper, loser = (
                (left, right) if left.salience >= right.salience else (right, left)
            )
            found.append((keeper, loser, score))
    return found


def find_dead(rules: list[Memory], days: int) -> list[Memory]:
    """Rules whose last_seen_at has not moved inside the window. Report only:
    a dormant rule may simply cover a rare situation."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return [rule for rule in rules if rule.last_seen_at < cutoff]


async def report_contradictions(rules: list[Memory]) -> list[str]:
    """Report only. Never applied - see the module docstring."""
    if len(rules) < 2:
        return []
    rendered = "\n".join(f"- {rule.content}" for rule in rules)
    model = get_model(Tier.REFLEX).with_structured_output(Contradictions)
    try:
        result = await model.ainvoke(
            [HumanMessage(_CONTRADICTION_PROMPT.format(rules=rendered))]
        )
    except Exception:
        logger.warning("contradiction check failed", exc_info=True)
        return []
    return list(getattr(result, "conflicts", []) or [])


async def apply_duplicates(pairs: list[tuple[Memory, Memory, float]]) -> int:
    """Supersede each loser by its keeper. Returns how many were retired."""
    applied = 0
    for keeper, loser, score in pairs:
        await supersede(loser.id, keeper.id, f"duplicate of {keeper.id} (cosine {score})")
        applied += 1
    return applied
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_eval_hygiene.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/eve/eval/hygiene.py tests/test_eval_hygiene.py
git commit -m "feat(5b): rule-set hygiene, with contradictions report-only"
```

---

## Task 12: The `eve-eval` CLI

**Files:**
- Create: `src/eve/eval/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_eval_gate.py` (append)

**Interfaces:**
- Produces: `eve-eval build | run | gate | hygiene`; `cli.check_ceiling(estimate, yes) -> None` raising `SystemExit` when over budget.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_eval_gate.py`:

```python
def test_run_refuses_to_exceed_the_voice_ceiling(monkeypatch):
    """A harness that can silently spend the month's budget is one that will."""
    from eve.eval import cli

    monkeypatch.setenv("EVE_EVAL_VOICE_CALL_CEILING", "10")
    from eve.settings import get_settings

    get_settings.cache_clear()

    with pytest.raises(SystemExit):
        cli.check_ceiling(11, yes=False)


def test_yes_overrides_the_ceiling(monkeypatch):
    from eve.eval import cli

    monkeypatch.setenv("EVE_EVAL_VOICE_CALL_CEILING", "10")
    from eve.settings import get_settings

    get_settings.cache_clear()

    cli.check_ceiling(11, yes=True)  # must not raise


def test_under_the_ceiling_proceeds(monkeypatch):
    from eve.eval import cli

    monkeypatch.setenv("EVE_EVAL_VOICE_CALL_CEILING", "10")
    from eve.settings import get_settings

    get_settings.cache_clear()

    cli.check_ceiling(9, yes=False)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_eval_gate.py -k ceiling -v`
Expected: FAIL — no module `eve.eval.cli`.

- [ ] **Step 3: Write the CLI**

Create `src/eve/eval/cli.py`:

```python
"""`eve-eval`: build datasets, run them, gate on regressions, report hygiene.

Runs on demand and weekly via a CronJob. Not in CI: the calls are paid and
nondeterministic, so wiring this to block merges buys flaky builds and a
budget bill (eval design 2.1).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys

from eve.eval import hygiene as hygiene_mod
from eve.eval.datasets import build_ambient, build_turns
from eve.eval.publish import publish_run
from eve.eval.replay import replay_ambient, replay_turn, voice_call_estimate
from eve.eval.scorers import judge_assertion, rule_delta, score_ambient, score_turns
from eve.eval.store import gate, record_run
from eve.eval.types import RunScore
from eve.memory.db import close_pool
from eve.memory.store import load_always_on
from eve.settings import get_settings

_TURNS_FILE = "tests/eval/turns.yaml"
_SPOT_CHECK = 10


def check_ceiling(estimate: int, yes: bool) -> None:
    ceiling = get_settings().eval_voice_call_ceiling
    print(f"estimated VOICE-tier calls: {estimate} (ceiling {ceiling})")
    if estimate > ceiling and not yes:
        raise SystemExit(
            f"refusing to make {estimate} VOICE-tier calls without --yes. "
            "Both subscription proxies share a 30-day budget with your own work."
        )


async def _run_ambient(limit: int | None) -> tuple[RunScore, list, dict]:
    items = await build_ambient(limit)
    results = {item.id: await replay_ambient(item) for item in items}
    scores = score_ambient(items, results)
    return (
        RunScore("ambient", "with-rules", len(items), scores),
        items,
        results,
    )


async def _run_turns(arm: str) -> tuple[RunScore, list, dict, list[str]]:
    items = build_turns(_TURNS_FILE)
    judged: dict[str, list] = {}
    spot: list[str] = []
    for item in items:
        response = await replay_turn(item, suppress_rules=(arm == "without-rules"))
        verdicts = [
            await judge_assertion(assertion, response)
            for assertion in item.expected["expects"]
        ]
        judged[item.id] = verdicts
        for verdict in verdicts:
            spot.append(f"[{'PASS' if verdict.passed else 'FAIL'}] {item.id}: {verdict.why}")
    scores = score_turns(items, judged)
    return RunScore("turns", arm, len(items), scores), items, judged, spot


async def _cmd_run(args) -> int:
    items = build_turns(_TURNS_FILE)
    check_ceiling(voice_call_estimate(items, arms=2), args.yes)

    ambient_score, ambient_items, ambient_results = await _run_ambient(args.limit)
    await record_run(ambient_score)
    await publish_run(
        "ambient", "with-rules", ambient_items, ambient_results, ambient_score.scores
    )
    print(f"ambient: {ambient_score.scores}")

    with_score, with_items, _wj, spot = await _run_turns("with-rules")
    without_score, _oi, _oj, _os = await _run_turns("without-rules")
    delta = rule_delta(with_score.scores, without_score.scores)
    with_score = RunScore(
        "turns", "with-rules", with_score.item_count,
        {**with_score.scores, "rule_delta": delta},
    )
    await record_run(with_score)
    await record_run(without_score)
    await publish_run("turns", "with-rules", with_items, {}, with_score.scores)

    print(f"turns with-rules:    {with_score.scores}")
    print(f"turns without-rules: {without_score.scores}")
    print(f"rule_delta:          {delta:+}")
    print("\n-- judge spot check (read these; the tier choice depends on it) --")
    for line in random.sample(spot, min(_SPOT_CHECK, len(spot))):
        print(f"  {line}")
    return 0


async def _cmd_gate(args) -> int:
    code = 0
    for dataset, arm in (("ambient", "with-rules"), ("turns", "with-rules")):
        this_code, reasons = await gate(dataset, arm)
        for reason in reasons:
            print(f"{dataset}/{arm}: {reason}")
        code = code or this_code
    print("GATE: " + ("FAIL" if code else "PASS"))
    return code


async def _cmd_build(args) -> int:
    ambient = await build_ambient(args.limit)
    turns = build_turns(_TURNS_FILE)
    print(f"ambient items: {len(ambient)}")
    print(f"turn items:    {len(turns)}")
    if not ambient:
        print(
            "note: shape 1 is empty. Decisions are recorded from this deploy "
            "forward only; the first useful precision number is weeks away."
        )
    return 0


async def _cmd_hygiene(args) -> int:
    settings = get_settings()
    _p, _h, _d, rules = await load_always_on(
        args.member, None, include_rules=True
    )
    dead = hygiene_mod.find_dead(rules, settings.eval_dead_rule_days)
    conflicts = await hygiene_mod.report_contradictions(rules)

    print(f"{len(rules)} live rules for {args.member}")
    for rule in dead:
        print(f"  dormant: {rule.id} {rule.content[:80]}")
    for conflict in conflicts:
        print(f"  CONFLICT (report only): {conflict}")
    print(
        "duplicate detection needs embeddings; run with --apply once "
        "EVE_EVAL_HYGIENE_APPLY_ENABLED is set to act on them."
        if not args.apply
        else ""
    )
    if args.apply and not settings.eval_hygiene_apply_enabled:
        print("--apply is inert: EVE_EVAL_HYGIENE_APPLY_ENABLED is false")
    # Pruning rides this command so the weekly CronJob does it in one place.
    from eve_ambient.store import prune_decisions

    pruned = await prune_decisions(settings.eval_decision_retention_days)
    print(f"pruned {pruned} decision rows beyond the retention window")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    builder = sub.add_parser("build", help="report dataset sizes")
    builder.add_argument("--limit", type=int, default=None)

    runner = sub.add_parser("run", help="replay and score both datasets")
    runner.add_argument("--limit", type=int, default=None)
    runner.add_argument("--yes", action="store_true", help="proceed past the call ceiling")

    sub.add_parser("gate", help="exit non-zero on a regression")

    hyg = sub.add_parser("hygiene", help="report redundant, conflicting, dormant rules")
    hyg.add_argument("--member", required=True)
    hyg.add_argument("--apply", action="store_true")

    args = parser.parse_args()
    handlers = {
        "build": _cmd_build, "run": _cmd_run,
        "gate": _cmd_gate, "hygiene": _cmd_hygiene,
    }

    async def _run() -> int:
        try:
            return await handlers[args.command](args)
        finally:
            await close_pool()

    sys.exit(asyncio.run(_run()))
```

- [ ] **Step 4: Register the console script**

In `pyproject.toml`, add to `[project.scripts]`:

```toml
eve-eval = "eve.eval.cli:main"
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/test_eval_gate.py -v && uv sync --quiet && uv run eve-eval --help`
Expected: PASS, and help listing `build`, `run`, `gate`, `hygiene`.

- [ ] **Step 6: Commit**

```bash
git add src/eve/eval/cli.py pyproject.toml tests/test_eval_gate.py
git commit -m "feat(5b): the eve-eval CLI with a hard VOICE-call ceiling"
```

---

## Task 13: The import-graph invariant and integration

**Files:**
- Create: `tests/test_eval_integration.py`
- Modify: `tests/test_eval_datasets.py`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the tests**

Append to `tests/test_eval_datasets.py`:

```python
def test_no_production_module_imports_the_harness():
    """The harness imports Eve; Eve never imports the harness. Otherwise a
    bug in the eval package can fail a family member's turn."""
    import pathlib

    offenders = []
    for path in pathlib.Path("src").rglob("*.py"):
        if "eve/eval" in str(path).replace("\\", "/"):
            continue
        text = path.read_text()
        if "eve.eval" in text or "from eve import eval" in text:
            offenders.append(str(path))
    assert offenders == [], offenders
```

Create `tests/test_eval_integration.py`:

```python
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def clean_pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    pool = await db.get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "TRUNCATE eve_ambient_decision, eve_eval_run, eve_ambient_notice"
        )
    yield pool
    await db.close_pool()


async def test_a_seeded_regression_fails_the_gate(clean_pool):
    """DoD 5: two runs, the second worse, gate exits non-zero - with Langfuse
    never contacted."""
    from eve.eval.store import gate, record_run
    from eve.eval.types import RunScore

    await record_run(RunScore("ambient", "with-rules", 10, {"notify_agreement": 90.0}), "sha1")
    code, reasons = await gate("ambient")
    assert code == 0

    await record_run(RunScore("ambient", "with-rules", 10, {"notify_agreement": 70.0}), "sha2")
    code, reasons = await gate("ambient")
    assert code == 1
    assert any("notify_agreement" in r for r in reasons)


async def test_a_decision_round_trips_into_a_dataset_item(clean_pool):
    """DoD 0 and 1: recorded verdict -> replayable dataset item."""
    from datetime import UTC, datetime

    from eve.eval.datasets import build_ambient
    from eve_ambient.store import record_decision
    from eve_ambient.types import FilterVerdict, Signal

    await record_decision(
        Signal(
            source="mail", key="k1", occurred_at=datetime(2026, 8, 27, tzinfo=UTC),
            member_sub="sub-noah", summary="A package shipped.", payload={"x": 1},
        ),
        FilterVerdict(notify=True, audience=["sub-noah"], why="worth it"),
    )
    items = await build_ambient(limit=10)

    assert len(items) == 1
    assert items[0].input["signal"]["summary"] == "A package shipped."
    assert items[0].expected["notify"] is True


async def test_the_reply_label_is_stamped_and_read(clean_pool):
    """DoD 3: a member turn in an ambient thread stamps replied_at, and the
    dataset join picks it up."""
    from datetime import UTC, datetime

    from eve.eval.datasets import build_ambient
    from eve_ambient.store import mark_replied, record_decision, record_notice
    from eve_ambient.types import FilterVerdict, Signal

    signal = Signal(
        source="mail", key="k1", occurred_at=datetime(2026, 8, 27, tzinfo=UTC),
        member_sub="sub-noah", summary="A package shipped.",
    )
    await record_decision(signal, FilterVerdict(notify=True, audience=["sub-noah"], why="w"))
    await record_notice("sub-noah", "mail", "k1", False, "thread-1")
    await mark_replied("thread-1")

    items = await build_ambient(limit=10)
    assert items[0].expected["replied"] is True
```

- [ ] **Step 2: Run**

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest tests/test_eval_integration.py -m integration -v
uv run pytest
```
Expected: PASS both.

- [ ] **Step 3: Commit**

```bash
git add tests/test_eval_integration.py tests/test_eval_datasets.py
git commit -m "test(5b): pin the import-graph invariant and the gate end to end"
```

---

## Task 14: Documentation and the ADR

**Files:**
- Create: `docs/adr/0009-eval-inputs-from-postgres.md`
- Modify: `README.md`, `docs/architecture.md`, `.env.example`

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0009-eval-inputs-from-postgres.md`:

```markdown
# 9. Eval inputs come from Postgres, not from Langfuse traces

**Status:** Accepted
**Date:** 2026-08-27

## Context

The program spec calls for "an eval harness over Langfuse datasets."
Langfuse holds every trace already, so reconstructing eval inputs from traces
looks like the cheapest path.

It is not. A trace's span tree exists for human debugging, and its shape is set
by Aegra and LiteLLM rather than by this repository — it changes whenever
either is upgraded. Meanwhile `eve_memory`, `eve_ambient_notice` and their
siblings are tables this repository owns.

Phase 4's tables turned out not to hold enough: `eve_ambient_seen` keeps only
`(source, key)`, and `eve_ambient_notice` keeps no signal content, so a
replayable ambient item could not be reconstructed from either.

## Decision

Datasets are **built** from Eve's own Postgres tables and one hand-authored
golden file, and **published** to Langfuse. Scores are written to
`eve_eval_run` locally first; the Langfuse upload is best-effort and its
failure is logged and ignored. `eve-eval gate` reads only Postgres.

The corollary: a subsystem that wants to be evaluated must *record* its
decisions, not merely log them. This is why Phase 5b adds
`eve_ambient_decision` — one row per judged signal, carrying the whole
`Signal` — rather than parsing `pipeline.py`'s resolution log line.

## Consequences

The gate works with Langfuse down, which means a reporting outage can never
block a regression check. Langfuse keeps the one job the local table does not
do cheaply: run-over-run comparison in a UI nobody had to build.

The cost is a recording obligation on any subsystem entering the harness, and
the fact that every dataset is forward-looking from the deploy that starts
recording it. Shape 1 was empty on the day this phase shipped.
```

- [ ] **Step 2: Update `.env.example`**

```bash
# Phase 5b (Eval harness). See docs/superpowers/specs/
# 2026-08-27-eve-eval-harness-design.md section 9.2
EVE_EVAL_DATASET_LIMIT=200
EVE_EVAL_VOICE_CALL_CEILING=60
EVE_EVAL_REGRESSION_POINTS=10
EVE_EVAL_DEAD_RULE_DAYS=90
EVE_EVAL_DECISION_RETENTION_DAYS=180
EVE_EVAL_HYGIENE_APPLY_ENABLED=false
EVE_LANGFUSE_HOST=https://langfuse.chalifour.dev
```

- [ ] **Step 3: Update `docs/architecture.md`**

1. Module map gains the `src/eve/eval/` block with one line per module.
2. The import-graph paragraph: note that `eve_ambient/store.py` now also
   depends on `eve_ambient.types` (for `record_decision`), and that nothing
   in `src/eve/` outside `eve/eval/` imports `eve.eval`.
3. A short "Eval harness" section: the two shapes, the A/B, the REFLEX judge
   and why, and that the gate never calls Langfuse.
4. Note `MIGRATIONS` is now 5 entries and that Phase 5c moves to Alembic.

- [ ] **Step 4: Update `README.md`**

Mark 5b delivered in the phase table; keep 5c pending.

- [ ] **Step 5: Verify**

```bash
uv run python -c "from eve.memory.db import MIGRATIONS; assert len(MIGRATIONS)==5; print('5 migrations')"
uv run pytest
```

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0009-eval-inputs-from-postgres.md README.md docs/architecture.md .env.example
git commit -m "docs(5b): ADR 0009, architecture, README and env for the harness"
```

---

## Definition of Done Traceability

| Spec criterion | Task |
|---|---|
| 0. Decisions recorded with the full Signal; not on stale/error paths; failure never breaks the pipeline | 3 |
| 1. `build` produces both shapes; `gate` skips an empty shape 1 | 5, 9, 13 |
| 2. `run` replays through the real `judge()` and the real graph, both arms | 7, 12 |
| 3. Reply stamps `replied_at`; ambient-marked turn does not | 4, 13 |
| 4. `rule_delta` reported; a seeded harmful rule makes it negative | 6, 8, 12 |
| 5. `gate` exits non-zero per threshold, zero on a clean run, Langfuse unreachable | 9, 10, 13 |
| 6. Under the ceiling unless `--yes`; estimate printed first | 12 |
| 7. Judge spot-check in output with reasons; agreement recorded once by hand | 12 (output), manual (the recording) |
| 8. Canary fails as designed; a passing canary fails the gate | 5, 8, 9 |
| 9. `hygiene` finds a seeded duplicate and supersedes with the setting on; contradictions never applied | 11 |
| 10. `MIGRATIONS` has exactly 5 entries; no production import of `eve.eval` | 1, 13 |

**Criterion 7's second half is a human action, not a task.** After the first
real `eve-eval run`, read the ten spot-check lines, count how many you agree
with, and record the number in `docs/architecture.md`'s eval section. Below
~85%, move the judge to `Tier.DEEP` in `scorers.py` and accept the budget cost.
