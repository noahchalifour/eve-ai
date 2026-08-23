# Eve Phase 4 — Ambient — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `eve-ambient`, a standing service that turns household signals
into proactive Eve messages a family member can reply to.

**Architecture:** A new deployment polls calendar, mail, and finances through
`eve-tools` and receives Home Assistant pushes on a webhook. Each signal runs a
gate chain — dedup/cooldown, a `REFLEX`-tier relevance filter, the Phase 3
permission check, a per-member daily cap, member-local quiet hours — and each
survivor gets a thread created *as that member* against Aegra, a full `eve`
turn to compose the message, and an ntfy push. The `eve` graph itself is not
modified; ambient is a new caller of it.

**Tech Stack:** Python 3.12, uv, FastAPI + uvicorn, httpx, psycopg 3 (existing
pool), langgraph_sdk (as an HTTP client this time, not a server), `caldav`,
pydantic-settings, pytest with the repo's three-tier markers.

**Spec:** [`docs/superpowers/specs/2026-08-23-eve-ambient-design.md`](../specs/2026-08-23-eve-ambient-design.md)

## Global Constraints

Every task's requirements implicitly include these.

- **`eve-ambient` holds no third-party credential.** Every calendar, mail,
  finance, or home API call goes through `eve-tools`' `/invoke`. ADR 0006.
- **The `eve` graph, `EveState`, and `prompts/eve.md` are not modified by this
  phase.** If a task appears to need a graph change, stop and raise it.
- **No exception escapes into the poll loop.** Every external call degrades to
  a logged failure and a safe default. A single broken source must not stop the
  others, and must not kill the process.
- **`EVE_AMBIENT_ENABLED` defaults to `false`.** A deployment that has not been
  deliberately switched on starts, serves `/healthz`, and sends nothing.
- **Permission strings, exactly as the roster spells them:** `mail.read`,
  `finances`, `home.control`, and `calendar.read` (added in Task 1).
- **Defaults:** poll interval 300s, daily cap 6, quiet hours `21:00-07:00`,
  cooldown 6h, calendar lookahead 90 minutes, budget-signal cooldown 720h.
- **One replica.** No leader election exists; two replicas would double-count
  the daily cap.
- **Silence is the safe default.** Any gate that cannot evaluate — a failed
  filter call, an unreachable database — resolves to "do not notify."
- Run the unit tier with `uv run pytest -m "not integration and not live"`.
  Integration needs `docker compose -f docker-compose.test.yml up -d`.

## File Structure

```
src/eve_ambient/
  __init__.py
  types.py          Signal, FilterVerdict, Outcome            (Task 3)
  store.py          eve_ambient_seen / eve_ambient_notice SQL (Task 2)
  sources/
    __init__.py     the SOURCES registry                      (Task 3)
    mail.py                                                   (Task 3)
    finances.py                                               (Task 4)
    calendar.py                                               (Task 6)
    home.py         webhook payload -> Signal (no poll)        (Task 13)
  filter.py         the REFLEX call                           (Task 7)
  gates.py          audience scope, permission, cap, quiet     (Task 8)
  notify.py         thread + eve run + veto + audit log        (Task 11)
  ntfy.py           Notifier protocol + NtfyNotifier           (Task 10)
  pipeline.py       handle_signal: the whole chain, wired      (Task 12)
  app.py            FastAPI, webhook, poll loop                (Task 13)
prompts/ambient_filter.md                                      (Task 7)
src/eve_tools/caldav_client.py                                 (Task 5)
```

Modified: `src/eve/settings.py` and `family.yaml` (Task 1),
`src/eve/memory/db.py` (Task 2), `src/eve_tools/app.py` and
`src/eve_tools/settings.py` (Task 5), `src/eve/auth.py` (Task 9),
`Dockerfile.eve-ambient` and `.github/workflows/build.yml` (Task 16), docs
(Task 17).

The split follows the spec's own seams: a source knows one API and nothing about
gates; a gate is a pure decision over a `Signal` plus a `Member`; `notify.py`
knows Aegra and nothing about why it was called; `pipeline.py` is the only
module that knows the order things happen in. That is what makes the gate chain
testable without a database and the sources testable without a network.

---

### Task 1: Settings, the calendar grant, and roster iteration

**Files:**
- Modify: `src/eve/settings.py` (append a Phase 4 block after the memory block; extend `model_post_init`)
- Modify: `family.yaml` (add `calendar.read` to both members)
- Modify: `src/eve/family.py` (add `Family.members()`)
- Test: `tests/test_settings.py`, `tests/test_family.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.ambient_*` fields (names listed below);
  `Family.members() -> tuple[Member, ...]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings.py`:

```python
def test_phase_4_ambient_defaults():
    s = Settings()
    assert s.ambient_enabled is False
    assert s.ambient_poll_interval_seconds == 300
    assert s.ambient_daily_cap == 6
    assert s.ambient_quiet_hours == "21:00-07:00"
    assert s.ambient_cooldown_hours == 6
    assert s.ambient_calendar_lookahead_minutes == 90
    assert s.ambient_aegra_base_url == "http://eve:2026"
    assert s.ambient_token == ""


def test_a_short_ambient_token_is_refused_at_startup():
    """An impersonation secret is the one credential in this deployment that
    can speak as any family member. A guessable one is worse than none,
    because it fails open rather than closed."""
    with pytest.raises(ValueError, match="EVE_AMBIENT_TOKEN"):
        Settings(ambient_token="short")


def test_an_empty_ambient_token_is_allowed():
    """Ambient off is the default; an unset token must not stop Eve booting."""
    assert Settings(ambient_token="").ambient_token == ""


def test_a_long_ambient_token_is_accepted():
    assert Settings(ambient_token="a" * 32).ambient_token == "a" * 32
```

Append to `tests/test_family.py`:

```python
def test_members_are_iterable_in_roster_order(roster):
    """The poll loop walks the roster per member; without this it would have
    to reach into Family's private dict."""
    assert [m.name for m in roster.members()] == ["Noah", "Kid"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_settings.py -k ambient tests/test_family.py -k members -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'ambient_enabled'` and `AttributeError: 'Family' object has no attribute 'members'`.

- [ ] **Step 3: Implement**

In `src/eve/settings.py`, after the memory block:

```python
    # Phase 4 (Ambient). See docs/superpowers/specs/
    # 2026-08-23-eve-ambient-design.md sections 5 and 8.2.
    #
    # Off by default: this is the one subsystem that speaks without being
    # spoken to, so a deployment that has not deliberately enabled it must
    # send nothing.
    ambient_enabled: bool = False
    ambient_poll_interval_seconds: int = 300
    ambient_daily_cap: int = 6
    ambient_quiet_hours: str = "21:00-07:00"
    ambient_cooldown_hours: int = 6
    ambient_calendar_lookahead_minutes: int = 90
    # The impersonation credential (design section 6.1). Held by eve-ambient,
    # which presents it, and by eve, which verifies it.
    ambient_token: str = ""
    ambient_ha_webhook_secret: str = ""
    ambient_ntfy_base_url: str = ""
    ambient_ntfy_topic: str = ""
    ambient_ntfy_token: str = ""
    ambient_thread_url_template: str = ""
    ambient_aegra_base_url: str = "http://eve:2026"
```

At the end of `model_post_init`:

```python
        if self.ambient_token and len(self.ambient_token) < 32:
            raise ValueError(
                "EVE_AMBIENT_TOKEN must be at least 32 characters: it "
                "authenticates as any family member, so a guessable value "
                "fails open"
            )
```

In `src/eve/family.py`, on `Family`:

```python
    def members(self) -> tuple[Member, ...]:
        """Roster order, for the ambient poll loop. Insertion-ordered dict."""
        return tuple(self._by_sub.values())
```

In `family.yaml`, add `calendar.read` to Noah's and Kendra's `permissions`
lists, with a comment above the first occurrence:

```yaml
      # Phase 4: receiving proactive calendar notifications. No existing
      # grant covered the calendar, because until now nothing read it.
      - calendar.read
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_settings.py tests/test_family.py -v`
Expected: PASS, including the pre-existing tests in both files.

- [ ] **Step 5: Commit**

```bash
git add src/eve/settings.py src/eve/family.py family.yaml tests/test_settings.py tests/test_family.py
git commit -m "feat: ambient settings, the calendar.read grant, and roster iteration"
```

---

### Task 2: The ambient tables and their store module

**Files:**
- Modify: `src/eve/memory/db.py` (append a `0002_ambient` entry to `MIGRATIONS`)
- Create: `src/eve_ambient/__init__.py` (empty), `src/eve_ambient/store.py`
- Test: `tests/test_ambient_store.py`

**Interfaces:**
- Consumes: `eve.memory.db.get_pool`, `Settings.ambient_cooldown_hours`.
- Produces:
  - `async def is_fresh(source: str, key: str, cooldown_hours: int) -> bool`
  - `async def mark_seen(source: str, key: str) -> None`
  - `async def prune_seen(days: int = 30) -> int`
  - `async def record_notice(member_sub: str, source: str, key: str, urgent: bool, thread_id: str | None) -> None`
  - `async def notices_since(member_sub: str, since: datetime) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_store.py`:

```python
"""Integration tests against the real Postgres in docker-compose.test.yml."""

from datetime import UTC, datetime, timedelta

import pytest

from eve.memory import db
from eve_ambient import store

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    from eve.settings import get_settings

    get_settings.cache_clear()
    db._pool = None
    pool = await db.get_pool()
    await db.migrate()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_ambient_seen, eve_ambient_notice")
    yield pool
    db._pool = None


async def test_an_unseen_key_is_fresh(pool):
    assert await store.is_fresh("home", "door:open", 6) is True


async def test_a_seen_key_is_not_fresh_inside_the_window(pool):
    await store.mark_seen("home", "door:open")
    assert await store.is_fresh("home", "door:open", 6) is False


async def test_a_seen_key_is_fresh_again_past_the_window(pool):
    """A door that was open six hours ago and is open again is news again."""
    await store.mark_seen("home", "door:open")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_ambient_seen SET first_seen_at = now() - interval '7 hours'"
        )
    assert await store.is_fresh("home", "door:open", 6) is True


async def test_marking_seen_twice_refreshes_rather_than_erroring(pool):
    await store.mark_seen("home", "door:open")
    await store.mark_seen("home", "door:open")
    assert await store.is_fresh("home", "door:open", 6) is False


async def test_the_same_key_in_two_sources_is_independent(pool):
    await store.mark_seen("home", "shared-key")
    assert await store.is_fresh("mail", "shared-key", 6) is True


async def test_notices_are_counted_per_member_since_an_instant(pool):
    await store.record_notice("sub-noah", "home", "k1", False, "t1")
    await store.record_notice("sub-noah", "mail", "k2", False, "t2")
    await store.record_notice("sub-kendra", "home", "k3", False, "t3")
    since = datetime.now(UTC) - timedelta(hours=1)
    assert await store.notices_since("sub-noah", since) == 2
    assert await store.notices_since("sub-kendra", since) == 1


async def test_notices_before_the_instant_are_not_counted(pool):
    """The daily cap is a window, not a lifetime total."""
    await store.record_notice("sub-noah", "home", "k1", False, "t1")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_ambient_notice SET sent_at = now() - interval '2 days'"
        )
    since = datetime.now(UTC) - timedelta(hours=24)
    assert await store.notices_since("sub-noah", since) == 0


async def test_pruning_removes_only_rows_past_the_horizon(pool):
    await store.mark_seen("home", "old")
    await store.mark_seen("home", "new")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_ambient_seen SET first_seen_at = now() - interval '40 days' "
            "WHERE key = 'old'"
        )
    assert await store.prune_seen(30) == 1
    assert await store.is_fresh("home", "new", 6) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `docker compose -f docker-compose.test.yml up -d && uv run pytest tests/test_ambient_store.py -m integration -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_ambient'`.

- [ ] **Step 3: Implement the migration**

Append to `MIGRATIONS` in `src/eve/memory/db.py`. Note the module's own
`ponytail:` header note — this is the second entry, still well inside the
hand-rolled budget.

```python
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
          first_seen_at timestamptz NOT NULL DEFAULT now(),
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
```

- [ ] **Step 4: Implement the store module**

`src/eve_ambient/__init__.py` is empty. Create `src/eve_ambient/store.py`:

```python
"""Every eve_ambient_seen and eve_ambient_notice SQL statement.

Separate from `eve.memory.store` because it is a different subsystem with a
different lifetime, but it deliberately shares `eve.memory.db`'s pool and
migration list: one Postgres, one migration entrypoint, one place a schema
failure can stop a pod.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row

from eve.memory.db import get_pool


async def _fetchone(sql: str, params: dict) -> dict | None:
    pool = await get_pool()
    async with pool.connection() as conn:
        # Cursor-scoped row factory, not connection-scoped: see the comment
        # in eve.memory.db.migrate() for why the difference matters to a
        # pooled connection.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()


async def _execute(sql: str, params: dict) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(sql, params)


async def is_fresh(source: str, key: str, cooldown_hours: int) -> bool:
    """True when this signal has never been seen, or was last seen longer ago
    than its cooldown window."""
    row = await _fetchone(
        """
        SELECT first_seen_at < now() - make_interval(hours => %(hours)s)
                 AS expired
        FROM eve_ambient_seen
        WHERE source = %(source)s AND key = %(key)s
        """,
        {"source": source, "key": key, "hours": cooldown_hours},
    )
    return True if row is None else bool(row["expired"])


async def mark_seen(source: str, key: str) -> None:
    """Called only once a signal has been *resolved* — dropped by a gate,
    vetoed by Eve, or delivered. Marking on receipt would lose a signal to
    any crash in between (design section 4.5)."""
    await _execute(
        """
        INSERT INTO eve_ambient_seen (source, key) VALUES (%(source)s, %(key)s)
        ON CONFLICT (source, key) DO UPDATE SET first_seen_at = now()
        """,
        {"source": source, "key": key},
    )


async def prune_seen(days: int = 30) -> int:
    row = await _fetchone(
        """
        WITH gone AS (
          DELETE FROM eve_ambient_seen
          WHERE first_seen_at < now() - make_interval(days => %(days)s)
          RETURNING 1
        )
        SELECT count(*) AS n FROM gone
        """,
        {"days": days},
    )
    return int(row["n"]) if row else 0


async def record_notice(
    member_sub: str, source: str, key: str, urgent: bool, thread_id: str | None
) -> None:
    await _execute(
        """
        INSERT INTO eve_ambient_notice (member_sub, source, key, urgent, thread_id)
        VALUES (%(sub)s, %(source)s, %(key)s, %(urgent)s, %(thread)s)
        """,
        {
            "sub": member_sub,
            "source": source,
            "key": key,
            "urgent": urgent,
            "thread": thread_id,
        },
    )


async def notices_since(member_sub: str, since: datetime) -> int:
    row = await _fetchone(
        """
        SELECT count(*) AS n FROM eve_ambient_notice
        WHERE member_sub = %(sub)s AND sent_at >= %(since)s
        """,
        {"sub": member_sub, "since": since},
    )
    return int(row["n"]) if row else 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_store.py -m integration -v`
Expected: PASS, 8 tests.

- [ ] **Step 6: Commit**

```bash
git add src/eve/memory/db.py src/eve_ambient/__init__.py src/eve_ambient/store.py tests/test_ambient_store.py
git commit -m "feat: eve_ambient_seen and eve_ambient_notice, with cooldown and cap counting"
```

---

### Task 3: The Signal type, the source registry, and the mail source

**Files:**
- Create: `src/eve_ambient/types.py`, `src/eve_ambient/sources/__init__.py`, `src/eve_ambient/sources/mail.py`
- Test: `tests/test_ambient_sources_mail.py`

**Interfaces:**
- Consumes: `eve.tools_client.invoke`, `Settings`.
- Produces:
  - `Signal(source, key, occurred_at, member_sub, summary, payload, cooldown_hours=None)` — frozen dataclass.
  - `FilterVerdict(notify: bool, audience: list[str], urgent: bool, why: str)` — pydantic model.
  - `Source(name: str, per_member: bool, permission: str, poll)` — frozen dataclass; `poll` is `async (member_sub: str) -> list[Signal]`.
  - `SOURCES: tuple[Source, ...]`
  - `sources.mail.poll(member_sub: str) -> list[Signal]`
  - `eve_ambient.types.tool_result(raw: str) -> dict | None` — parses `tools_client.invoke`'s string return, `None` on any error string or malformed JSON.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_sources_mail.py`:

```python
import json
from unittest.mock import AsyncMock

from eve_ambient.sources import mail
from eve_ambient.types import tool_result

MESSAGES = {
    "messages": [
        {
            "id": "m1",
            "internalDate": "1787500000000",
            "subject": "Field trip form due Friday",
            "from": "school@example.com",
            "snippet": "Please return the signed form.",
        },
        {
            "id": "m2",
            "internalDate": "1787500600000",
            "subject": "Your package shipped",
            "from": "shop@example.com",
            "snippet": "On its way.",
        },
    ]
}


async def test_each_unread_message_becomes_one_signal(monkeypatch):
    monkeypatch.setattr(mail, "invoke", AsyncMock(return_value=json.dumps(MESSAGES)))
    signals = await mail.poll("sub-noah")
    assert [s.key for s in signals] == ["m1", "m2"]
    assert all(s.source == "mail" for s in signals)
    assert all(s.member_sub == "sub-noah" for s in signals)


async def test_the_summary_names_the_sender_and_subject(monkeypatch):
    """The filter reads `summary` and nothing else, so the one line has to
    carry enough to judge relevance."""
    monkeypatch.setattr(mail, "invoke", AsyncMock(return_value=json.dumps(MESSAGES)))
    first = (await mail.poll("sub-noah"))[0]
    assert "school@example.com" in first.summary
    assert "Field trip form due Friday" in first.summary


async def test_the_query_asks_only_for_recent_unread_mail(monkeypatch):
    invoke = AsyncMock(return_value=json.dumps({"messages": []}))
    monkeypatch.setattr(mail, "invoke", invoke)
    await mail.poll("sub-noah")
    _tool, args = invoke.await_args.args
    assert args["member_sub"] == "sub-noah"
    assert "is:unread" in args["query"]
    assert "newer_than:1d" in args["query"]


async def test_an_eve_tools_error_yields_no_signals(monkeypatch):
    """eve-tools returns error strings rather than raising. A source that let
    that through would report a signal whose summary was an error message."""
    monkeypatch.setattr(
        mail, "invoke", AsyncMock(return_value="error: eve-tools unavailable")
    )
    assert await mail.poll("sub-noah") == []


async def test_malformed_json_yields_no_signals(monkeypatch):
    monkeypatch.setattr(mail, "invoke", AsyncMock(return_value="{not json"))
    assert await mail.poll("sub-noah") == []


def test_tool_result_parses_what_tools_client_returns():
    """tools_client.invoke hands back the already-unwrapped result as JSON."""
    assert tool_result(json.dumps({"messages": []})) == {"messages": []}


def test_tool_result_rejects_an_error_string():
    assert tool_result("error: whatever") is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ambient_sources_mail.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_ambient.sources'`.

- [ ] **Step 3: Implement the types**

Create `src/eve_ambient/types.py`:

```python
"""Shapes only. No I/O, no behaviour beyond parsing one string."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Signal:
    source: str
    key: str
    occurred_at: datetime
    member_sub: str | None
    summary: str
    payload: dict = field(default_factory=dict)
    # None means "the configured default". A source that knows its signal
    # should stay quiet longer than six hours says so here (design 4.3).
    cooldown_hours: int | None = None


# Pydantic, not a dataclass: this is the structured-output schema handed to
# the REFLEX model, the same way memory/types.py's Extraction is.
class FilterVerdict(BaseModel):
    notify: bool = False
    audience: list[str] = Field(
        default_factory=list, description="Family member subs to notify."
    )
    urgent: bool = False
    why: str = Field(default="", description="One sentence of reasoning.")


def tool_result(raw: str) -> dict | None:
    """Unwrap what `eve.tools_client.invoke` returns.

    It answers a JSON string on success and a human-readable `error: ...`
    string on failure, because its usual caller hands the value straight to a
    model. Ambient needs structure, so anything that is not parseable JSON is
    a failure here, not data.
    """
    if raw.startswith("error:"):
        logger.warning("eve-tools reported: %s", raw)
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("eve-tools returned unparseable JSON: %.80s", raw)
        return None
    if not isinstance(parsed, dict):
        return None
    # `invoke` already unwraps eve-tools' {"result": ...} envelope. The
    # fallback covers a caller that hands over a raw eve-tools body instead.
    inner = parsed.get("result", parsed)
    return inner if isinstance(inner, dict) else None
```

- [ ] **Step 4: Implement the mail source and the registry**

Create `src/eve_ambient/sources/mail.py`:

```python
"""Unread mail as signals, via eve-tools' existing Gmail client."""

from __future__ import annotations

from datetime import UTC, datetime

from eve.tools_client import invoke
from eve_ambient.types import Signal, tool_result

_QUERY = "is:unread newer_than:1d"


def _occurred_at(message: dict) -> datetime:
    """Gmail's internalDate is epoch milliseconds as a string. A missing or
    malformed value must not lose the signal, so it falls back to now."""
    try:
        return datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC)
    except (KeyError, TypeError, ValueError):
        return datetime.now(UTC)


async def poll(member_sub: str) -> list[Signal]:
    result = tool_result(
        await invoke("mail.list_messages", {"member_sub": member_sub, "query": _QUERY})
    )
    if result is None:
        return []
    signals = []
    for message in result.get("messages") or []:
        sender = message.get("from", "unknown sender")
        subject = message.get("subject", "(no subject)")
        snippet = message.get("snippet", "")
        signals.append(
            Signal(
                source="mail",
                key=str(message.get("id", "")),
                occurred_at=_occurred_at(message),
                member_sub=member_sub,
                summary=f"Unread mail from {sender}: {subject}. {snippet}".strip(),
                payload=message,
            )
        )
    return [s for s in signals if s.key]
```

Create `src/eve_ambient/sources/__init__.py`:

```python
"""The polled-source registry. `home` is absent deliberately: it is pushed,
not polled (design section 4.4)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from eve_ambient.sources import mail
from eve_ambient.types import Signal


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    # True: polled once per member holding `permission`, with their sub.
    # False: polled once for the household, with an empty sub.
    per_member: bool
    permission: str
    poll: Callable[[str], Awaitable[list[Signal]]]


SOURCES: tuple[Source, ...] = (
    Source("mail", True, "mail.read", mail.poll),
)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_sources_mail.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
git add src/eve_ambient/types.py src/eve_ambient/sources tests/test_ambient_sources_mail.py
git commit -m "feat: the Signal type, the source registry, and unread mail as signals"
```

---

### Task 4: The finances source

**Files:**
- Create: `src/eve_ambient/sources/finances.py`
- Modify: `src/eve_ambient/sources/__init__.py` (register it)
- Test: `tests/test_ambient_sources_finances.py`

**Interfaces:**
- Consumes: `eve.tools_client.invoke`, `Signal`, `tool_result`.
- Produces: `sources.finances.poll(member_sub: str) -> list[Signal]` — ignores
  its argument (household scope) and emits `member_sub=None` signals.
  `BUDGET_COOLDOWN_HOURS = 720`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_sources_finances.py`:

```python
import json
from unittest.mock import AsyncMock

from eve_ambient.sources import finances

TRANSACTIONS = {
    "transactions": [
        {"id": "t1", "amount": -842.19, "merchant": "Dentist", "date": "2026-08-23"},
        {"id": "t2", "amount": -12.40, "merchant": "Coffee", "date": "2026-08-23"},
    ]
}
BUDGETS = {
    "budgets": [
        {"id": "b1", "category": "Groceries", "period": "2026-08", "spent": 910.0, "limit": 800.0},
        {"id": "b2", "category": "Fuel", "period": "2026-08", "spent": 120.0, "limit": 300.0},
    ]
}


def _fake_invoke(**by_tool):
    async def _invoke(tool, args, **kwargs):
        return json.dumps(by_tool.get(tool, {}))

    return AsyncMock(side_effect=_invoke)


async def test_transactions_and_overrun_budgets_both_become_signals(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": TRANSACTIONS,
            "finances.get_budgets": BUDGETS,
        }),
    )
    keys = [s.key for s in await finances.poll("")]
    assert "t1" in keys and "t2" in keys
    assert "budget:b1:2026-08:over" in keys


async def test_a_budget_within_its_limit_is_not_a_signal(monkeypatch):
    """Nothing has happened. A signal per budget per poll would burn the
    filter's whole day on non-events."""
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": []},
            "finances.get_budgets": BUDGETS,
        }),
    )
    assert [s.key for s in await finances.poll("")] == ["budget:b1:2026-08:over"]


async def test_a_budget_signal_stays_quiet_for_a_month(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": {"transactions": []},
            "finances.get_budgets": BUDGETS,
        }),
    )
    budget = (await finances.poll(""))[0]
    assert budget.cooldown_hours == finances.BUDGET_COOLDOWN_HOURS == 720


async def test_transactions_use_the_default_cooldown(monkeypatch):
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": TRANSACTIONS,
            "finances.get_budgets": {"budgets": []},
        }),
    )
    assert all(s.cooldown_hours is None for s in await finances.poll(""))


async def test_signals_are_household_scoped(monkeypatch):
    """Money is shared; the audience comes from the filter, not the account."""
    monkeypatch.setattr(
        finances,
        "invoke",
        _fake_invoke(**{
            "finances.list_transactions": TRANSACTIONS,
            "finances.get_budgets": {"budgets": []},
        }),
    )
    assert all(s.member_sub is None for s in await finances.poll(""))


async def test_one_failing_call_does_not_lose_the_other(monkeypatch):
    async def _invoke(tool, args, **kwargs):
        if tool == "finances.get_budgets":
            return "error: monarch unavailable"
        return json.dumps(TRANSACTIONS)

    monkeypatch.setattr(finances, "invoke", AsyncMock(side_effect=_invoke))
    assert [s.key for s in await finances.poll("")] == ["t1", "t2"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ambient_sources_finances.py -v`
Expected: FAIL — `ImportError: cannot import name 'finances'`.

- [ ] **Step 3: Implement**

Create `src/eve_ambient/sources/finances.py`:

```python
"""Transactions and budget overruns as signals, via eve-tools' Monarch client.

Household-scoped: every signal carries `member_sub=None` and the audience is
the filter's decision (design section 5).
"""

from __future__ import annotations

from datetime import UTC, datetime

from eve.tools_client import invoke
from eve_ambient.types import Signal, tool_result

# A budget that is over stays over for the rest of the month. Six hours would
# mean four notifications a day about one fact.
BUDGET_COOLDOWN_HOURS = 720

_TRANSACTION_LIMIT = 50


def _parsed_date(raw: str | None) -> datetime:
    try:
        return datetime.fromisoformat(str(raw)).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


async def _transactions() -> list[Signal]:
    result = tool_result(
        await invoke("finances.list_transactions", {"limit": _TRANSACTION_LIMIT})
    )
    if result is None:
        return []
    signals = []
    for txn in result.get("transactions") or []:
        key = str(txn.get("id", ""))
        if not key:
            continue
        amount = txn.get("amount")
        merchant = txn.get("merchant", "unknown merchant")
        signals.append(
            Signal(
                source="finances",
                key=key,
                occurred_at=_parsed_date(txn.get("date")),
                member_sub=None,
                summary=f"Transaction: {amount} at {merchant} on {txn.get('date')}.",
                payload=txn,
            )
        )
    return signals


async def _budget_overruns() -> list[Signal]:
    result = tool_result(await invoke("finances.get_budgets", {}))
    if result is None:
        return []
    signals = []
    for budget in result.get("budgets") or []:
        spent, limit = budget.get("spent"), budget.get("limit")
        if not isinstance(spent, (int, float)) or not isinstance(limit, (int, float)):
            continue
        if spent <= limit:
            continue
        period = budget.get("period", "")
        signals.append(
            Signal(
                source="finances",
                # The state is in the key, so crossing back under and over
                # again is a new signal rather than a suppressed one.
                key=f"budget:{budget.get('id')}:{period}:over",
                occurred_at=datetime.now(UTC),
                member_sub=None,
                summary=(
                    f"Budget over: {budget.get('category')} for {period} is at "
                    f"{spent} against a limit of {limit}."
                ),
                payload=budget,
                cooldown_hours=BUDGET_COOLDOWN_HOURS,
            )
        )
    return signals


async def poll(member_sub: str) -> list[Signal]:
    """`member_sub` is unused: finances are household-scoped. The parameter
    exists so every source in the registry has one shape."""
    return [*await _transactions(), *await _budget_overruns()]
```

Register it in `src/eve_ambient/sources/__init__.py`:

```python
from eve_ambient.sources import finances, mail
...
SOURCES: tuple[Source, ...] = (
    Source("mail", True, "mail.read", mail.poll),
    Source("finances", False, "finances", finances.poll),
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_sources_finances.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/sources tests/test_ambient_sources_finances.py
git commit -m "feat: transactions and budget overruns as ambient signals"
```

---

### Task 5: The CalDAV client in `eve-tools`

**Files:**
- Create: `src/eve_tools/caldav_client.py`
- Modify: `src/eve_tools/settings.py` (add `caldav_credentials_json`), `src/eve_tools/app.py` (add the `calendar.list_events` entry), `pyproject.toml` (add `caldav`)
- Test: `tests/test_eve_tools_caldav.py`

**Interfaces:**
- Consumes: `eve_tools.settings.get_tools_settings`.
- Produces: `caldav_client.list_events(member_sub: str, lookahead_minutes: int) -> dict`
  returning `{"events": [{"uid", "revision", "summary", "start", "end", "location"}]}`
  with ISO-8601 UTC strings for `start`/`end`. Registered as the
  `calendar.list_events` tool name.

**Note on `revision`:** the spec calls this an etag. The implementation uses a
content hash of the event's iCalendar body instead, because it needs no extra
request per event and does not depend on a server returning etags from a
search. Same job — a value that changes when the event changes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_eve_tools_caldav.py`:

```python
"""The caldav library is synchronous and talks to a real server, so these
tests replace the calendar-lookup seam and exercise everything above it."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from eve_tools import caldav_client


class FakeEvent:
    def __init__(self, data: str):
        self.data = data


def _ics(uid: str, summary: str, start: str) -> str:
    return (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nSUMMARY:{summary}\r\n"
        f"DTSTART:{start}\r\nDTEND:{start}\r\n"
        "LOCATION:Office\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )


@pytest.fixture
def one_calendar(monkeypatch):
    events = [FakeEvent(_ics("uid-1", "Dentist", "20260823T150000Z"))]
    calendar = SimpleNamespace(search=lambda **kwargs: events)
    monkeypatch.setattr(caldav_client, "_calendars", lambda sub: [calendar])
    return events


async def test_an_event_becomes_one_dict(one_calendar):
    result = await caldav_client.list_events("sub-noah", 90)
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["uid"] == "uid-1"
    assert event["summary"] == "Dentist"
    assert event["location"] == "Office"
    assert event["start"] == "2026-08-23T15:00:00+00:00"


async def test_the_revision_changes_when_the_event_changes(one_calendar):
    first = (await caldav_client.list_events("sub-noah", 90))["events"][0]["revision"]
    one_calendar[0].data = _ics("uid-1", "Dentist MOVED", "20260823T170000Z")
    second = (await caldav_client.list_events("sub-noah", 90))["events"][0]["revision"]
    assert first != second


async def test_the_revision_is_stable_when_nothing_changes(one_calendar):
    first = (await caldav_client.list_events("sub-noah", 90))["events"][0]["revision"]
    second = (await caldav_client.list_events("sub-noah", 90))["events"][0]["revision"]
    assert first == second


async def test_an_unparseable_event_is_skipped_not_fatal(monkeypatch):
    """One malformed event on a shared calendar must not blind Eve to the
    rest of the day."""
    events = [FakeEvent("not a calendar at all"), FakeEvent(_ics("uid-2", "Soccer", "20260823T180000Z"))]
    monkeypatch.setattr(
        caldav_client, "_calendars", lambda sub: [SimpleNamespace(search=lambda **kw: events)]
    )
    result = await caldav_client.list_events("sub-noah", 90)
    assert [e["uid"] for e in result["events"]] == ["uid-2"]


async def test_a_member_without_credentials_gets_an_empty_list(monkeypatch):
    monkeypatch.setattr(
        caldav_client, "_credentials_for", lambda sub: (_ for _ in ()).throw(KeyError(sub))
    )
    assert await caldav_client.list_events("sub-nobody", 90) == {"events": []}


async def test_the_search_window_starts_now_and_spans_the_lookahead(monkeypatch):
    captured = {}

    def _search(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        caldav_client, "_calendars", lambda sub: [SimpleNamespace(search=_search)]
    )
    await caldav_client.list_events("sub-noah", 90)
    span = captured["end"] - captured["start"]
    assert 89 <= span.total_seconds() / 60 <= 91
    assert captured["event"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_eve_tools_caldav.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_tools.caldav_client'`.

- [ ] **Step 3: Add the dependency**

```bash
uv add caldav
```

- [ ] **Step 4: Implement**

Create `src/eve_tools/caldav_client.py`:

```python
"""CalDAV client. One credential per family member, the same shape gmail.py
uses: caldav_credentials_json holds a JSON object keyed by member sub, each
value {"url": ..., "username": ..., "password": ...}.

The caldav library is synchronous, so every call runs in a thread via
asyncio.to_thread, exactly as gmail.py does, so one slow calendar server does
not block eve-tools' event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

import caldav
import icalendar

from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)


def _credentials_for(member_sub: str) -> dict:
    all_creds = json.loads(get_tools_settings().caldav_credentials_json or "{}")
    return all_creds[member_sub]


def _calendars(member_sub: str) -> list:
    creds = _credentials_for(member_sub)
    client = caldav.DAVClient(
        url=creds["url"], username=creds["username"], password=creds["password"]
    )
    return client.principal().calendars()


def _as_utc_iso(value) -> str | None:
    """An icalendar dtstart is a datetime or a date. An all-day event has no
    time; midnight UTC is the only sane reading, and losing the event because
    it lacks a clock would be worse."""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat()
    return datetime(value.year, value.month, value.day, tzinfo=UTC).isoformat()


def _to_dict(raw: str) -> dict | None:
    try:
        component = next(
            part
            for part in icalendar.Calendar.from_ical(raw).walk()
            if part.name == "VEVENT"
        )
    except Exception:
        logger.warning("skipping an unparseable calendar event")
        return None
    return {
        "uid": str(component.get("uid", "")),
        # A content hash, not a server etag: no extra request, and it works
        # on servers that omit etags from search results.
        "revision": hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16],
        "summary": str(component.get("summary", "")),
        "location": str(component.get("location", "")),
        "start": _as_utc_iso(getattr(component.get("dtstart"), "dt", None)),
        "end": _as_utc_iso(getattr(component.get("dtend"), "dt", None)),
    }


async def list_events(member_sub: str, lookahead_minutes: int) -> dict:
    def _run() -> dict:
        try:
            calendars = _calendars(member_sub)
        except Exception:
            logger.warning("no reachable calendar for %s", member_sub, exc_info=True)
            return {"events": []}
        start = datetime.now(UTC)
        end = start + timedelta(minutes=lookahead_minutes)
        events = []
        for calendar in calendars:
            for found in calendar.search(start=start, end=end, event=True, expand=True):
                parsed = _to_dict(found.data)
                if parsed and parsed["uid"]:
                    events.append(parsed)
        return {"events": events}

    return await asyncio.to_thread(_run)
```

In `src/eve_tools/settings.py`, add to `ToolsSettings`:

```python
    caldav_credentials_json: str = ""
```

In `src/eve_tools/app.py`, import `caldav_client` alongside the others and add
to the tool table:

```python
    "calendar.list_events": lambda a: caldav_client.list_events(
        a["member_sub"], a.get("lookahead_minutes", 90)
    ),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_eve_tools_caldav.py tests/test_eve_tools_app.py -v`
Expected: PASS. `test_eve_tools_app.py` must still pass — the new table entry
is additive.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/eve_tools/caldav_client.py src/eve_tools/settings.py src/eve_tools/app.py tests/test_eve_tools_caldav.py
git commit -m "feat: a CalDAV client in eve-tools, exposed as calendar.list_events"
```

---

### Task 6: The calendar source

**Files:**
- Create: `src/eve_ambient/sources/calendar.py`
- Modify: `src/eve_ambient/sources/__init__.py` (register it)
- Test: `tests/test_ambient_sources_calendar.py`

**Interfaces:**
- Consumes: `eve.tools_client.invoke`, `Settings.ambient_calendar_lookahead_minutes`, `Signal`, `tool_result`.
- Produces: `sources.calendar.poll(member_sub: str) -> list[Signal]` emitting two
  key shapes: `"<uid>:start:<iso>"` for an event entering the window and
  `"<uid>:rev:<revision>"` for a changed event.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_sources_calendar.py`:

```python
import json
from unittest.mock import AsyncMock

from eve_ambient.sources import calendar

EVENTS = {
    "events": [
        {
            "uid": "uid-1",
            "revision": "abc123",
            "summary": "Dentist",
            "location": "Main St",
            "start": "2026-08-23T15:00:00+00:00",
            "end": "2026-08-23T16:00:00+00:00",
        }
    ]
}


def _invoke_returning(payload):
    # tools_client.invoke already unwraps eve-tools' {"result": ...} envelope
    # and returns the inner object as a JSON string.
    return AsyncMock(return_value=json.dumps(payload))


async def test_an_upcoming_event_produces_a_start_signal(monkeypatch):
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    keys = [s.key for s in await calendar.poll("sub-noah")]
    assert "uid-1:start:2026-08-23T15:00:00+00:00" in keys


async def test_an_upcoming_event_also_produces_a_revision_signal(monkeypatch):
    """A reschedule changes the revision, so a fresh revision key is how a
    moved or cancelled event reaches Eve before its start window."""
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    keys = [s.key for s in await calendar.poll("sub-noah")]
    assert "uid-1:rev:abc123" in keys


async def test_the_summary_carries_the_time_and_place(monkeypatch):
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    start_signal = next(
        s for s in await calendar.poll("sub-noah") if ":start:" in s.key
    )
    assert "Dentist" in start_signal.summary
    assert "Main St" in start_signal.summary


async def test_signals_are_scoped_to_the_member_whose_calendar_it_is(monkeypatch):
    monkeypatch.setattr(calendar, "invoke", _invoke_returning(EVENTS))
    assert all(s.member_sub == "sub-noah" for s in await calendar.poll("sub-noah"))


async def test_the_lookahead_from_settings_is_passed_through(monkeypatch):
    invoke = _invoke_returning({"events": []})
    monkeypatch.setattr(calendar, "invoke", invoke)
    await calendar.poll("sub-noah")
    _tool, args = invoke.await_args.args
    assert args["lookahead_minutes"] == 90


async def test_an_event_without_a_uid_is_skipped(monkeypatch):
    monkeypatch.setattr(
        calendar, "invoke", _invoke_returning({"events": [{"summary": "Ghost"}]})
    )
    assert await calendar.poll("sub-noah") == []


async def test_an_eve_tools_error_yields_no_signals(monkeypatch):
    monkeypatch.setattr(
        calendar, "invoke", AsyncMock(return_value="error: caldav unavailable")
    )
    assert await calendar.poll("sub-noah") == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ambient_sources_calendar.py -v`
Expected: FAIL — `ImportError: cannot import name 'calendar'`.

- [ ] **Step 3: Implement**

Create `src/eve_ambient/sources/calendar.py`:

```python
"""Calendar events as signals, via eve-tools' CalDAV client.

Two signal shapes per event, and they answer different questions. The `start`
key answers "this is about to happen"; the `rev` key answers "this changed."
Both are content-keyed, so neither needs a stored cursor: a start time only
enters the window once, and a revision only appears once per edit.
"""

from __future__ import annotations

from datetime import UTC, datetime

from eve.settings import get_settings
from eve.tools_client import invoke
from eve_ambient.types import Signal, tool_result


def _occurred_at(start: str | None) -> datetime:
    try:
        return datetime.fromisoformat(str(start))
    except (TypeError, ValueError):
        return datetime.now(UTC)


async def poll(member_sub: str) -> list[Signal]:
    lookahead = get_settings().ambient_calendar_lookahead_minutes
    result = tool_result(
        await invoke(
            "calendar.list_events",
            {"member_sub": member_sub, "lookahead_minutes": lookahead},
        )
    )
    if result is None:
        return []

    signals: list[Signal] = []
    for event in result.get("events") or []:
        uid = str(event.get("uid") or "")
        if not uid:
            continue
        title = event.get("summary") or "(untitled event)"
        where = f" at {event['location']}" if event.get("location") else ""
        start = event.get("start")
        occurred = _occurred_at(start)
        signals.append(
            Signal(
                source="calendar",
                key=f"{uid}:start:{start}",
                occurred_at=occurred,
                member_sub=member_sub,
                summary=f"Upcoming: {title}{where}, starting {start}.",
                payload=event,
            )
        )
        revision = event.get("revision")
        if revision:
            signals.append(
                Signal(
                    source="calendar",
                    key=f"{uid}:rev:{revision}",
                    occurred_at=occurred,
                    member_sub=member_sub,
                    summary=f"Calendar entry changed: {title}{where}, now starting {start}.",
                    payload=event,
                )
            )
    return signals
```

Register it in `src/eve_ambient/sources/__init__.py`:

```python
from eve_ambient.sources import calendar, finances, mail
...
SOURCES: tuple[Source, ...] = (
    Source("calendar", True, "calendar.read", calendar.poll),
    Source("mail", True, "mail.read", mail.poll),
    Source("finances", False, "finances", finances.poll),
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_sources_calendar.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/sources tests/test_ambient_sources_calendar.py
git commit -m "feat: upcoming and changed calendar events as ambient signals"
```

---

### Task 7: The REFLEX relevance filter

**Files:**
- Create: `src/eve_ambient/filter.py`, `prompts/ambient_filter.md`
- Test: `tests/test_ambient_filter.py`

**Interfaces:**
- Consumes: `Signal`, `FilterVerdict`, `eve.models.get_model`, `eve.family.get_family`, `eve.memory.store.load_always_on`.
- Produces: `async def judge(signal: Signal) -> FilterVerdict`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_filter.py`:

```python
from datetime import UTC, datetime

import pytest

from eve_ambient import filter as ambient_filter
from eve_ambient.types import FilterVerdict, Signal

SIGNAL = Signal(
    source="home",
    key="binary_sensor.garage:open",
    occurred_at=datetime(2026, 8, 23, 2, 0, tzinfo=UTC),
    member_sub=None,
    summary="The garage door has been open for 40 minutes.",
    payload={"entity_id": "binary_sensor.garage", "state": "open"},
)


class FakeStructuredModel:
    def __init__(self, verdict=None, error=None):
        self.verdict, self.error, self.prompt = verdict, error, None

    def with_structured_output(self, schema):
        return self

    async def ainvoke(self, messages, *args, **kwargs):
        self.prompt = messages[0].content
        if self.error:
            raise self.error
        return self.verdict


@pytest.fixture
def no_household_memory(monkeypatch):
    async def _load_always_on(sub, thread):
        return [], [], None

    monkeypatch.setattr(ambient_filter, "load_always_on", _load_always_on)


async def test_the_verdict_is_returned_as_given(monkeypatch, no_household_memory):
    verdict = FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="ok")
    monkeypatch.setattr(
        ambient_filter, "get_model", lambda tier: FakeStructuredModel(verdict=verdict)
    )
    assert await ambient_filter.judge(SIGNAL) == verdict


async def test_a_failing_model_call_means_do_not_notify(monkeypatch, no_household_memory):
    """Silence is the safe default: a filter outage must not become a
    notification storm, and it must not raise into the poll loop either."""
    monkeypatch.setattr(
        ambient_filter,
        "get_model",
        lambda tier: FakeStructuredModel(error=RuntimeError("litellm down")),
    )
    verdict = await ambient_filter.judge(SIGNAL)
    assert verdict.notify is False
    assert "unavailable" in verdict.why


async def test_the_prompt_carries_the_summary_the_roster_and_the_time(
    monkeypatch, no_household_memory
):
    model = FakeStructuredModel(verdict=FilterVerdict())
    monkeypatch.setattr(ambient_filter, "get_model", lambda tier: model)
    await ambient_filter.judge(SIGNAL)
    assert SIGNAL.summary in model.prompt
    assert "Noah" in model.prompt
    assert "2026-08-23" in model.prompt


async def test_household_memory_reaches_the_prompt(monkeypatch):
    """Without it the filter re-tells the family things they already know."""
    from eve.memory.types import Memory

    fact = Memory(
        id="1", layer="household", scope_kind="household", scope_id="",
        kind="fact", subject=None, content="The garage door sticks in humidity.",
        confidence=0.9, salience=0.8,
        created_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
    )

    async def _load_always_on(sub, thread):
        return [], [fact], None

    monkeypatch.setattr(ambient_filter, "load_always_on", _load_always_on)
    model = FakeStructuredModel(verdict=FilterVerdict())
    monkeypatch.setattr(ambient_filter, "get_model", lambda tier: model)
    await ambient_filter.judge(SIGNAL)
    assert "sticks in humidity" in model.prompt


async def test_a_memory_read_failure_still_produces_a_verdict(monkeypatch):
    """Postgres being unreachable should cost the filter its context, not its
    ability to answer."""
    async def _boom(sub, thread):
        raise RuntimeError("no database")

    monkeypatch.setattr(ambient_filter, "load_always_on", _boom)
    verdict = FilterVerdict(notify=True, audience=["sub-noah"], why="fine")
    monkeypatch.setattr(
        ambient_filter, "get_model", lambda tier: FakeStructuredModel(verdict=verdict)
    )
    assert (await ambient_filter.judge(SIGNAL)).notify is True


async def test_the_reflex_tier_is_the_one_used(monkeypatch, no_household_memory):
    """Ambient filtering runs on every household signal; it must never spend
    the conversational tier's quota (models.py's REFLEX comment)."""
    from eve.models import Tier

    seen = {}

    def _get_model(tier):
        seen["tier"] = tier
        return FakeStructuredModel(verdict=FilterVerdict())

    monkeypatch.setattr(ambient_filter, "get_model", _get_model)
    await ambient_filter.judge(SIGNAL)
    assert seen["tier"] is Tier.REFLEX
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ambient_filter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_ambient.filter'`.

- [ ] **Step 3: Write the prompt**

Create `prompts/ambient_filter.md`:

```markdown
You are the relevance gate in front of Eve, a family assistant. A signal has
arrived from the household. Decide whether it is worth interrupting anyone
over, and if so, who.

Default to NOT notifying. A wrong interruption costs far more than a missed
one: the family mutes an assistant that cries wolf, and then the important
signal never lands either. Notify only when a specific person would plausibly
want to be told this, right now, by a person who knows the household.

Do not notify when:
- the signal is routine, expected, or already known from household memory
- nobody could act on it, and nobody would care that it happened
- it is a repeat of something the family clearly already handles themselves

Set `audience` to the family member subs who should hear it. Prefer the
smallest audience that makes sense. An empty audience with `notify: true` is
meaningless, so leave `notify: false` if you cannot name anyone.

Set `urgent` ONLY for a genuine safety condition: fire or smoke, water where
water should not be, a security breach, or a medical emergency. `urgent`
bypasses the family's daily notification cap AND their quiet hours, so an
urgent verdict at 3am wakes a house. Nothing about money, mail, or a calendar
is urgent. An open door is not urgent unless something in the signal says the
house is being entered.

Set `why` to one sentence explaining the decision, whichever way it went. It
is read by a human reviewing Eve's judgment, not by a model.
```

- [ ] **Step 4: Implement**

Create `src/eve_ambient/filter.py`:

```python
"""The REFLEX-tier relevance gate: is this worth interrupting anyone over?

Structured output through the same mechanism memory/extract.py uses. Every
failure here resolves to "do not notify" — a filter that cannot decide must
not decide yes.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache

from langchain_core.messages import HumanMessage

from eve.family import get_family
from eve.memory.store import load_always_on
from eve.models import Tier, get_model
from eve.settings import get_settings
from eve_ambient.types import FilterVerdict, Signal

logger = logging.getLogger(__name__)

_PAYLOAD_CHARS = 800


@lru_cache(maxsize=1)
def load_filter_prompt() -> str:
    return (get_settings().prompt_file.parent / "ambient_filter.md").read_text()


async def _household_context() -> str:
    """Household memory only. Profile memory is deliberately not read: the
    audience is not known yet, and the compose turn does full recall anyway
    (design section 5)."""
    try:
        _profile, household, _digest = await load_always_on("", None)
    except Exception:
        logger.warning("household memory unavailable to the filter", exc_info=True)
        return "(household memory unavailable)"
    if not household:
        return "(nothing recorded)"
    return "\n".join(f"- {memory.content}" for memory in household)


def _roster_block() -> str:
    return "\n".join(
        f"- {member.name} ({member.role}, {member.timezone}), sub={member.sub}"
        for member in get_family().members()
    )


def _render(signal: Signal, household: str) -> str:
    return (
        f"{load_filter_prompt()}\n\n"
        f"## The family\n{_roster_block()}\n\n"
        f"## Household memory\n{household}\n\n"
        f"## The signal\n"
        f"Source: {signal.source}\n"
        f"Occurred at: {signal.occurred_at.isoformat()}\n"
        f"Belongs to: {signal.member_sub or 'the household'}\n"
        f"Summary: {signal.summary}\n"
        f"Detail: {json.dumps(signal.payload, default=str)[:_PAYLOAD_CHARS]}\n"
    )


async def judge(signal: Signal) -> FilterVerdict:
    try:
        prompt = _render(signal, await _household_context())
        model = get_model(Tier.REFLEX).with_structured_output(FilterVerdict)
        verdict = await model.ainvoke([HumanMessage(prompt)])
    except Exception:
        logger.warning("ambient filter failed for %s", signal.key, exc_info=True)
        return FilterVerdict(notify=False, why="filter unavailable")
    logger.info(
        "ambient filter verdict source=%s key=%s notify=%s urgent=%s why=%s",
        signal.source, signal.key, verdict.notify, verdict.urgent, verdict.why,
    )
    return verdict
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_filter.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 6: Commit**

```bash
git add src/eve_ambient/filter.py prompts/ambient_filter.md tests/test_ambient_filter.py
git commit -m "feat: the REFLEX relevance filter, defaulting to silence"
```

---

### Task 8: The gates — audience scope, permission, cap window, quiet hours

**Files:**
- Create: `src/eve_ambient/gates.py`
- Test: `tests/test_ambient_gates.py`

**Interfaces:**
- Consumes: `Signal`, `eve.family.get_family`, `eve.specialists.permissions.permission_denial`.
- Produces:
  - `SOURCE_PERMISSION: dict[str, str]`
  - `def scoped_audience(signal: Signal, audience: list[str]) -> list[str]`
  - `def permitted(signal: Signal, subs: list[str]) -> list[str]`
  - `def parse_window(window: str) -> tuple[time, time]`
  - `def in_quiet_hours(when_local: datetime, window: str) -> bool`
  - `def day_start_utc(timezone: str, now_utc: datetime) -> datetime`
  - `def local_now(timezone: str, now_utc: datetime) -> datetime`

Every function here is pure. That is deliberate: these are the decisions most
likely to be wrong, and pure functions let the tests cover a midnight
boundary and two timezones without a database or a clock.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_gates.py`:

```python
from datetime import UTC, datetime, time

import pytest

from eve_ambient import gates
from eve_ambient.types import Signal

ROSTER = """
members:
  - sub: "sub-noah"
    name: "Noah"
    role: adult
    timezone: "America/Vancouver"
    permissions: [mail.read, finances, home.control, calendar.read]
  - sub: "sub-kid"
    name: "Kid"
    role: child
    timezone: "America/Toronto"
    permissions: [home.control]
"""


@pytest.fixture(autouse=True)
def roster(tmp_path, monkeypatch):
    path = tmp_path / "family.yaml"
    path.write_text(ROSTER)
    monkeypatch.setenv("EVE_FAMILY_FILE", str(path))
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    yield
    get_settings.cache_clear()
    get_family.cache_clear()


def _signal(source: str, member_sub: str | None = None) -> Signal:
    return Signal(
        source=source, key="k", occurred_at=datetime.now(UTC),
        member_sub=member_sub, summary="s", payload={},
    )


def test_mail_may_only_notify_its_own_owner():
    """Private correspondence is not the filter's to redistribute, and no
    permission string expresses "may read Noah's mail"."""
    audience = gates.scoped_audience(_signal("mail", "sub-noah"), ["sub-noah", "sub-kid"])
    assert audience == ["sub-noah"]


def test_a_calendar_signal_may_notify_someone_else():
    """A family calendar is shared logistics: a kid's game on one calendar is
    news for the parent doing the driving."""
    audience = gates.scoped_audience(_signal("calendar", "sub-noah"), ["sub-kid"])
    assert audience == ["sub-kid"]


def test_a_household_signal_keeps_the_filters_whole_audience():
    audience = gates.scoped_audience(_signal("finances", None), ["sub-noah", "sub-kid"])
    assert audience == ["sub-noah", "sub-kid"]


def test_a_member_lacking_the_permission_is_dropped():
    assert gates.permitted(_signal("finances"), ["sub-noah", "sub-kid"]) == ["sub-noah"]


def test_a_member_holding_the_permission_is_kept():
    assert gates.permitted(_signal("home"), ["sub-kid"]) == ["sub-kid"]


def test_an_unknown_subject_is_dropped_rather_than_raising():
    """The filter names subs; a hallucinated one must not kill the tick."""
    assert gates.permitted(_signal("home"), ["sub-nobody"]) == []


def test_an_unknown_source_permits_nobody():
    """Fail closed: a source added without a permission mapping notifies
    no one instead of everyone."""
    assert gates.permitted(_signal("weather"), ["sub-noah"]) == []


def test_the_window_parses_to_two_times():
    assert gates.parse_window("21:00-07:00") == (time(21, 0), time(7, 0))


@pytest.mark.parametrize("hour,quiet", [(22, True), (2, True), (6, True), (7, False), (12, False), (20, False), (21, True)])
def test_quiet_hours_wrap_around_midnight(hour, quiet):
    when = datetime(2026, 8, 23, hour, 0)
    assert gates.in_quiet_hours(when, "21:00-07:00") is quiet


@pytest.mark.parametrize("hour,quiet", [(13, True), (11, False), (15, False)])
def test_a_window_inside_one_day_does_not_wrap(hour, quiet):
    when = datetime(2026, 8, 23, hour, 0)
    assert gates.in_quiet_hours(when, "12:00-14:00") is quiet


def test_a_malformed_window_is_never_quiet():
    """A typo in configuration must not silence Eve permanently and must not
    raise into the pipeline."""
    assert gates.in_quiet_hours(datetime(2026, 8, 23, 3, 0), "nonsense") is False


def test_local_now_converts_into_the_members_zone():
    utc_evening = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    assert gates.local_now("America/Vancouver", utc_evening).hour == 20


def test_the_cap_window_starts_at_the_members_own_midnight():
    """Two members in two zones have two different days; one cap counted in
    UTC would cut off mid-evening for the western one."""
    now = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    vancouver = gates.day_start_utc("America/Vancouver", now)
    toronto = gates.day_start_utc("America/Toronto", now)
    assert vancouver == datetime(2026, 8, 23, 7, 0, tzinfo=UTC)
    assert toronto == datetime(2026, 8, 24, 4, 0, tzinfo=UTC)


def test_an_unknown_timezone_falls_back_to_utc_rather_than_raising():
    now = datetime(2026, 8, 24, 3, 0, tzinfo=UTC)
    assert gates.day_start_utc("Mars/Olympus", now) == datetime(2026, 8, 24, tzinfo=UTC)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ambient_gates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_ambient.gates'`.

- [ ] **Step 3: Implement**

Create `src/eve_ambient/gates.py`:

```python
"""The gates between a signal and an interruption. Pure functions only: no
I/O, no clock reads, no database. The pipeline supplies `now` and the counts.

Every gate fails closed. An unmapped source notifies nobody; an unknown
subject is dropped; an unparseable quiet-hours window is treated as not
quiet, because the failure that silences Eve forever is worse than the one
that lets a notification through at a bad hour.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eve.family import UnknownMemberError, get_family
from eve.specialists.permissions import permission_denial
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

# The strings the roster actually grants. `home.control` rather than a
# read-only equivalent because no read-only home grant exists: whoever may
# operate the house is who gets told about it.
SOURCE_PERMISSION: dict[str, str] = {
    "calendar": "calendar.read",
    "mail": "mail.read",
    "finances": "finances",
    "home": "home.control",
}

# Sources whose content belongs to one member and may not be redistributed,
# whatever the filter decides (design section 5).
_OWNER_ONLY = {"mail"}


def scoped_audience(signal: Signal, audience: list[str]) -> list[str]:
    """An owner-only signal notifies its owner and nobody else — including
    when the filter named somebody else instead. The filter decides *whether*
    for these sources, never *who*."""
    if signal.source in _OWNER_ONLY and signal.member_sub:
        return [signal.member_sub]
    return list(audience)


def permitted(signal: Signal, subs: list[str]) -> list[str]:
    required = SOURCE_PERMISSION.get(signal.source)
    if required is None:
        logger.warning("no permission mapping for source %r; notifying nobody", signal.source)
        return []
    family = get_family()
    kept = []
    for sub in subs:
        try:
            member = family.get(sub)
        except UnknownMemberError:
            logger.warning("filter named an unknown subject %r", sub)
            continue
        if permission_denial(sorted(member.permissions), required) is None:
            kept.append(sub)
        else:
            logger.info("dropping %s: lacks %s for a %s signal", sub, required, signal.source)
    return kept


def parse_window(window: str) -> tuple[time, time]:
    start_text, end_text = window.split("-", 1)
    return time.fromisoformat(start_text.strip()), time.fromisoformat(end_text.strip())


def in_quiet_hours(when_local: datetime, window: str) -> bool:
    try:
        start, end = parse_window(window)
    except (AttributeError, ValueError):
        logger.warning("unparseable quiet-hours window %r; treating as never quiet", window)
        return False
    now = when_local.time()
    if start <= end:
        return start <= now < end
    # Wraps midnight: quiet from `start` to the end of the day, and from the
    # start of the day to `end`.
    return now >= start or now < end


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown timezone %r; falling back to UTC", timezone)
        return ZoneInfo("UTC")


def local_now(timezone: str, now_utc: datetime) -> datetime:
    return now_utc.astimezone(_zone(timezone))


def day_start_utc(timezone: str, now_utc: datetime) -> datetime:
    """Midnight of the member's current local day, expressed in UTC. This is
    the lower bound of the daily-cap count."""
    local = local_now(timezone, now_utc)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(now_utc.tzinfo or _zone("UTC"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_gates.py -v`
Expected: PASS, 20 tests (the parametrized cases count individually).

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/gates.py tests/test_ambient_gates.py
git commit -m "feat: the ambient gate chain - audience scope, permission, cap window, quiet hours"
```

---

### Task 9: The impersonation auth path

**Files:**
- Modify: `src/eve/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Settings.ambient_token` (Task 1).
- Produces: an accepted credential — bearer `EVE_AMBIENT_TOKEN` plus an
  `x-eve-on-behalf-of: <sub>` header — resolving to that member's principal.

**Design note the spec understates:** this is *not* a third value of
`EVE_AUTH_MODE`. Production runs `auth_mode=oidc`, and the ambient token has to
work there, so it is an additional accepted credential checked before the mode's
own path — not an alternative to it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth.py`:

```python
AMBIENT_TOKEN = "a" * 40


def _ambient_settings(monkeypatch):
    monkeypatch.setenv("EVE_AUTH_MODE", "dev")
    monkeypatch.setenv("EVE_DEV_TOKENS", '{"tok-noah": "sub-noah"}')
    monkeypatch.setenv("EVE_AMBIENT_TOKEN", AMBIENT_TOKEN)
    from eve.settings import get_settings

    get_settings.cache_clear()


async def test_the_ambient_token_authenticates_as_the_named_member(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    _ambient_settings(monkeypatch)
    user = await authenticate(
        {
            "Authorization": f"Bearer {AMBIENT_TOKEN}",
            "x-eve-on-behalf-of": "sub-noah",
        }
    )
    assert user["identity"] == "sub-noah"
    assert user["is_authenticated"] is True


async def test_a_member_token_cannot_impersonate(monkeypatch):
    """The header is only meaningful alongside the ambient token. If an
    ordinary member could set it, every member could read every thread."""
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH, KID]))
    _ambient_settings(monkeypatch)
    user = await authenticate(
        {"Authorization": "Bearer tok-noah", "x-eve-on-behalf-of": "sub-kid"}
    )
    assert user["identity"] == "sub-noah"


async def test_the_ambient_token_without_the_header_is_refused(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    _ambient_settings(monkeypatch)
    with pytest.raises(AuthError, match="on-behalf-of"):
        await authenticate({"Authorization": f"Bearer {AMBIENT_TOKEN}"})


async def test_an_unknown_subject_is_refused(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    _ambient_settings(monkeypatch)
    with pytest.raises(AuthError, match="sub-stranger"):
        await authenticate(
            {
                "Authorization": f"Bearer {AMBIENT_TOKEN}",
                "x-eve-on-behalf-of": "sub-stranger",
            }
        )


async def test_the_ambient_path_is_inert_when_no_token_is_configured(monkeypatch):
    """An empty configured token must never match an empty presented one."""
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    monkeypatch.setenv("EVE_AUTH_MODE", "dev")
    monkeypatch.setenv("EVE_DEV_TOKENS", '{"tok-noah": "sub-noah"}')
    monkeypatch.delenv("EVE_AMBIENT_TOKEN", raising=False)
    from eve.settings import get_settings

    get_settings.cache_clear()
    with pytest.raises(AuthError):
        await authenticate({"Authorization": "Bearer ", "x-eve-on-behalf-of": "sub-noah"})


async def test_bytes_headers_are_handled_on_the_ambient_path(monkeypatch):
    """Aegra hands headers through as bytes in some paths; extract_bearer
    already copes and the new header must too."""
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    _ambient_settings(monkeypatch)
    user = await authenticate(
        {
            b"authorization": f"Bearer {AMBIENT_TOKEN}".encode(),
            b"x-eve-on-behalf-of": b"sub-noah",
        }
    )
    assert user["identity"] == "sub-noah"
```

`tests/test_auth.py` defines `NOAH` but no `KID`. Add one beside it, matching
the existing construction:

```python
KID = Member(
    sub="sub-kid",
    name="Kid",
    role="child",
    timezone="America/Toronto",
    permissions=frozenset({"home.control"}),
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_auth.py -k ambient -v`
Expected: FAIL — the ambient token is treated as an unrecognised dev token.

- [ ] **Step 3: Implement**

In `src/eve/auth.py`, add the import and the two helpers, then rewrite
`authenticate`:

```python
import logging
from hmac import compare_digest

logger = logging.getLogger(__name__)

_ON_BEHALF_OF = "x-eve-on-behalf-of"


def _header(headers: dict, name: str) -> str | None:
    """Same bytes-tolerance as extract_bearer, for one named header."""
    for key, value in headers.items():
        candidate = key.decode() if isinstance(key, bytes) else key
        if candidate.lower() != name:
            continue
        return value.decode() if isinstance(value, bytes) else value
    return None


def _ambient_subject(headers: dict, token: str) -> str | None:
    """The impersonation path (design section 6.1). Returns None — falling
    through to the configured auth mode — unless the presented bearer is
    exactly the configured ambient token.

    Deliberately not a third EVE_AUTH_MODE: production runs `oidc` and this
    credential has to work there too, so it is an additional accepted
    credential rather than an alternative mode. `compare_digest` because a
    timing-distinguishable comparison of an impersonation secret is worth
    avoiding for the cost of one import.
    """
    configured = get_settings().ambient_token
    if not configured or not compare_digest(token, configured):
        return None
    subject = _header(headers, _ON_BEHALF_OF)
    if not subject:
        raise AuthError(f"the ambient token requires an {_ON_BEHALF_OF} header")
    logger.info("ambient impersonation authenticated as %s", subject)
    return subject


@auth.authenticate
async def authenticate(headers: dict) -> dict:
    token = extract_bearer(headers)
    subject = _ambient_subject(headers, token) or _subject_from_token(token)
    try:
        member = get_family().get(subject)
    except UnknownMemberError as exc:
        raise AuthError(str(exc)) from exc
    return {
        "identity": member.sub,
        "display_name": member.name,
        "role": member.role,
        "permissions": sorted(member.permissions),
        "is_authenticated": True,
    }
```

Also extend the module docstring: two modes plus one additional credential.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_auth.py -v`
Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Commit**

```bash
git add src/eve/auth.py tests/test_auth.py
git commit -m "feat: an ambient service token that authenticates as a named member"
```

---

### Task 10: The notifier

**Files:**
- Create: `src/eve_ambient/ntfy.py`
- Test: `tests/test_ambient_ntfy.py`

**Interfaces:**
- Consumes: `Settings.ambient_ntfy_*`.
- Produces: `Notifier` protocol with
  `async def send(self, *, title: str, body: str, urgent: bool, click_url: str | None) -> bool`,
  and `NtfyNotifier` implementing it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_ntfy.py`:

```python
import httpx
import pytest
import respx

from eve_ambient.ntfy import NtfyNotifier


@pytest.fixture(autouse=True)
def ntfy_settings(monkeypatch):
    monkeypatch.setenv("EVE_AMBIENT_NTFY_BASE_URL", "https://ntfy.test")
    monkeypatch.setenv("EVE_AMBIENT_NTFY_TOPIC", "eve-family")
    monkeypatch.setenv("EVE_AMBIENT_NTFY_TOKEN", "tk_secret")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@respx.mock
async def test_the_message_is_posted_to_the_topic():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    sent = await NtfyNotifier().send(
        title="Eve", body="Your dentist appointment is at 3.", urgent=False, click_url=None
    )
    assert sent is True
    assert route.calls.last.request.content.decode() == "Your dentist appointment is at 3."


@respx.mock
async def test_an_urgent_message_raises_the_priority_and_the_tag():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    await NtfyNotifier().send(title="Eve", body="Water detected.", urgent=True, click_url=None)
    headers = route.calls.last.request.headers
    assert headers["priority"] == "urgent"
    assert "rotating_light" in headers["tags"]


@respx.mock
async def test_a_normal_message_uses_default_priority():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    await NtfyNotifier().send(title="Eve", body="Trash day.", urgent=False, click_url=None)
    assert route.calls.last.request.headers["priority"] == "default"


@respx.mock
async def test_the_click_url_is_forwarded():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    await NtfyNotifier().send(
        title="Eve", body="hi", urgent=False, click_url="https://eve.test/t/abc"
    )
    assert route.calls.last.request.headers["click"] == "https://eve.test/t/abc"


@respx.mock
async def test_the_token_is_sent_as_a_bearer():
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    await NtfyNotifier().send(title="Eve", body="hi", urgent=False, click_url=None)
    assert route.calls.last.request.headers["authorization"] == "Bearer tk_secret"


@respx.mock
async def test_a_failing_push_returns_false_rather_than_raising():
    """ntfy being down must lose the push, not the turn that produced it."""
    respx.post("https://ntfy.test/eve-family").mock(
        side_effect=httpx.ConnectError("refused")
    )
    assert await NtfyNotifier().send(title="Eve", body="hi", urgent=False, click_url=None) is False


@respx.mock
async def test_an_http_error_status_returns_false():
    respx.post("https://ntfy.test/eve-family").mock(return_value=httpx.Response(502))
    assert await NtfyNotifier().send(title="Eve", body="hi", urgent=False, click_url=None) is False


async def test_an_unconfigured_notifier_reports_failure_without_a_request(monkeypatch):
    monkeypatch.delenv("EVE_AMBIENT_NTFY_BASE_URL", raising=False)
    from eve.settings import get_settings

    get_settings.cache_clear()
    assert await NtfyNotifier().send(title="Eve", body="hi", urgent=False, click_url=None) is False


@respx.mock
async def test_a_non_ascii_title_does_not_break_the_request():
    """ntfy carries metadata in headers, which are latin-1 on the wire. Eve's
    body may be any text; the title must not be able to fail the push."""
    route = respx.post("https://ntfy.test/eve-family").mock(
        return_value=httpx.Response(200)
    )
    assert await NtfyNotifier().send(
        title="Eve — urgent", body="Wasserschaden 💧", urgent=True, click_url=None
    ) is True
    assert route.calls.last.request.content.decode() == "Wasserschaden 💧"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ambient_ntfy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_ambient.ntfy'`.

- [ ] **Step 3: Implement**

Create `src/eve_ambient/ntfy.py`:

```python
"""Delivery. One protocol, one implementation — swappable as the design asks,
without a factory for a single product.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from eve.settings import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


class Notifier(Protocol):
    async def send(
        self, *, title: str, body: str, urgent: bool, click_url: str | None
    ) -> bool:
        """True when the push was accepted. Never raises: a delivery failure
        must not lose the thread the message is already in."""
        ...


def _ascii(value: str) -> str:
    """ntfy carries title and tags in HTTP headers, which are latin-1 on the
    wire. Eve's own text goes in the body, where UTF-8 is fine; anything
    header-bound is flattened so an em dash can never fail a push."""
    return value.encode("ascii", "replace").decode("ascii")


class NtfyNotifier:
    async def send(
        self, *, title: str, body: str, urgent: bool, click_url: str | None
    ) -> bool:
        settings = get_settings()
        if not settings.ambient_ntfy_base_url or not settings.ambient_ntfy_topic:
            logger.warning("ntfy is not configured; dropping a notification")
            return False

        headers = {
            "Title": _ascii(title),
            "Priority": "urgent" if urgent else "default",
            "Tags": "rotating_light" if urgent else "speech_balloon",
        }
        if settings.ambient_ntfy_token:
            headers["Authorization"] = f"Bearer {settings.ambient_ntfy_token}"
        if click_url:
            headers["Click"] = click_url

        url = f"{settings.ambient_ntfy_base_url.rstrip('/')}/{settings.ambient_ntfy_topic}"
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.post(
                    url, content=body.encode("utf-8"), headers=headers
                )
                response.raise_for_status()
        except Exception:
            logger.warning("ntfy push failed", exc_info=True)
            return False
        return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_ntfy.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/ntfy.py tests/test_ambient_ntfy.py
git commit -m "feat: ntfy delivery behind a Notifier protocol"
```

---

### Task 11: The compose turn and delivery

**Files:**
- Create: `src/eve_ambient/notify.py`
- Test: `tests/test_ambient_notify.py`

**Interfaces:**
- Consumes: `langgraph_sdk.get_client`, `Signal`, `FilterVerdict`,
  `eve.family.Member`, `Notifier`, `Settings.ambient_aegra_base_url`,
  `Settings.ambient_token`, `Settings.ambient_thread_url_template`.
- Produces:
  - `class DeliveryError(Exception)` — infrastructure failed; the caller must
    NOT mark the signal seen.
  - `async def deliver(signal, member, verdict, notifier) -> str | None` —
    thread id when a notification was sent, `None` when Eve declined.
  - `VETO = "NOTHING"`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_notify.py`:

```python
from datetime import UTC, datetime

import pytest

from eve.family import Member
from eve_ambient import notify
from eve_ambient.types import FilterVerdict, Signal

MEMBER = Member(
    sub="sub-noah", name="Noah", role="adult", timezone="America/Vancouver",
    permissions=frozenset({"calendar.read"}),
)
SIGNAL = Signal(
    source="calendar", key="uid-1:start:x",
    occurred_at=datetime(2026, 8, 23, 22, 0, tzinfo=UTC),
    member_sub="sub-noah", summary="Upcoming: Dentist at 3pm.", payload={"uid": "uid-1"},
)
VERDICT = FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="soon")


class FakeThreads:
    def __init__(self):
        self.created, self.deleted = [], []

    async def create(self, metadata=None, **kwargs):
        self.created.append(metadata or {})
        return {"thread_id": "thread-1"}

    async def delete(self, thread_id):
        self.deleted.append(thread_id)


class FakeRuns:
    def __init__(self, final_text="Dentist at 3 — leave by 2:30.", tool_names=(), error=None):
        self.final_text, self.tool_names, self.error = final_text, tool_names, error
        self.inputs = []

    async def wait(self, thread_id, assistant, input=None, **kwargs):
        self.inputs.append(input)
        if self.error:
            raise self.error
        messages = [{"type": "human", "content": "prompt"}]
        if self.tool_names:
            messages.append(
                {
                    "type": "ai",
                    "content": "",
                    "tool_calls": [{"name": name, "args": {}} for name in self.tool_names],
                }
            )
        messages.append({"type": "ai", "content": self.final_text})
        return {"messages": messages}


class FakeClient:
    def __init__(self, runs=None):
        self.threads, self.runs = FakeThreads(), runs or FakeRuns()


class RecordingNotifier:
    def __init__(self, result=True):
        self.result, self.calls = result, []

    async def send(self, *, title, body, urgent, click_url):
        self.calls.append({"title": title, "body": body, "urgent": urgent, "click_url": click_url})
        return self.result


@pytest.fixture(autouse=True)
def ambient_settings(monkeypatch):
    monkeypatch.setenv("EVE_AMBIENT_TOKEN", "a" * 40)
    monkeypatch.setenv("EVE_AMBIENT_AEGRA_BASE_URL", "http://eve.test:2026")
    monkeypatch.setenv("EVE_AMBIENT_THREAD_URL_TEMPLATE", "https://eve.test/t/{thread_id}")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _with_client(monkeypatch, client):
    monkeypatch.setattr(notify, "get_client", lambda **kwargs: client)
    return client


async def test_a_notification_creates_a_thread_and_pushes(monkeypatch):
    client = _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    thread_id = await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert thread_id == "thread-1"
    assert notifier.calls[0]["body"] == "Dentist at 3 — leave by 2:30."


async def test_the_client_impersonates_the_member(monkeypatch):
    """Aegra scopes threads to the authenticated identity, so this header is
    the whole reason the member can reply in the thread."""
    captured = {}

    def _get_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(notify, "get_client", _get_client)
    await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert captured["url"] == "http://eve.test:2026"
    assert captured["headers"]["x-eve-on-behalf-of"] == "sub-noah"
    assert captured["headers"]["Authorization"] == f"Bearer {'a' * 40}"


async def test_the_input_is_one_marked_human_message(monkeypatch):
    """recall.py and extract.py both key off the last HumanMessage, so the
    ambient prompt has to be one (design section 6.2)."""
    client = _with_client(monkeypatch, FakeClient())
    await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    messages = client.runs.inputs[0]["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert "[ambient signal" in messages[0]["content"]
    assert SIGNAL.summary in messages[0]["content"]
    assert notify.VETO in messages[0]["content"]


async def test_the_thread_is_tagged_as_ambient(monkeypatch):
    client = _with_client(monkeypatch, FakeClient())
    await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert client.threads.created[0]["ambient"] is True
    assert client.threads.created[0]["source"] == "calendar"


async def test_a_veto_deletes_the_thread_and_sends_nothing(monkeypatch):
    client = _with_client(monkeypatch, FakeClient(runs=FakeRuns(final_text="NOTHING")))
    notifier = RecordingNotifier()
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier) is None
    assert client.threads.deleted == ["thread-1"]
    assert notifier.calls == []


async def test_an_empty_answer_is_treated_as_a_veto(monkeypatch):
    client = _with_client(monkeypatch, FakeClient(runs=FakeRuns(final_text="   ")))
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier()) is None
    assert client.threads.deleted == ["thread-1"]


async def test_a_failed_run_raises_delivery_error_and_cleans_up(monkeypatch):
    """Aegra being unreachable must leave the signal unseen so the next poll
    retries it (design 6.4), which is what DeliveryError signals."""
    client = _with_client(
        monkeypatch, FakeClient(runs=FakeRuns(error=RuntimeError("aegra down")))
    )
    with pytest.raises(notify.DeliveryError):
        await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    assert client.threads.deleted == ["thread-1"]


async def test_a_failed_push_still_returns_the_thread(monkeypatch):
    """The content is already in the thread. Retrying would re-run a paid
    turn to re-send text the member can already read."""
    client = _with_client(monkeypatch, FakeClient())
    assert await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier(result=False)) == "thread-1"


async def test_an_urgent_verdict_is_passed_to_the_notifier(monkeypatch):
    _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    urgent = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="leak")
    await notify.deliver(SIGNAL, MEMBER, urgent, notifier)
    assert notifier.calls[0]["urgent"] is True
    assert "urgent" in notifier.calls[0]["title"].lower()


async def test_the_click_url_comes_from_the_template(monkeypatch):
    _with_client(monkeypatch, FakeClient())
    notifier = RecordingNotifier()
    await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert notifier.calls[0]["click_url"] == "https://eve.test/t/thread-1"


async def test_tool_calls_made_during_the_run_are_logged(monkeypatch, caplog):
    """Ambient turns may act (design section 7). Initiative without an audit
    trail is how "why did the garage close" becomes unanswerable."""
    _with_client(
        monkeypatch, FakeClient(runs=FakeRuns(tool_names=("ask_home", "search_memory")))
    )
    with caplog.at_level("INFO"):
        await notify.deliver(SIGNAL, MEMBER, VERDICT, RecordingNotifier())
    logged = " ".join(record.message for record in caplog.records)
    assert "ask_home" in logged
    assert "search_memory" in logged
    assert SIGNAL.key in logged


async def test_block_style_content_is_flattened(monkeypatch):
    """The Responses API returns content as a list of blocks, not a string."""
    runs = FakeRuns()
    runs.final_text = [{"type": "text", "text": "Leave by 2:30."}]
    _with_client(monkeypatch, FakeClient(runs=runs))
    notifier = RecordingNotifier()
    await notify.deliver(SIGNAL, MEMBER, VERDICT, notifier)
    assert notifier.calls[0]["body"] == "Leave by 2:30."
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ambient_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_ambient.notify'`.

- [ ] **Step 3: Implement**

Create `src/eve_ambient/notify.py`:

```python
"""Create the thread, run Eve on it, and push the result.

This module knows Aegra and ntfy. It does not know why it was called: the
gates ran before it, and the decision to spend a VOICE-tier turn has already
been made.
"""

from __future__ import annotations

import json
import logging

from langgraph_sdk import get_client

from eve.family import Member
from eve.settings import get_settings
from eve_ambient.ntfy import Notifier
from eve_ambient.types import FilterVerdict, Signal

logger = logging.getLogger(__name__)

VETO = "NOTHING"
_PAYLOAD_CHARS = 800
_ASSISTANT = "eve"


class DeliveryError(Exception):
    """Infrastructure failed rather than Eve declining. The caller must leave
    the signal unseen so the next poll retries it."""


def _client(member_sub: str):
    settings = get_settings()
    return get_client(
        url=settings.ambient_aegra_base_url,
        headers={
            "Authorization": f"Bearer {settings.ambient_token}",
            "x-eve-on-behalf-of": member_sub,
        },
    )


def compose_prompt(signal: Signal, member: Member, verdict: FilterVerdict) -> str:
    """A marked human message, not a developer one: recall.py:40 and
    extract.py:129 both key off the last HumanMessage, so a developer message
    would silently cost this turn its episodic recall and half its extraction
    (design 6.2). The marker also tells Eve she was not spoken to, and leaves
    the thread showing what prompted her.
    """
    return (
        f"[ambient signal — not spoken by {member.name}]\n"
        f"{signal.summary}\n"
        f"Source: {signal.source}. Noticed at {signal.occurred_at.isoformat()}.\n"
        f"Detail: {json.dumps(signal.payload, default=str)[:_PAYLOAD_CHARS]}\n"
        f"Why this reached you: {verdict.why}\n\n"
        f"You noticed this; nobody asked you. Decide whether {member.name} needs "
        f"to know right now. If it is worth saying, say it in one or two "
        f"sentences in your own voice, and act only if acting is plainly what "
        f"they would want. If it is not worth saying, reply with exactly "
        f"{VETO} and nothing else."
    )


def _text_of(content) -> str:
    """Content is a string on the Chat Completions path and a list of blocks
    on the Responses path."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _final_text(state: dict) -> str:
    for message in reversed(state.get("messages") or []):
        role = message.get("type") or message.get("role")
        if role in ("ai", "assistant") and not message.get("tool_calls"):
            return _text_of(message.get("content")).strip()
    return ""


def _tools_called(state: dict) -> list[str]:
    names = []
    for message in state.get("messages") or []:
        for call in message.get("tool_calls") or []:
            name = call.get("name")
            if name:
                names.append(name)
    return names


def _click_url(thread_id: str) -> str | None:
    template = get_settings().ambient_thread_url_template
    return template.format(thread_id=thread_id) if template else None


async def deliver(
    signal: Signal, member: Member, verdict: FilterVerdict, notifier: Notifier
) -> str | None:
    client = _client(member.sub)
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
            input={"messages": [{"role": "user", "content": compose_prompt(signal, member, verdict)}]},
        )
    except Exception as exc:
        await _discard(client, thread_id)
        raise DeliveryError(f"the compose turn failed: {exc}") from exc

    tools = _tools_called(state)
    logger.info(
        "ambient turn member=%s source=%s key=%s thread=%s tools=%s",
        member.sub, signal.source, signal.key, thread_id, ",".join(tools) or "none",
    )

    text = _final_text(state)
    if not text or text == VETO:
        logger.info("Eve declined to speak about %s; discarding the thread", signal.key)
        await _discard(client, thread_id)
        return None

    title = "Eve — urgent" if verdict.urgent else "Eve"
    sent = await notifier.send(
        title=title, body=text, urgent=verdict.urgent, click_url=_click_url(thread_id)
    )
    if not sent:
        # Deliberately still a success: the message is in the thread the
        # member owns. Retrying would re-run a paid turn to re-send text
        # they can already read.
        logger.warning("the push failed but %s holds the message", thread_id)
    return thread_id


async def _discard(client, thread_id: str) -> None:
    try:
        await client.threads.delete(thread_id)
    except Exception:
        logger.warning("could not delete the ambient thread %s", thread_id, exc_info=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_notify.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/notify.py tests/test_ambient_notify.py
git commit -m "feat: the ambient compose turn, Eve's veto, and the delivery audit line"
```

---

### Task 12: The pipeline

**Files:**
- Create: `src/eve_ambient/pipeline.py`
- Test: `tests/test_ambient_pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 2, 7, 8, 10, 11.
- Produces:
  - `async def handle_signal(signal: Signal, *, now: datetime | None = None, notifier: Notifier | None = None) -> str`
    returning one of `"stale" | "filtered" | "unpermitted" | "quiet" | "capped" | "vetoed" | "sent" | "deferred"`.

Ordering matters and is the thing this task's tests pin: cooldown before the
filter (do not pay for a repeat), the filter before the gates (do not read the
roster for a non-event), quiet hours and the cap before the compose turn (do
not pay for a message nobody will get), and `mark_seen` last.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ambient_pipeline.py`:

```python
from datetime import UTC, datetime

import pytest

from eve_ambient import pipeline
from eve_ambient.types import FilterVerdict, Signal

ROSTER = """
members:
  - sub: "sub-noah"
    name: "Noah"
    role: adult
    timezone: "America/Vancouver"
    permissions: [mail.read, finances, home.control, calendar.read]
  - sub: "sub-kid"
    name: "Kid"
    role: child
    timezone: "America/Vancouver"
    permissions: [home.control]
"""

MIDDAY = datetime(2026, 8, 23, 19, 0, tzinfo=UTC)   # 12:00 in Vancouver
NIGHT = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)     # 02:00 in Vancouver


def _signal(source="finances", member_sub=None, key="k1"):
    return Signal(
        source=source, key=key, occurred_at=MIDDAY, member_sub=member_sub,
        summary="Budget over: Groceries.", payload={},
    )


@pytest.fixture(autouse=True)
def wiring(tmp_path, monkeypatch):
    """Replace the three I/O seams — store, filter, notify — and keep the
    real gates, because the gates are what this task is about."""
    path = tmp_path / "family.yaml"
    path.write_text(ROSTER)
    monkeypatch.setenv("EVE_FAMILY_FILE", str(path))
    monkeypatch.setenv("EVE_AMBIENT_DAILY_CAP", "2")
    monkeypatch.setenv("EVE_AMBIENT_QUIET_HOURS", "21:00-07:00")
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()

    state = {
        "fresh": True, "seen": [], "notices": [], "counts": {},
        "verdict": FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="w"),
        "delivered": [], "deliver_result": "thread-1", "deliver_error": None,
    }

    async def _is_fresh(source, key, cooldown_hours):
        state["cooldown_seen"] = cooldown_hours
        return state["fresh"]

    async def _mark_seen(source, key):
        state["seen"].append((source, key))

    async def _record_notice(member_sub, source, key, urgent, thread_id):
        state["notices"].append((member_sub, source, key, urgent, thread_id))

    async def _notices_since(member_sub, since):
        return state["counts"].get(member_sub, 0)

    async def _judge(signal):
        return state["verdict"]

    async def _deliver(signal, member, verdict, notifier):
        if state["deliver_error"]:
            raise state["deliver_error"]
        state["delivered"].append(member.sub)
        return state["deliver_result"]

    monkeypatch.setattr(pipeline.store, "is_fresh", _is_fresh)
    monkeypatch.setattr(pipeline.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(pipeline.store, "record_notice", _record_notice)
    monkeypatch.setattr(pipeline.store, "notices_since", _notices_since)
    monkeypatch.setattr(pipeline, "judge", _judge)
    monkeypatch.setattr(pipeline, "deliver", _deliver)
    yield state
    get_settings.cache_clear()
    get_family.cache_clear()


async def test_a_signal_inside_its_cooldown_is_stale_and_costs_no_filter_call(wiring):
    wiring["fresh"] = False
    wiring["verdict"] = None  # judge would raise if called
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "stale"
    assert wiring["delivered"] == []


async def test_the_signals_own_cooldown_overrides_the_default(wiring):
    signal = Signal(
        source="finances", key="b1", occurred_at=MIDDAY, member_sub=None,
        summary="over", payload={}, cooldown_hours=720,
    )
    await pipeline.handle_signal(signal, now=MIDDAY)
    assert wiring["cooldown_seen"] == 720


async def test_the_default_cooldown_is_used_when_the_signal_has_none(wiring):
    await pipeline.handle_signal(_signal(), now=MIDDAY)
    assert wiring["cooldown_seen"] == 6


async def test_a_notify_verdict_delivers_and_records(wiring):
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "sent"
    assert wiring["delivered"] == ["sub-noah"]
    assert wiring["notices"][0][0] == "sub-noah"
    assert wiring["seen"] == [("finances", "k1")]


async def test_a_no_verdict_is_marked_seen_and_never_delivered(wiring):
    wiring["verdict"] = FilterVerdict(notify=False, why="routine")
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "filtered"
    assert wiring["delivered"] == []
    assert wiring["seen"] == [("finances", "k1")]


async def test_a_notify_verdict_with_an_empty_audience_is_filtered(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=[], why="who?")
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "filtered"


async def test_a_member_without_the_permission_is_dropped(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-kid"], why="w")
    assert await pipeline.handle_signal(_signal(source="finances"), now=MIDDAY) == "unpermitted"
    assert wiring["delivered"] == []


async def test_quiet_hours_suppress_a_normal_signal(wiring):
    assert await pipeline.handle_signal(_signal(), now=NIGHT) == "quiet"
    assert wiring["delivered"] == []


async def test_quiet_hours_do_not_suppress_an_urgent_signal(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="leak")
    assert await pipeline.handle_signal(_signal(source="home"), now=NIGHT) == "sent"
    assert wiring["delivered"] == ["sub-noah"]


async def test_the_cap_suppresses_once_it_is_reached(wiring):
    wiring["counts"]["sub-noah"] = 2
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "capped"


async def test_an_urgent_signal_bypasses_the_cap(wiring):
    wiring["counts"]["sub-noah"] = 99
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-noah"], urgent=True, why="fire")
    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "sent"


async def test_a_veto_is_recorded_as_seen_but_not_as_a_notice(wiring):
    wiring["deliver_result"] = None
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "vetoed"
    assert wiring["notices"] == []
    assert wiring["seen"] == [("finances", "k1")]


async def test_a_delivery_failure_leaves_the_signal_unseen(wiring):
    from eve_ambient.notify import DeliveryError

    wiring["deliver_error"] = DeliveryError("aegra down")
    assert await pipeline.handle_signal(_signal(), now=MIDDAY) == "deferred"
    assert wiring["seen"] == []


async def test_a_mail_signal_only_reaches_its_owner(wiring):
    wiring["verdict"] = FilterVerdict(notify=True, audience=["sub-kid"], why="w")
    result = await pipeline.handle_signal(
        _signal(source="mail", member_sub="sub-noah"), now=MIDDAY
    )
    assert result == "sent"
    assert wiring["delivered"] == ["sub-noah"]


async def test_every_signal_leaves_one_resolution_line(wiring, caplog):
    """Design section 9: the trace only starts at the compose turn, so the
    verdict, the reasoning and the gate that stopped it live in this log line
    or nowhere."""
    wiring["verdict"] = FilterVerdict(notify=False, why="routine and expected")
    with caplog.at_level("INFO"):
        await pipeline.handle_signal(_signal(), now=MIDDAY)
    line = next(
        r.getMessage() for r in caplog.records if "ambient resolved" in r.getMessage()
    )
    assert "outcome=filtered" in line
    assert "key=k1" in line
    assert "routine and expected" in line


async def test_two_members_each_get_their_own_notice(wiring):
    wiring["verdict"] = FilterVerdict(
        notify=True, audience=["sub-noah", "sub-kid"], why="w"
    )
    assert await pipeline.handle_signal(_signal(source="home"), now=MIDDAY) == "sent"
    assert sorted(wiring["delivered"]) == ["sub-kid", "sub-noah"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ambient_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_ambient.pipeline'`.

- [ ] **Step 3: Implement**

Create `src/eve_ambient/pipeline.py`:

```python
"""One signal, from arrival to resolution. The only module that knows the
order things happen in.

The order is a cost order as much as a logic order: the cooldown check is one
indexed SELECT, the filter is a cheap model call, the gates are pure, and the
compose turn is the only expensive step. Nothing expensive runs until
everything cheap has agreed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from eve.family import UnknownMemberError, get_family
from eve.settings import get_settings
from eve_ambient import gates, store
from eve_ambient.filter import judge
from eve_ambient.notify import DeliveryError, deliver
from eve_ambient.ntfy import Notifier, NtfyNotifier
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)


async def handle_signal(
    signal: Signal, *, now: datetime | None = None, notifier: Notifier | None = None
) -> str:
    settings = get_settings()
    now = now or datetime.now(UTC)
    notifier = notifier or NtfyNotifier()

    cooldown = (
        signal.cooldown_hours
        if signal.cooldown_hours is not None
        else settings.ambient_cooldown_hours
    )
    if not await store.is_fresh(signal.source, signal.key, cooldown):
        return "stale"

    verdict = await judge(signal)
    audience = gates.permitted(signal, gates.scoped_audience(signal, verdict.audience))
    if not verdict.notify or not verdict.audience:
        await store.mark_seen(signal.source, signal.key)
        return _resolved(signal, verdict, audience, "filtered")
    if not audience:
        await store.mark_seen(signal.source, signal.key)
        return _resolved(signal, verdict, audience, "unpermitted")

    family = get_family()
    outcomes: list[str] = []
    deferred = False

    for sub in audience:
        try:
            member = family.get(sub)
        except UnknownMemberError:
            continue

        if not verdict.urgent:
            local = gates.local_now(member.timezone, now)
            if gates.in_quiet_hours(local, settings.ambient_quiet_hours):
                logger.info("holding %s for %s: quiet hours", signal.key, sub)
                outcomes.append("quiet")
                continue
            sent_today = await store.notices_since(
                sub, gates.day_start_utc(member.timezone, now)
            )
            if sent_today >= settings.ambient_daily_cap:
                logger.info("holding %s for %s: daily cap", signal.key, sub)
                outcomes.append("capped")
                continue
        else:
            logger.warning(
                "URGENT bypass of cap and quiet hours: source=%s key=%s member=%s why=%s",
                signal.source, signal.key, sub, verdict.why,
            )

        try:
            thread_id = await deliver(signal, member, verdict, notifier)
        except DeliveryError:
            logger.warning("deferring %s for %s", signal.key, sub, exc_info=True)
            deferred = True
            continue
        if thread_id is None:
            outcomes.append("vetoed")
            continue
        await store.record_notice(
            sub, signal.source, signal.key, verdict.urgent, thread_id
        )
        outcomes.append("sent")

    if deferred:
        # Left unseen deliberately: the next poll retries it (design 6.4).
        return _resolved(signal, verdict, audience, "deferred")

    await store.mark_seen(signal.source, signal.key)
    for candidate in ("sent", "vetoed", "capped", "quiet"):
        if candidate in outcomes:
            return _resolved(signal, verdict, audience, candidate)
    return _resolved(signal, verdict, audience, "filtered")


def _resolved(signal, verdict, audience, outcome: str) -> str:
    """One line per signal, whatever happened to it (design section 9). The
    Langfuse trace only starts at the compose turn, so everything before it —
    the verdict, the reasoning, who survived the permission gate, and which
    gate stopped it — exists here or nowhere. It is the difference between
    "Eve is too noisy" being diagnosable and being an argument.
    """
    logger.info(
        "ambient resolved source=%s key=%s outcome=%s notify=%s urgent=%s "
        "audience=%s why=%s",
        signal.source, signal.key, outcome, verdict.notify, verdict.urgent,
        ",".join(audience) or "none", verdict.why,
    )
    return outcome
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_pipeline.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve_ambient/pipeline.py tests/test_ambient_pipeline.py
git commit -m "feat: the ambient pipeline, cheapest gate first"
```

---

### Task 13: The service — webhook, poll loop, priming

**Files:**
- Create: `src/eve_ambient/app.py`, `src/eve_ambient/sources/home.py`
- Modify: `src/eve_ambient/store.py` (add `has_any`)
- Test: `tests/test_ambient_app.py`, and add one integration test to `tests/test_ambient_store.py`

**Interfaces:**
- Consumes: `pipeline.handle_signal`, `SOURCES`, `store.has_any`, `Settings`.
- Produces:
  - `app` — the FastAPI application (`eve_ambient.app:app` for uvicorn).
  - `sources.home.from_webhook(payload: dict) -> Signal`
  - `async def poll_once(now: datetime | None = None) -> dict[str, int]` — counts by outcome.
  - `async def store.has_any(source: str) -> bool`

**Priming, and why it is here rather than in a source:** on the first poll of a
source, every open item looks new — a month of upcoming calendar entries, fifty
recent transactions, a day of unread mail. Notifying on all of it would be the
worst possible first impression, and it would recur on any database restore. So
the first poll of a source marks everything seen and notifies nothing, and says
so in the log. It belongs here because it is a property of *polling*, not of any
one API.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ambient_app.py`:

```python
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from eve_ambient import app as app_module
from eve_ambient.types import Signal


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    monkeypatch.setenv("EVE_AMBIENT_HA_WEBHOOK_SECRET", "ha-secret")
    monkeypatch.setenv("EVE_AMBIENT_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    return TestClient(app_module.app)


def test_healthz_reports_whether_ambient_is_enabled(client):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["ambient_enabled"] is False


def test_the_webhook_rejects_a_wrong_secret(client):
    response = client.post(
        "/signals/home-assistant",
        headers={"x-eve-ambient-secret": "wrong"},
        json={"entity_id": "binary_sensor.garage", "state": "open"},
    )
    assert response.status_code == 401


def test_the_webhook_rejects_a_missing_secret(client):
    response = client.post(
        "/signals/home-assistant",
        json={"entity_id": "binary_sensor.garage", "state": "open"},
    )
    assert response.status_code == 401


def test_the_webhook_accepts_and_queues(monkeypatch, client):
    """202 rather than waiting: a compose turn takes far longer than Home
    Assistant will hold a webhook open."""
    handled = []

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    monkeypatch.setattr(app_module, "handle_signal", _handle)
    response = client.post(
        "/signals/home-assistant",
        headers={"x-eve-ambient-secret": "ha-secret"},
        json={
            "entity_id": "binary_sensor.garage",
            "state": "open",
            "friendly_name": "Garage door",
        },
    )
    assert response.status_code == 202
    assert handled[0].source == "home"
    assert handled[0].key == "binary_sensor.garage:open"
    assert "Garage door" in handled[0].summary


def test_the_webhook_rejects_a_payload_without_an_entity(client):
    response = client.post(
        "/signals/home-assistant",
        headers={"x-eve-ambient-secret": "ha-secret"},
        json={"state": "open"},
    )
    assert response.status_code == 422


def test_a_home_signal_is_household_scoped():
    signal = app_module.from_webhook(
        {"entity_id": "lock.front", "state": "unlocked", "friendly_name": "Front door"}
    )
    assert isinstance(signal, Signal)
    assert signal.member_sub is None
    assert signal.source == "home"


async def test_the_first_poll_of_a_source_primes_without_notifying(monkeypatch):
    """A month of calendar entries must not become a month of notifications."""
    seen, handled = [], []

    async def _has_any(source):
        return False

    async def _mark_seen(source, key):
        seen.append(key)

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module.store, "mark_seen", _mark_seen)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (_fake_source(),))

    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert handled == []
    assert seen == ["k1", "k2"]
    assert counts["primed"] == 2


async def test_a_later_poll_runs_the_pipeline(monkeypatch):
    handled = []

    async def _has_any(source):
        return True

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "filtered"

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (_fake_source(),))

    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert [s.key for s in handled] == ["k1", "k2"]
    assert counts["filtered"] == 2


async def test_one_failing_source_does_not_stop_the_others(monkeypatch):
    handled = []

    async def _has_any(source):
        return True

    async def _handle(signal, **kwargs):
        handled.append(signal)
        return "sent"

    async def _broken_poll(member_sub):
        raise RuntimeError("monarch exploded")

    from eve_ambient.sources import Source

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(
        app_module,
        "SOURCES",
        (Source("broken", False, "finances", _broken_poll), _fake_source()),
    )
    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert [s.key for s in handled] == ["k1", "k2"]
    assert counts["errors"] == 1


async def test_a_failing_signal_does_not_stop_its_siblings(monkeypatch):
    async def _has_any(source):
        return True

    calls = {"n": 0}

    async def _handle(signal, **kwargs):
        calls["n"] += 1
        if signal.key == "k1":
            raise RuntimeError("something in the pipeline")
        return "sent"

    monkeypatch.setattr(app_module.store, "has_any", _has_any)
    monkeypatch.setattr(app_module, "handle_signal", _handle)
    monkeypatch.setattr(app_module, "SOURCES", (_fake_source(),))
    counts = await app_module.poll_once(now=datetime(2026, 8, 23, tzinfo=UTC))
    assert calls["n"] == 2
    assert counts["errors"] == 1


def _fake_source():
    from eve_ambient.sources import Source

    async def _poll(member_sub):
        return [
            Signal(
                source="fake", key=key, occurred_at=datetime(2026, 8, 23, tzinfo=UTC),
                member_sub=None, summary=f"summary {key}", payload={},
            )
            for key in ("k1", "k2")
        ]

    return Source("fake", False, "finances", _poll)
```

Append to `tests/test_ambient_store.py`:

```python
async def test_has_any_is_false_before_the_first_signal_and_true_after(pool):
    """This is what makes the first poll prime rather than notify."""
    assert await store.has_any("calendar") is False
    await store.mark_seen("calendar", "uid-1:start:x")
    assert await store.has_any("calendar") is True
    assert await store.has_any("mail") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ambient_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'eve_ambient.app'`.

- [ ] **Step 3: Add `has_any` to the store**

In `src/eve_ambient/store.py`:

```python
async def has_any(source: str) -> bool:
    """Whether this source has ever produced a signal. False means the next
    poll is a first poll, which primes rather than notifies (app.py)."""
    row = await _fetchone(
        "SELECT 1 AS found FROM eve_ambient_seen WHERE source = %(source)s LIMIT 1",
        {"source": source},
    )
    return row is not None
```

- [ ] **Step 4: Implement the home source and the app**

Create `src/eve_ambient/sources/home.py`:

```python
"""Home Assistant state changes as signals. Pushed, never polled: which
entities are worth Eve's attention is a Home Assistant question, answered in
Home Assistant's own automations (design section 4.4).
"""

from __future__ import annotations

from datetime import UTC, datetime

from eve_ambient.types import Signal


def from_webhook(payload: dict) -> Signal:
    entity_id = str(payload["entity_id"])
    state = str(payload.get("state", "unknown"))
    name = payload.get("friendly_name") or entity_id
    try:
        occurred_at = datetime.fromisoformat(str(payload["occurred_at"]))
    except (KeyError, TypeError, ValueError):
        occurred_at = datetime.now(UTC)
    return Signal(
        source="home",
        # The state is in the key so open -> closed -> open is a new signal,
        # while repeated `open` reports inside the cooldown are one.
        key=f"{entity_id}:{state}",
        occurred_at=occurred_at,
        member_sub=None,
        summary=f"{name} is {state}.",
        payload=payload,
    )
```

Create `src/eve_ambient/app.py`:

```python
"""The eve-ambient service: a webhook, a poll loop, and a health endpoint.

One replica only. Nothing here elects a leader, and two instances would
double-count the daily cap.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Header, HTTPException, Request

from eve.family import get_family
from eve.settings import get_settings
from eve_ambient import store
from eve_ambient.pipeline import handle_signal
from eve_ambient.sources import SOURCES
from eve_ambient.sources.home import from_webhook

logger = logging.getLogger(__name__)

_background: set[asyncio.Task] = set()


def _audience_for(source) -> list[str]:
    """Which subs to poll this source for. A per-member source is polled only
    for members holding its permission, so an ungranted member costs no API
    call rather than being filtered after the fact."""
    if not source.per_member:
        return [""]
    return [m.sub for m in get_family().members() if m.can(source.permission)]


async def poll_once(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    counts: Counter[str] = Counter()
    for source in SOURCES:
        try:
            signals = []
            for sub in _audience_for(source):
                signals.extend(await source.poll(sub))
        except Exception:
            logger.warning("source %s failed this tick", source.name, exc_info=True)
            counts["errors"] += 1
            continue

        first_poll = not await store.has_any(source.name)
        if first_poll:
            for signal in signals:
                await store.mark_seen(signal.source, signal.key)
                counts["primed"] += 1
            logger.info(
                "primed %s with %d existing signals; notifying on none of them",
                source.name, counts["primed"],
            )
            continue

        for signal in signals:
            try:
                counts[await handle_signal(signal, now=now)] += 1
            except Exception:
                logger.warning(
                    "signal %s/%s failed", signal.source, signal.key, exc_info=True
                )
                counts["errors"] += 1
    return dict(counts)


async def _poll_forever() -> None:
    interval = get_settings().ambient_poll_interval_seconds
    while True:
        try:
            logger.info("ambient poll: %s", await poll_once())
            await store.prune_seen()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The loop is the last line of defence. It never dies.
            logger.exception("the ambient poll tick failed outright")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    task = None
    if settings.ambient_enabled:
        task = asyncio.create_task(_poll_forever())
        logger.info("ambient polling every %ss", settings.ambient_poll_interval_seconds)
    else:
        logger.info("ambient is disabled; serving health only")
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="eve-ambient", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "ambient_enabled": get_settings().ambient_enabled}


@app.post("/signals/home-assistant", status_code=202)
async def home_assistant_signal(
    request: Request,
    x_eve_ambient_secret: str | None = Header(default=None),
) -> dict:
    secret = get_settings().ambient_ha_webhook_secret
    if not secret or x_eve_ambient_secret != secret:
        raise HTTPException(status_code=401, detail="unauthorized")
    payload = await request.json()
    try:
        signal = from_webhook(payload)
    except (KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"unusable payload: {exc}") from exc

    # 202 and a background task: a compose turn takes far longer than Home
    # Assistant will hold the connection open.
    task = asyncio.create_task(_handle_in_background(signal))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return {"accepted": signal.key}


async def _handle_in_background(signal) -> None:
    try:
        logger.info("webhook signal %s resolved as %s", signal.key, await handle_signal(signal))
    except Exception:
        logger.warning("webhook signal %s failed", signal.key, exc_info=True)
```

Note for the implementer: the webhook test monkeypatches
`app_module.handle_signal`, so `_handle_in_background` must call the
module-global name (as written) rather than holding an imported reference in a
default argument.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ambient_app.py -v && uv run pytest tests/test_ambient_store.py -m integration -v`
Expected: PASS — 10 app tests, 9 store tests.

- [ ] **Step 6: Commit**

```bash
git add src/eve_ambient/app.py src/eve_ambient/sources/home.py src/eve_ambient/store.py tests/test_ambient_app.py tests/test_ambient_store.py
git commit -m "feat: the eve-ambient service - webhook, poll loop, and first-poll priming"
```

---

### Task 14: Integration — impersonation against a live Aegra

**Files:**
- Create: `tests/test_ambient_integration.py`
- Modify: `tests/conftest.py` (add `EVE_AMBIENT_TOKEN` to the `aegra_server` env)

**Interfaces:**
- Consumes: the `aegra_server` fixture, Task 9's auth path, Task 2's migration.
- Produces: nothing importable. This is the test that proves the one genuinely
  novel piece of trust in the phase actually behaves.

- [ ] **Step 1: Write the failing test**

In `tests/conftest.py`, add to the `env` dict inside `aegra_server`:

```python
        # Phase 4: the impersonation credential the ambient integration tests
        # present. Length matters — Settings refuses anything under 32.
        "EVE_AMBIENT_TOKEN": "ambient-integration-token-0123456789abcdef",
```

Create `tests/test_ambient_integration.py`:

```python
"""Ambient's impersonation path against a live `aegra serve`.

Requires `docker compose -f docker-compose.test.yml up -d`. The roster is
tests/fixtures/family.yaml, which the aegra_server fixture points the server
at, so `sub-noah` and `sub-kid` are the two identities here.
"""

from __future__ import annotations

import pytest
from langgraph_sdk import get_client
from langgraph_sdk.errors import APIStatusError

from eve.memory import db
from eve_ambient import store

pytestmark = pytest.mark.integration

AMBIENT_TOKEN = "ambient-integration-token-0123456789abcdef"


def _ambient_client(url, on_behalf_of):
    return get_client(
        url=url,
        headers={
            "Authorization": f"Bearer {AMBIENT_TOKEN}",
            "x-eve-on-behalf-of": on_behalf_of,
        },
    )


def _member_client(url, token):
    return get_client(url=url, headers={"Authorization": f"Bearer {token}"})


async def test_the_ambient_token_creates_a_thread_owned_by_the_member(aegra_server):
    thread = await _ambient_client(aegra_server, "sub-noah").threads.create(
        metadata={"ambient": True, "source": "home", "signal_key": "k1"}
    )
    assert thread["metadata"]["owner"] == "sub-noah"
    assert thread["metadata"]["ambient"] is True


async def test_the_member_can_read_the_thread_ambient_created_for_them(aegra_server):
    """This is the whole point of impersonating rather than pushing only: the
    member opens Eve and the proactive message is there, in a thread they own
    and can reply in."""
    thread = await _ambient_client(aegra_server, "sub-noah").threads.create()
    fetched = await _member_client(aegra_server, "tok-noah").threads.get(
        thread["thread_id"]
    )
    assert fetched["thread_id"] == thread["thread_id"]


async def test_another_member_cannot_read_it(aegra_server):
    thread = await _ambient_client(aegra_server, "sub-noah").threads.create()
    with pytest.raises(APIStatusError) as exc_info:
        await _member_client(aegra_server, "tok-kid").threads.get(thread["thread_id"])
    assert exc_info.value.status_code == 404


async def test_a_wrong_ambient_token_is_rejected(aegra_server):
    client = get_client(
        url=aegra_server,
        headers={
            "Authorization": "Bearer ambient-wrong-token-0123456789abcdefgh",
            "x-eve-on-behalf-of": "sub-noah",
        },
    )
    with pytest.raises(APIStatusError) as exc_info:
        await client.threads.create()
    assert exc_info.value.status_code == 401


async def test_a_member_token_with_the_header_still_authenticates_as_itself(aegra_server):
    """Belt and braces on the unit test in Task 9: at the HTTP boundary, the
    header must be inert without the ambient token."""
    client = get_client(
        url=aegra_server,
        headers={"Authorization": "Bearer tok-kid", "x-eve-on-behalf-of": "sub-noah"},
    )
    thread = await client.threads.create()
    assert thread["metadata"]["owner"] == "sub-kid"


async def test_the_ambient_thread_can_be_deleted_by_ambient(aegra_server):
    """The veto path deletes the thread it just created; it must be allowed
    to."""
    client = _ambient_client(aegra_server, "sub-noah")
    thread = await client.threads.create()
    await client.threads.delete(thread["thread_id"])
    with pytest.raises(APIStatusError):
        await client.threads.get(thread["thread_id"])


async def test_the_ambient_tables_exist_after_migration(monkeypatch):
    from eve.settings import get_settings

    get_settings.cache_clear()
    db._pool = None
    pool = await db.get_pool()
    await db.migrate()
    assert await store.has_any("nothing-here") is False
    async with pool.connection() as conn:
        result = await conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('eve_ambient_seen', 'eve_ambient_notice')"
        )
        assert (await result.fetchone())[0] == 2
    db._pool = None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `docker compose -f docker-compose.test.yml up -d && uv run pytest tests/test_ambient_integration.py -m integration -v`
Expected: FAIL — the ambient token is rejected with 401 until `conftest.py` passes `EVE_AMBIENT_TOKEN` to the server *and* Task 9 is in place. If the server was already running from an earlier task, stop it so the fixture restarts it with the new environment.

- [ ] **Step 3: Make them pass**

No new production code should be needed: Tasks 2 and 9 implement everything
these assert. If a test fails, the bug is in Task 9's auth path or Task 2's
migration, not here — fix it there.

- [ ] **Step 4: Run the whole integration tier**

Run: `uv run pytest -m integration -v`
Expected: PASS. The pre-existing integration tests must be unaffected; the new
environment variable is additive and the auth path falls through to `dev` mode
for every existing token.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_ambient_integration.py
git commit -m "test: ambient impersonation and thread ownership against a live Aegra"
```

---

### Task 15: Live — one signal all the way to a push

**Files:**
- Create: `tests/test_ambient_live.py`

**Interfaces:**
- Consumes: the whole stack. Nothing consumes this.

This is the tier that spends real quota and pushes a real notification, so it
is opt-in twice: the `live` marker plus `EVE_LIVE_TESTS=1`, matching
`tests/test_live_models.py`.

- [ ] **Step 1: Write the test**

Create `tests/test_ambient_live.py`:

```python
"""One fabricated Home Assistant signal, all the way through: a real REFLEX
verdict, a real eve turn on a real thread, and a real ntfy push.

Opt-in twice, because it spends quota and notifies a phone:

    EVE_LIVE_TESTS=1 \
    EVE_AMBIENT_NTFY_BASE_URL=https://ntfy.example \
    EVE_AMBIENT_NTFY_TOPIC=eve-family-test \
    uv run pytest -m live tests/test_ambient_live.py -v

Requires the compose stack up and `EVE_LITELLM_API_KEY` set.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from eve_ambient.pipeline import handle_signal
from eve_ambient.types import Signal

pytestmark = pytest.mark.live

LIVE = os.environ.get("EVE_LIVE_TESTS") == "1"
NTFY = os.environ.get("EVE_AMBIENT_NTFY_TOPIC")


@pytest.mark.skipif(not LIVE, reason="EVE_LIVE_TESTS is not 1")
@pytest.mark.skipif(not NTFY, reason="no ntfy test topic configured")
async def test_a_water_leak_reaches_a_real_notification(aegra_server):
    """Urgent by design: it is the one shape whose verdict is predictable
    enough to assert on, and it exercises the bypass path end to end."""
    signal = Signal(
        source="home",
        key=f"sensor.basement_water:wet:{datetime.now(UTC).isoformat()}",
        occurred_at=datetime.now(UTC),
        member_sub=None,
        summary="The basement water sensor is reporting water on the floor.",
        payload={"entity_id": "sensor.basement_water", "state": "wet"},
    )
    outcome = await handle_signal(signal)
    assert outcome == "sent"


@pytest.mark.skipif(not LIVE, reason="EVE_LIVE_TESTS is not 1")
async def test_a_non_event_is_filtered_by_the_real_reflex_model(aegra_server):
    """The other half of the contract: the filter has to say no to something
    plainly uninteresting, or the daily cap is doing all the work."""
    signal = Signal(
        source="home",
        key=f"sensor.living_room_lux:41:{datetime.now(UTC).isoformat()}",
        occurred_at=datetime.now(UTC),
        member_sub=None,
        summary="The living room light level changed from 40 to 41 lux.",
        payload={"entity_id": "sensor.living_room_lux", "state": "41"},
    )
    assert await handle_signal(signal) in ("filtered", "vetoed")
```

- [ ] **Step 2: Run it**

Run: `EVE_LIVE_TESTS=1 EVE_AMBIENT_NTFY_BASE_URL=... EVE_AMBIENT_NTFY_TOPIC=... uv run pytest -m live tests/test_ambient_live.py -v`
Expected: PASS, and a notification actually arrives on the test topic. If the
filter refuses the water leak, that is a prompt problem in
`prompts/ambient_filter.md`, not a test problem — fix the prompt.

- [ ] **Step 3: Confirm the default tier still ignores it**

Run: `uv run pytest -m "not integration and not live" -q`
Expected: PASS with the live tests deselected.

- [ ] **Step 4: Commit**

```bash
git add tests/test_ambient_live.py
git commit -m "test: one live signal from webhook to real push"
```

---

### Task 16: Packaging — image, workflow, and example environment

**Files:**
- Create: `Dockerfile.eve-ambient`
- Modify: `.github/workflows/build.yml`, `.env.example`
- Test: the build itself, plus the existing unit tier.

**Interfaces:** none. This is the task that makes the previous fifteen
deployable.

**Note on scope:** the workflow currently builds only the root `Dockerfile`,
so `eve-tools`' image has never been published either — a Phase 3 gap. The
matrix below fixes both, because a `eve-ambient` image with no `eve-tools`
image to poll is not deployable.

- [ ] **Step 1: Write the Dockerfile**

Create `Dockerfile.eve-ambient`, mirroring `Dockerfile.eve-tools`:

```dockerfile
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.0 /uv /usr/local/bin/uv

WORKDIR /app

# Same reasoning as Dockerfile.eve-tools: only the dependency tree is
# installed from the shared lockfile, and PYTHONPATH makes the source
# importable without packaging it as the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# eve_ambient imports eve.settings, eve.family, eve.models, eve.memory and
# eve.specialists.permissions, so both packages are copied. It imports
# nothing from eve.graph: ambient is a caller of the graph over HTTP, not a
# second host for it.
COPY src/eve ./src/eve
COPY src/eve_ambient ./src/eve_ambient
COPY prompts ./prompts
COPY family.yaml ./family.yaml

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PORT=8091

EXPOSE 8091

RUN useradd --system --uid 10003 --no-create-home eve-ambient \
    && chown -R eve-ambient:eve-ambient /app
USER 10003

CMD ["uvicorn", "eve_ambient.app:app", "--host", "0.0.0.0", "--port", "8091"]
```

- [ ] **Step 2: Verify the image builds and starts disabled**

```bash
docker build -f Dockerfile.eve-ambient -t eve-ambient:test .
docker run --rm -p 8091:8091 eve-ambient:test &
sleep 5 && curl -s localhost:8091/healthz
```
Expected: `{"status":"ok","ambient_enabled":false}` — with no configuration at
all, the service starts and sends nothing. Stop the container afterwards.

- [ ] **Step 3: Publish all three images from the workflow**

In `.github/workflows/build.yml`, replace the single `image` job's build step
with a matrix:

```yaml
  image:
    needs: test
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    strategy:
      matrix:
        include:
          - image: eve-ai
            dockerfile: Dockerfile
          - image: eve-tools
            dockerfile: Dockerfile.eve-tools
          - image: eve-ambient
            dockerfile: Dockerfile.eve-ambient
    steps:
      - uses: actions/checkout@v4
      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/noahchalifour/${{ matrix.image }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          file: ${{ matrix.dockerfile }}
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
```

- [ ] **Step 4: Document the environment**

Append to `.env.example`:

```bash
# --- Phase 4: Ambient (eve-ambient) ---------------------------------------
# Off by default. Nothing is polled and nothing is pushed until this is true.
EVE_AMBIENT_ENABLED=false
EVE_AMBIENT_POLL_INTERVAL_SECONDS=300
EVE_AMBIENT_DAILY_CAP=6
EVE_AMBIENT_QUIET_HOURS=21:00-07:00
EVE_AMBIENT_COOLDOWN_HOURS=6
EVE_AMBIENT_CALENDAR_LOOKAHEAD_MINUTES=90

# The impersonation credential (design 6.1). At least 32 characters; Settings
# refuses to start otherwise. Needed by BOTH eve (which verifies it) and
# eve-ambient (which presents it), the same way EVE_TOOLS_API_KEY is shared.
# EVE_AMBIENT_TOKEN=

# Shared secret for the Home Assistant automation that POSTs state changes to
# /signals/home-assistant.
# EVE_AMBIENT_HA_WEBHOOK_SECRET=

# Delivery.
# EVE_AMBIENT_NTFY_BASE_URL=https://ntfy.chalifour.dev
# EVE_AMBIENT_NTFY_TOPIC=eve-family
# EVE_AMBIENT_NTFY_TOKEN=
# Click-through target for a notification; {thread_id} is substituted.
# EVE_AMBIENT_THREAD_URL_TEMPLATE=https://eve.chalifour.dev/threads/{thread_id}

# Where eve-ambient reaches Aegra. In-cluster service name by default.
EVE_AMBIENT_AEGRA_BASE_URL=http://eve:2026

# eve-tools only: per-member CalDAV credentials, keyed by member sub, the same
# shape EVE_TOOLS_GMAIL_CREDENTIALS_JSON uses.
# EVE_TOOLS_CALDAV_CREDENTIALS_JSON={"<sub>":{"url":"https://...","username":"...","password":"..."}}
```

- [ ] **Step 5: Run the unit tier**

Run: `uv run pytest -m "not integration and not live" -q`
Expected: PASS. Nothing here changes behaviour, but `.env.example` is read by
developers, and `Settings` reads `.env`, so a typo would surface here.

- [ ] **Step 6: Commit**

```bash
git add Dockerfile.eve-ambient .github/workflows/build.yml .env.example
git commit -m "build: publish eve-ambient (and eve-tools) images, and document ambient config"
```

---

### Task 17: Documentation and ADR 0007

**Files:**
- Create: `docs/adr/0007-ambient-impersonation.md`
- Modify: `docs/architecture.md`, `README.md`

**Interfaces:** none.

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0007-ambient-impersonation.md`, following the shape of the
existing ADRs in that directory (read `0006-eve-tools-isolation.md` first and
match its structure and tone):

```markdown
# ADR 0007 — Ambient runs impersonate family members through one scoped token

**Status:** Accepted (Phase 4)

## Context

A proactive message has to land somewhere the member can reply. Aegra scopes
threads to `user.identity` and does so before any handler in `src/eve/auth.py`
runs, which is why a cross-member read returns 404 rather than 403. So a
thread a member can open and answer in must be created *as that member* —
there is no "create on behalf of" in the Agent Protocol, and no way to hand a
thread over afterwards.

`eve-ambient` runs unattended. It has no member sitting in front of it to
authenticate, and the members it acts for are four people in one household.

## Decision

One shared secret, `EVE_AMBIENT_TOKEN`, plus an `x-eve-on-behalf-of` header.
`src/eve/auth.py` accepts that pair as an additional credential — not a third
`EVE_AUTH_MODE`, because production runs `oidc` and this has to work there —
and resolves the principal to the named roster member, with that member's
permissions.

Guardrails, all in `auth.py` and `settings.py`:

- `compare_digest`, not `==`.
- A token under 32 characters is refused at startup, beside the existing rule
  that refuses `dev` auth in production.
- The subject must exist in `family.yaml`.
- The header is inert on every other auth path: a member's own token carrying
  `x-eve-on-behalf-of` still authenticates as that member. There is a unit
  test and an integration test whose only job is to hold that true.
- Every use logs the impersonated subject.

## Consequences

The credential is issued to exactly two pods: `eve-ambient`, which presents
it, and `eve`, which verifies it. That is a wider blast radius than a
per-member credential would have, and it is stated plainly rather than hidden:
whoever holds this secret can act as any family member.

It also means ambient turns carry the member's own permissions, which is what
bounds Phase 4's "ambient turns may act" decision — ambient adds initiative,
not capability.

## Alternatives considered

- **Per-member Authentik service accounts.** Four OAuth clients, four
  secrets, and a token cache, to buy separation between four people who share
  a house and a budget. Rejected as ceremony.
- **Signed requests (HMAC or asymmetric).** Removes the shared secret at the
  cost of a key distribution mechanism this lab does not have. Worth
  revisiting only if something outside the household ever needs to create
  threads.
- **ntfy-only, no threads.** Cheapest, and it loses the reply-in-place
  behaviour that makes a proactive message a conversation rather than an
  alert.
```

- [ ] **Step 2: Update `docs/architecture.md`**

Make these edits, matching the document's existing voice — it describes what
exists, not what is planned:

1. The opening line: Phase 3 becomes Phase 4, "Ambient", pointing at this
   phase's spec and plan.
2. In the tier table, `REFLEX`'s "First used" stays Phase 2, but its purpose
   line is now literal rather than anticipatory — ambient filtering exists.
3. A new section, **"Ambient"**, after "Memory", covering: the `eve-ambient`
   deployment and that it holds no third-party credential; the four sources
   and that `home` is pushed while the rest are polled; the gate chain in
   order with the defaults; that `urgent` bypasses the cap and quiet hours but
   never the permission gate; the compose turn, the `NOTHING` veto, and why
   the input is a marked human message; the two tables and that
   `eve_ambient_notice` is the cap counter; first-poll priming; and the
   one-replica constraint.
4. In "Auth and thread scoping", a paragraph on the ambient credential,
   pointing at ADR 0007 and stating that the `x-eve-on-behalf-of` header is
   inert without the ambient token.
5. In the module map, add `src/eve_ambient/` with its internal dependency
   order: `types` -> `store`/`sources`/`gates`/`ntfy`; `filter` and `notify`
   depend on `types` plus `eve`'s own modules; `pipeline` depends on all of
   them; `app` depends on `pipeline` and `sources`.
6. Add ADR 0007 to the decision-record list.
7. In "Running the tests", note the two new tiers' requirements: the ambient
   integration tests need the same compose stack, and the live ambient test
   needs an ntfy topic.

- [ ] **Step 3: Update `README.md`**

Two changes:

1. The phase table's bold row moves to Phase 4.
2. The paragraph below it still says "This repository is Phase 2" — stale
   since Phase 3 shipped. Replace it with a Phase 4 description: a family
   assistant that remembers, does things, and now speaks first, pointing at
   this phase's spec.

- [ ] **Step 4: Check the docs against the code**

Run: `uv run pytest -m "not integration and not live" -q`
Then re-read the new "Ambient" section beside `src/eve_ambient/` and confirm
every claim in it is true of the code as built — defaults, table names, gate
order, and the veto behaviour. A wrong architecture doc is worse than none,
because it is believed.

- [ ] **Step 5: Commit**

```bash
git add docs/adr/0007-ambient-impersonation.md docs/architecture.md README.md
git commit -m "docs: ADR 0007, the Ambient architecture section, and a current README"
```

---

## Definition of Done for the phase

Cross-check against spec §12 before calling Phase 4 complete. Each row names
the task that proves it.

| # | Criterion | Proven by |
|---|---|---|
| 1 | A calendar event an hour out produces exactly one notification, in Eve's voice, in a thread the member can reply in. | Tasks 6, 11, 14 + a manual check against the real calendar |
| 2 | A Home Assistant webhook notifies without waiting for a poll. | Task 13, Task 15 |
| 3 | A rejected signal and a vetoed signal produce no push and leave no thread. | Tasks 11, 12 |
| 4 | Quiet hours suppress normal and pass urgent, visibly. | Tasks 8, 12 |
| 5 | The cap holds per member in that member's timezone. | Tasks 8, 12 |
| 6 | A member without `finances` never receives a finances notification. | Tasks 8, 12 |
| 7 | A member's own token cannot impersonate. | Tasks 9, 14 |
| 8 | `eve-tools`, Aegra, or ntfy down loses no signal permanently and never kills the loop. | Tasks 3–6, 10, 11, 12, 13 |
| 9 | With `EVE_AMBIENT_ENABLED=false` the deployment starts, serves `/healthz`, and sends nothing. | Tasks 13, 16 |

Two things are deliberately *not* in this plan, and both are prerequisites
from spec §1 rather than work: the Home Assistant automation that POSTs to the
webhook (P3, authored in Home Assistant) and the `infrastructure` repository's
manifests for the new deployment (spec §10). Neither can be written here.
