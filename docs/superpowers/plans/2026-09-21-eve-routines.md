# Scheduled Routines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A member can ask Eve in conversation to do something on a schedule, and she runs a full headless turn on that cadence, speaking only when there is something worth saying.

**Architecture:** A routine is a stored prompt plus a cadence in one new table. Firing is a new polled source on the existing `eve-ambient` tick, so dedup, the permission gate, thread creation, the headless run, the `NOTHING` veto and the ntfy push are all code that already exists. Eve authors routines through three tools; the client manages existing ones through a resource API mounted beside the widget one.

**Tech Stack:** Python 3.12, LangGraph/LangChain, FastAPI, psycopg 3 + Postgres, Alembic, pytest. Client: Flutter, `go_router`, `provider`.

**Spec:** `docs/superpowers/specs/2026-09-21-eve-routines-design.md`

**Repositories:** Tasks 1 through 13 are in `eve-ai`. Tasks 14 through 19 are in `open-assistant` (`~/GitHub/open-assistant/flutter-open-assistant`). They are separate git repos; commit inside whichever you are changing.

## Global Constraints

- **Python >= 3.12.** Every module starts with `from __future__ import annotations`.
- **Every tool degrades to a returned string, never a raised exception.** `graph.py`'s `_handle_tool_error` is the backstop, but tools catch their own expected failures and return `f"error: {exc.__class__.__name__}"`.
- **Every SQL statement against `eve_routine` carries `member_sub`**, with one documented exception: the household-wide due query the ambient tick runs.
- **Test command:** `uv run pytest -m "not integration and not live and not docker"` is the default run. Integration tests need `docker compose -f docker-compose.test.yml up -d` and `uv run eve-migrate` first.
- **Test naming:** `tests/test_<area>_<thing>.py`, test functions are full sentences (`test_a_paused_routine_is_not_due`).
- **No new dependency.** Everything here uses what `pyproject.toml` already declares.
- **`EVE_ROUTINES_ENABLED` defaults to `false`.** When off, no tools bound and no source registered.
- **Cadence floor is 1 hour.** `every_hours` is an integer in `[1, 168]`.
- **Failure limit default is 5**, via `EVE_ROUTINE_FAILURE_LIMIT`.
- **Client layer direction is one-way: `ui -> domain -> data`.** A ViewModel never imports a service; it goes through a repository. No `BuildContext` in `domain/` or `data/`. See `open-assistant/RULES.md` §2.
- **Client design tokens only:** spacing via `AppSpacing`, radii via `AppRadii`, motion via `AppMotion`. No raw doubles. See `RULES.md` §3.1.

---

## File Structure

**`eve-ai`, created:**

| File | Responsibility |
|---|---|
| `src/eve/routines/__init__.py` | package marker |
| `src/eve/routines/cadence.py` | the closed cadence vocabulary: `validate`, `next_after`, `describe`. Pure, imports nothing from `eve` |
| `src/eve/routines/store.py` | every `eve_routine` SQL statement |
| `src/eve/routines/tools.py` | `schedule_routine`, `list_routines`, `cancel_routine` |
| `src/eve/routines/app.py` | the resource API router |
| `src/eve/http_app.py` | the one app `aegra.json` points at; includes the widget and routine routers |
| `alembic/versions/0011_eve_routine.py` | the table |
| `src/eve_ambient/sources/routines.py` | due routines become signals; records each run's outcome |

**`eve-ai`, modified:**

| File | Change |
|---|---|
| `src/eve/state.py` | add `turn_is_ambient(messages)`, the one shared predicate |
| `src/eve/settings.py` | add the `routines_*` block |
| `src/eve/graph.py` | bind the three tools behind the switch; add three `_TOOL_LABELS` entries |
| `src/eve_ambient/sources/__init__.py` | register the `routines` source |
| `src/eve_ambient/pipeline.py` | add `"routines"` to `_REQUESTED_SOURCES`; record the run outcome |
| `src/eve_ambient/notify.py` | `compose_prompt` gains the routine branch |
| `family.yaml` | grant `routines` to both members |
| `aegra.json` | point `http.app` at the combined app |
| `docs/architecture.md` | document the subsystem |

**`open-assistant`, created:** `lib/domain/models/routines/routine.dart`, `lib/data/services/agent/routine_service.dart`, `lib/data/services/agent/langgraph_routine_service.dart`, `lib/ui/features/routines/view_models/routines_view_model.dart`, `lib/ui/features/routines/views/routines_screen.dart`, `lib/ui/features/routines/views/routine_card.dart`.

**`open-assistant`, modified:** `lib/data/services/agent/agent_service.dart`, `lib/data/repositories/agent_repository.dart`, `lib/ui/core/di/app_providers.dart`, `lib/config/router.dart`, `lib/ui/features/chat/views/chat_drawer.dart`.

---

## Task 1: The shared ambient-turn predicate

The guard every routines tool depends on. `save_widget`'s existing check on `configurable["is_ambient"]` does not work: nothing in `src/` ever sets that key (`eve_ambient.notify.deliver` passes no `config` to `runs.wait`), so it is falsy on every real ambient turn and only the hand-built test config makes it look alive. That is filed as EVE-30 and is **not** fixed here; this task builds the predicate that works, which EVE-30 will adopt.

**Files:**
- Modify: `src/eve/state.py` (append after `may_author`, around line 49)
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `is_ambient_text(text: str) -> bool` and `AMBIENT_MARKER_PREFIX`, both already in `src/eve/state.py`.
- Produces: `turn_is_ambient(messages: list) -> bool`. Every routines tool calls it with `state["messages"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_state.py`:

```python
def test_turn_is_ambient_reads_the_last_human_message():
    from langchain_core.messages import AIMessage, HumanMessage

    from eve.state import ambient_marker, turn_is_ambient

    messages = [
        HumanMessage(content="What's the weather?"),
        AIMessage(content="Clear and cold."),
        HumanMessage(content=ambient_marker("Noah") + "\nA package arrived."),
    ]

    assert turn_is_ambient(messages) is True


def test_turn_is_ambient_is_false_for_a_member_turn():
    from langchain_core.messages import AIMessage, HumanMessage

    from eve.state import ambient_marker, turn_is_ambient

    messages = [
        HumanMessage(content=ambient_marker("Noah") + "\nA package arrived."),
        AIMessage(content="Your package is here."),
        HumanMessage(content="Thanks, remind me every morning."),
    ]

    assert turn_is_ambient(messages) is False


def test_turn_is_ambient_fails_closed_on_an_empty_history():
    """No human message means nothing attributable to a member. A tool that
    creates a durable resource must refuse, not proceed."""
    from eve.state import turn_is_ambient

    assert turn_is_ambient([]) is True


def test_turn_is_ambient_handles_list_content():
    """The Responses API path delivers content as a list of blocks."""
    from langchain_core.messages import HumanMessage

    from eve.state import ambient_marker, turn_is_ambient

    marked = [{"type": "text", "text": ambient_marker("Noah") + "\nA thing."}]

    assert turn_is_ambient([HumanMessage(content=marked)]) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state.py -k turn_is_ambient -v`
Expected: FAIL with `ImportError: cannot import name 'turn_is_ambient'`

- [ ] **Step 3: Write the implementation**

Append to `src/eve/state.py`, immediately after `may_author`:

```python
def turn_is_ambient(messages: list) -> bool:
    """True when this turn was composed by the ambient pipeline rather than
    typed by a family member.

    The same question `may_author` asks, answered from the message list
    instead of a pre-extracted string, because a tool holds
    `Annotated[EveState, InjectedState]` and not the human text. One
    predicate, so a third copy cannot drift from the other two - the reason
    `may_author`'s own docstring gives for existing.

    Do NOT reimplement this as a check on `config.configurable["is_ambient"]`.
    Nothing in `src/` sets that key: `eve_ambient.notify.deliver` calls
    `runs.wait(thread_id, "eve", input={...})` with no `config` at all, so
    such a guard is inert in production and passes its tests only because
    they build the config by hand (EVE-30).

    Fails CLOSED: a history with no HumanMessage at all is treated as
    ambient, because a turn with nothing attributable to a member is not one
    that may create a durable resource in their account.
    """
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return is_ambient_text(_text_of(message.content))
    return True


def _text_of(content) -> str:
    """Content is a string on the Chat Completions path and a list of blocks
    on the Responses path - the same split `eve_ambient.notify._text_of`
    handles for AI messages."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""
```

Add to the imports at the top of `src/eve/state.py`:

```python
from langchain_core.messages import HumanMessage
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_state.py -v`
Expected: PASS, including the pre-existing `is_ambient_text` tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/state.py tests/test_state.py
git commit -m "feat(state): one shared predicate for whether a turn is ambient"
```

---

## Task 2: The cadence vocabulary

Pure functions, no I/O, no imports from `eve`. This is the whole schedule language.

**Files:**
- Create: `src/eve/routines/__init__.py` (empty), `src/eve/routines/cadence.py`
- Test: `tests/test_routines_cadence.py`

**Interfaces:**
- Produces:
  - `validate(cadence: dict) -> str | None` (None means valid; a string is the diagnostic)
  - `next_after(cadence: dict, timezone: str, after: datetime) -> datetime` (returns UTC)
  - `describe(cadence: dict) -> str` (English, for Eve's confirmation and the compose prompt)
  - `MAX_EVERY_HOURS = 168`, `MIN_EVERY_HOURS = 1`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_routines_cadence.py`:

```python
"""The cadence vocabulary is closed and model-authored, so every rejection
matters as much as every acceptance."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from eve.routines import cadence


def test_every_hours_is_valid_within_its_bounds():
    assert cadence.validate({"every_hours": 6}) is None
    assert cadence.validate({"every_hours": 1}) is None
    assert cadence.validate({"every_hours": 168}) is None


def test_a_sub_hourly_cadence_is_refused():
    """A VOICE turn every minute is the runaway this floor exists to stop."""
    assert cadence.validate({"every_hours": 0}) is not None
    assert cadence.validate({"every_hours": -1}) is not None


def test_a_cadence_longer_than_a_week_is_refused():
    assert cadence.validate({"every_hours": 169}) is not None


def test_a_boolean_is_not_an_hour_count():
    """bool is an int subclass; True must not pass as 1."""
    assert cadence.validate({"every_hours": True}) is not None


def test_daily_at_is_valid():
    assert cadence.validate({"daily_at": "08:00"}) is None
    assert cadence.validate({"daily_at": "23:59"}) is None


def test_a_malformed_time_is_refused():
    assert cadence.validate({"daily_at": "8am"}) is not None
    assert cadence.validate({"daily_at": "25:00"}) is not None
    assert cadence.validate({"daily_at": ""}) is not None


def test_weekly_at_is_valid():
    assert cadence.validate({"weekly_at": {"day": "sunday", "time": "19:00"}}) is None


def test_an_unknown_weekday_is_refused():
    assert cadence.validate({"weekly_at": {"day": "caturday", "time": "19:00"}}) is not None


def test_an_unknown_shape_is_refused():
    assert cadence.validate({"cron": "* * * * *"}) is not None
    assert cadence.validate({}) is not None
    assert cadence.validate({"every_hours": 6, "daily_at": "08:00"}) is not None


def test_a_non_dict_is_refused():
    assert cadence.validate("daily") is not None
    assert cadence.validate(None) is not None


def test_every_hours_advances_by_that_many_hours():
    after = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    result = cadence.next_after({"every_hours": 6}, "America/Vancouver", after)

    assert result == datetime(2026, 3, 1, 18, 0, tzinfo=UTC)


def test_daily_at_lands_on_the_next_local_occurrence():
    """08:00 in Vancouver on a PST date is 16:00 UTC."""
    after = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)  # 12:00 local

    result = cadence.next_after({"daily_at": "08:00"}, "America/Vancouver", after)

    assert result == datetime(2026, 1, 11, 16, 0, tzinfo=UTC)


def test_daily_at_uses_today_when_the_time_has_not_passed():
    after = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)  # 00:00 local

    result = cadence.next_after({"daily_at": "08:00"}, "America/Vancouver", after)

    assert result == datetime(2026, 1, 10, 16, 0, tzinfo=UTC)


def test_daily_at_holds_local_time_across_a_dst_change():
    """The whole reason timezone is a stored column: 08:00 stays 08:00
    locally, so the UTC instant moves by an hour across the transition.
    US DST began 2026-03-08."""
    before = cadence.next_after(
        {"daily_at": "08:00"}, "America/Vancouver", datetime(2026, 3, 6, 20, 0, tzinfo=UTC)
    )
    after = cadence.next_after(
        {"daily_at": "08:00"}, "America/Vancouver", datetime(2026, 3, 9, 20, 0, tzinfo=UTC)
    )

    assert before.hour == 16  # PST, UTC-8
    assert after.hour == 15   # PDT, UTC-7


def test_weekly_at_finds_the_next_matching_weekday():
    # 2026-01-10 is a Saturday. 12:00 local.
    after = datetime(2026, 1, 10, 20, 0, tzinfo=UTC)

    result = cadence.next_after(
        {"weekly_at": {"day": "sunday", "time": "19:00"}}, "America/Vancouver", after
    )

    # Sunday 2026-01-11 19:00 PST is Monday 03:00 UTC.
    assert result == datetime(2026, 1, 12, 3, 0, tzinfo=UTC)


def test_weekly_at_rolls_a_full_week_when_today_already_passed():
    # Sunday 2026-01-11, 20:00 local, past the 19:00 slot.
    after = datetime(2026, 1, 12, 4, 0, tzinfo=UTC)

    result = cadence.next_after(
        {"weekly_at": {"day": "sunday", "time": "19:00"}}, "America/Vancouver", after
    )

    assert result == datetime(2026, 1, 19, 3, 0, tzinfo=UTC)


def test_an_unknown_timezone_falls_back_to_utc_rather_than_raising():
    """Same posture as eve_ambient.gates._zone: a bad timezone must not stop
    a routine from ever firing."""
    result = cadence.next_after(
        {"daily_at": "08:00"}, "Mars/Olympus", datetime(2026, 1, 10, 20, 0, tzinfo=UTC)
    )

    assert result == datetime(2026, 1, 11, 8, 0, tzinfo=UTC)


def test_next_after_always_returns_a_time_strictly_later():
    """The claim in store.claim_due compares on this. A cadence that returned
    its own input would re-fire the same occurrence forever."""
    after = datetime(2026, 1, 10, 16, 0, tzinfo=UTC)  # exactly 08:00 local

    result = cadence.next_after({"daily_at": "08:00"}, "America/Vancouver", after)

    assert result > after


@pytest.mark.parametrize(
    "value,expected",
    [
        ({"every_hours": 1}, "every hour"),
        ({"every_hours": 6}, "every 6 hours"),
        ({"daily_at": "08:00"}, "every day at 08:00"),
        ({"weekly_at": {"day": "sunday", "time": "19:00"}}, "every Sunday at 19:00"),
    ],
)
def test_describe_reads_as_english(value, expected):
    assert cadence.describe(value) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routines_cadence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.routines'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/routines/__init__.py` as an empty file.

Create `src/eve/routines/cadence.py`:

```python
"""The closed cadence vocabulary.

Three shapes, validated in Python rather than by columns, for the reason the
widget recipe is jsonb: the set of legal shapes grows and a migration per
shape is the cost that choice exists to avoid.

Pure functions only. This module imports nothing from `eve`, which is what
lets the tools, the store, and the ambient source all depend on it without a
cycle, and what makes every rule here testable without a database.

Closed on purpose. A cron expression is more expressive and strictly worse
here: a model-authored `* * * * *` is a paid VOICE turn every minute, and
neither a mobile editor nor a plain-English rendering can be built over an
open grammar.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIN_EVERY_HOURS = 1
MAX_EVERY_HOURS = 168

_DAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_KEYS = ("every_hours", "daily_at", "weekly_at")


def _parse_time(raw: object) -> time | None:
    if not isinstance(raw, str):
        return None
    try:
        return time.fromisoformat(raw)
    except ValueError:
        return None


def validate(cadence: object) -> str | None:
    """None when the cadence is legal; otherwise a diagnostic a model can act
    on. Every rejection names what was wrong and what is legal, because the
    caller is a language model retrying from the message."""
    if not isinstance(cadence, dict):
        return "a cadence must be an object"

    present = [key for key in _KEYS if key in cadence]
    if len(present) != 1 or len(cadence) != 1:
        return (
            "a cadence has exactly one of every_hours, daily_at, or weekly_at"
        )

    if "every_hours" in cadence:
        hours = cadence["every_hours"]
        # bool is an int subclass; True must not read as 1.
        if not isinstance(hours, int) or isinstance(hours, bool):
            return "every_hours must be a whole number of hours"
        if hours < MIN_EVERY_HOURS or hours > MAX_EVERY_HOURS:
            return (
                f"every_hours must be between {MIN_EVERY_HOURS} and "
                f"{MAX_EVERY_HOURS}; nothing may run more often than hourly"
            )
        return None

    if "daily_at" in cadence:
        if _parse_time(cadence["daily_at"]) is None:
            return "daily_at must be a 24-hour time like \"08:00\""
        return None

    weekly = cadence["weekly_at"]
    if not isinstance(weekly, dict):
        return "weekly_at must be an object with day and time"
    day = weekly.get("day")
    if not isinstance(day, str) or day.lower() not in _DAYS:
        return f"weekly_at.day must be one of: {', '.join(_DAYS)}"
    if _parse_time(weekly.get("time")) is None:
        return "weekly_at.time must be a 24-hour time like \"19:00\""
    return None


def _zone(timezone: str) -> ZoneInfo:
    """An unknown timezone degrades to UTC rather than raising, the same
    posture `eve_ambient.gates._zone` takes: a bad timezone string must not
    be able to stop a routine from ever firing again."""
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _at_local(local_day: datetime, at: time) -> datetime:
    return local_day.replace(
        hour=at.hour, minute=at.minute, second=0, microsecond=0
    )


def next_after(cadence: dict, timezone: str, after: datetime) -> datetime:
    """The next firing time strictly after `after`, in UTC.

    Computed in local time and converted, not the other way round, which is
    what makes 08:00 stay 08:00 across a DST transition. The stored
    `timezone` column exists for exactly this: a member who moves keeps their
    existing routines firing at the local times they chose.
    """
    zone = _zone(timezone)

    if "every_hours" in cadence:
        return after + timedelta(hours=int(cadence["every_hours"]))

    if "daily_at" in cadence:
        at = _parse_time(cadence["daily_at"])
        local = after.astimezone(zone)
        candidate = _at_local(local, at)
        if candidate <= local:
            candidate = _at_local(local + timedelta(days=1), at)
        return candidate.astimezone(ZoneInfo("UTC"))

    weekly = cadence["weekly_at"]
    at = _parse_time(weekly["time"])
    target = _DAYS.index(str(weekly["day"]).lower())
    local = after.astimezone(zone)
    ahead = (target - local.weekday()) % 7
    candidate = _at_local(local + timedelta(days=ahead), at)
    if candidate <= local:
        candidate = _at_local(local + timedelta(days=ahead + 7), at)
    return candidate.astimezone(ZoneInfo("UTC"))


def describe(cadence: dict) -> str:
    """English, for Eve's spoken confirmation and the compose prompt. The
    client renders its own prose from the structured object rather than
    consuming this, so the two never have to agree on wording."""
    if "every_hours" in cadence:
        hours = int(cadence["every_hours"])
        return "every hour" if hours == 1 else f"every {hours} hours"
    if "daily_at" in cadence:
        return f"every day at {cadence['daily_at']}"
    weekly = cadence["weekly_at"]
    return f"every {str(weekly['day']).capitalize()} at {weekly['time']}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routines_cadence.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/routines/__init__.py src/eve/routines/cadence.py tests/test_routines_cadence.py
git commit -m "feat(routines): the closed cadence vocabulary"
```

---

## Task 3: The migration

**Files:**
- Create: `alembic/versions/0011_eve_routine.py`
- Test: `tests/test_alembic_graph.py` (already exists; it must keep passing)

**Interfaces:**
- Consumes: revision `0010_eve_widget_resource`, which is the current single head.
- Produces: the `eve_routine` table, consumed by Task 4's store.

- [ ] **Step 1: Confirm the current head**

Run: `uv run alembic heads`
Expected: exactly one head, `0010_eve_widget_resource`. If there is more than one, stop and report; this plan assumes a single head.

- [ ] **Step 2: Write the migration**

Create `alembic/versions/0011_eve_routine.py`:

```python
"""One row per standing instruction Eve runs on a schedule.

Revision ID: 0011_eve_routine
Revises: 0010_eve_widget_resource

`cadence` is jsonb and validated in Python (`eve.routines.cadence`) rather
than by columns, the same choice `0010_eve_widget_resource` made for the
widget recipe and for the same reason: the set of legal shapes grows, and a
migration per shape is the cost that avoids.

`timezone` is snapshotted from the member at creation rather than looked up
from family.yaml at fire time, so a member who moves keeps their existing
routines firing at the local times they chose.

`next_run_at` is stored rather than computed so that firing is a
compare-and-advance: the source claims a row by advancing this column before
emitting a signal, which is what stops a slow compose turn overlapping the
next tick from firing the same occurrence twice.
"""
from alembic import op

revision = "0011_eve_routine"
down_revision = "0010_eve_widget_resource"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_routine (
          id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub           text        NOT NULL,
          title                text        NOT NULL,
          instruction          text        NOT NULL,
          cadence              jsonb       NOT NULL,
          timezone             text        NOT NULL,
          status               text        NOT NULL DEFAULT 'active',
          next_run_at          timestamptz NOT NULL,
          last_run_at          timestamptz,
          last_outcome         text,
          consecutive_failures int         NOT NULL DEFAULT 0,
          expires_at           timestamptz,
          revision             bigint      NOT NULL DEFAULT 1,
          created_at           timestamptz NOT NULL DEFAULT now(),
          updated_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # The ambient tick's only query: "every active routine that is due."
    op.execute(
        "CREATE INDEX eve_routine_due ON eve_routine (status, next_run_at)"
    )
    # The screen's and list_routines' query, newest first.
    op.execute(
        "CREATE INDEX eve_routine_owner"
        " ON eve_routine (member_sub, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_routine")
```

- [ ] **Step 3: Verify the migration applies and the graph stays linear**

```bash
docker compose -f docker-compose.test.yml up -d
uv run eve-migrate
uv run alembic heads
```

Expected: `eve-migrate` succeeds; `alembic heads` shows exactly one head, `0011_eve_routine`.

- [ ] **Step 4: Run the alembic graph test**

Run: `uv run pytest tests/test_alembic_graph.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/0011_eve_routine.py
git commit -m "feat(routines): the eve_routine table"
```

---

## Task 4: The store

**Files:**
- Create: `src/eve/routines/store.py`
- Test: `tests/test_routines_store.py`

**Interfaces:**
- Consumes: `eve.memory.db.get_pool`, the `eve_routine` table from Task 3.
- Produces, all `async`:
  - `create(member_sub, title, instruction, cadence, timezone, next_run_at, expires_at) -> dict`
  - `list_for(member_sub) -> list[dict]`
  - `get(member_sub, routine_id) -> dict | None`
  - `find_by_title(member_sub, text) -> list[dict]`
  - `delete(member_sub, routine_id) -> bool`
  - `update(member_sub, routine_id, expected_revision, **fields) -> dict | None`
  - `claim_due(now) -> list[dict]` (household-wide)
  - `record_run(routine_id, outcome, failure_limit) -> None`
  - `expire(routine_id) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_routines_store.py`:

```python
"""Ownership is enforced here or not at all: Aegra's @auth.on handlers do not
reach a custom route, so every statement carries member_sub."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

CADENCE = {"daily_at": "08:00"}


async def _make(member_sub="sub-noah", *, next_run_at=None, title="Flights"):
    from eve.routines import store

    return await store.create(
        member_sub=member_sub,
        title=title,
        instruction="Check Aeroplan fares YVR to SJD.",
        cadence=CADENCE,
        timezone="America/Vancouver",
        next_run_at=next_run_at or datetime.now(UTC) + timedelta(hours=1),
        expires_at=None,
    )


@pytest.fixture(autouse=True)
async def _clean():
    from eve.memory.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM eve_routine")
    yield
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM eve_routine")


async def test_a_created_routine_comes_back_with_an_id_and_revision_one():
    row = await _make()

    assert row["id"]
    assert row["revision"] == 1
    assert row["status"] == "active"
    assert row["consecutive_failures"] == 0


async def test_listing_returns_only_this_members_routines():
    from eve.routines import store

    await _make("sub-noah")
    await _make("sub-kendra")

    rows = await store.list_for("sub-noah")

    assert len(rows) == 1
    assert rows[0]["member_sub"] == "sub-noah"


async def test_getting_another_members_routine_returns_none():
    """Absent and foreign answer identically, so probing learns nothing."""
    from eve.routines import store

    row = await _make("sub-kendra")

    assert await store.get("sub-noah", row["id"]) is None


async def test_deleting_another_members_routine_reports_false():
    from eve.routines import store

    row = await _make("sub-kendra")

    assert await store.delete("sub-noah", row["id"]) is False
    assert await store.get("sub-kendra", row["id"]) is not None


async def test_find_by_title_matches_case_insensitively_on_a_substring():
    from eve.routines import store

    await _make(title="Aeroplan flights to Los Cabos")

    found = await store.find_by_title("sub-noah", "flights")

    assert len(found) == 1


async def test_updating_bumps_the_revision():
    from eve.routines import store

    row = await _make()

    updated = await store.update(
        "sub-noah", row["id"], row["revision"], status="paused"
    )

    assert updated["status"] == "paused"
    assert updated["revision"] == row["revision"] + 1


async def test_updating_at_a_stale_revision_returns_none():
    """The guard is in the UPDATE's own WHERE clause, not a read-then-write,
    so two concurrent writers cannot both pass it."""
    from eve.routines import store

    row = await _make()
    await store.update("sub-noah", row["id"], row["revision"], title="First")

    assert await store.update(
        "sub-noah", row["id"], row["revision"], title="Second"
    ) is None


async def test_claim_due_returns_only_active_routines_that_are_due():
    from eve.routines import store

    past = datetime.now(UTC) - timedelta(minutes=5)
    future = datetime.now(UTC) + timedelta(hours=5)
    due = await _make(next_run_at=past, title="Due")
    await _make(next_run_at=future, title="Later")
    paused = await _make(next_run_at=past, title="Paused")
    await store.update("sub-noah", paused["id"], paused["revision"], status="paused")

    claimed = await store.claim_due(datetime.now(UTC))

    assert [row["id"] for row in claimed] == [due["id"]]


async def test_claim_due_advances_next_run_at_so_a_second_claim_finds_nothing():
    """The whole reason next_run_at is stored: a compose turn still running
    when the next tick arrives must not re-fire the same occurrence.

    This test pins a bug that was live in the first draft of this plan.
    Writing `SET next_run_at = now()` reads as "claimed" and is not: `now()`
    is the transaction timestamp and the predicate is `next_run_at <= now()`,
    so the row is still due the moment the statement commits and the next
    tick claims it again. The claim writes a LEASE instead.
    """
    from eve.routines import store

    await _make(next_run_at=datetime.now(UTC) - timedelta(minutes=5))

    first = await store.claim_due(datetime.now(UTC))
    second = await store.claim_due(datetime.now(UTC))

    assert len(first) == 1
    assert second == []


async def test_a_claim_leases_the_row_rather_than_releasing_it():
    """A worker that dies between claiming and scheduling must leave the
    firing retriable, not lost. The lease expiring is what retries it."""
    from eve.routines import store

    await _make(next_run_at=datetime.now(UTC) - timedelta(minutes=5))

    claimed = await store.claim_due(datetime.now(UTC))
    row = await store.get("sub-noah", claimed[0]["id"])

    assert row["next_run_at"] > datetime.now(UTC)
    assert row["next_run_at"] <= datetime.now(UTC) + timedelta(
        minutes=store.CLAIM_LEASE_MINUTES + 1
    )


async def test_claim_due_carries_the_scheduled_time_it_claimed():
    """The signal key is <id>:<scheduled_time>, so the claimed occurrence has
    to come back with the row."""
    from eve.routines import store

    when = datetime.now(UTC) - timedelta(minutes=5)
    await _make(next_run_at=when)

    claimed = await store.claim_due(datetime.now(UTC))

    assert claimed[0]["scheduled_for"] == when


async def test_recording_a_spoke_run_resets_the_failure_counter():
    from eve.routines import store

    row = await _make()
    await store.record_run(row["id"], "error", failure_limit=5)
    await store.record_run(row["id"], "spoke", failure_limit=5)

    fresh = await store.get("sub-noah", row["id"])

    assert fresh["consecutive_failures"] == 0
    assert fresh["last_outcome"] == "spoke"
    assert fresh["last_run_at"] is not None


async def test_a_silent_run_is_a_success_not_a_failure():
    """Silence is the routine working. Counting a veto as a failure would
    auto-pause every well-behaved routine within a week."""
    from eve.routines import store

    row = await _make()
    await store.record_run(row["id"], "error", failure_limit=5)
    await store.record_run(row["id"], "silent", failure_limit=5)

    fresh = await store.get("sub-noah", row["id"])

    assert fresh["consecutive_failures"] == 0
    assert fresh["status"] == "active"


async def test_reaching_the_failure_limit_pauses_the_routine():
    from eve.routines import store

    row = await _make()
    for _ in range(5):
        await store.record_run(row["id"], "error", failure_limit=5)

    fresh = await store.get("sub-noah", row["id"])

    assert fresh["consecutive_failures"] == 5
    assert fresh["status"] == "paused"


async def test_expiring_moves_the_routine_out_of_the_due_query():
    from eve.routines import store

    row = await _make(next_run_at=datetime.now(UTC) - timedelta(minutes=5))
    await store.expire(row["id"])

    assert await store.claim_due(datetime.now(UTC)) == []
    assert (await store.get("sub-noah", row["id"]))["status"] == "expired"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker compose -f docker-compose.test.yml up -d
uv run pytest tests/test_routines_store.py -m integration -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'eve.routines.store'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/routines/store.py`:

```python
"""Every eve_routine SQL statement.

Every statement here carries `member_sub` in its WHERE clause, with exactly
one deliberate exception: `claim_due`, which the ambient tick runs
household-wide and which returns each row's own `member_sub` for the signal
to carry. That is the same shape `eve.computer.store.recently_resolved_tasks`
already has, and it is named here so it reads as a decision rather than an
oversight. `record_run` and `expire` are keyed by a routine id the caller
only ever obtains from `claim_due`, so they inherit that scoping.

A routine id is a high-entropy locator and nothing more: Aegra's `@auth.on`
handlers scope threads and the store API, but they do not reach custom
routes, so ownership is enforced in this module or not at all.
"""

from __future__ import annotations

from datetime import datetime

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.memory.db import get_pool

_COLUMNS = (
    "id, member_sub, title, instruction, cadence, timezone, status,"
    " next_run_at, last_run_at, last_outcome, consecutive_failures,"
    " expires_at, revision, created_at, updated_at"
)

# Only these may be written through `update`. A caller cannot reach
# consecutive_failures, revision, or member_sub by naming them.
_UPDATABLE = ("title", "instruction", "cadence", "status", "next_run_at", "expires_at")

# How long a claimed routine stays un-claimable while its firing runs. Bounds
# how long a firing lost to a crashed worker stays invisible, so it must
# comfortably exceed one compose turn while staying well under the shortest
# legal cadence (one hour). See `claim_due`.
CLAIM_LEASE_MINUTES = 15


def _row(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {**row, "id": str(row["id"])}


async def create(
    member_sub: str,
    title: str,
    instruction: str,
    cadence: dict,
    timezone: str,
    next_run_at: datetime,
    expires_at: datetime | None,
) -> dict:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO eve_routine"
                " (member_sub, title, instruction, cadence, timezone,"
                "  next_run_at, expires_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                f" RETURNING {_COLUMNS}",
                (
                    member_sub,
                    title,
                    instruction,
                    Jsonb(cadence),
                    timezone,
                    next_run_at,
                    expires_at,
                ),
            )
            return _row(await cur.fetchone())


async def list_for(member_sub: str) -> list[dict]:
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_routine"
                " WHERE member_sub = %s ORDER BY created_at DESC",
                (member_sub,),
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def get(member_sub: str, routine_id: str) -> dict | None:
    """None for both a missing id and another member's id: the caller turns
    both into the same 404, so probing cannot distinguish them."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_routine"
                " WHERE id = %s AND member_sub = %s",
                (routine_id, member_sub),
            )
            return _row(await cur.fetchone())


async def find_by_title(member_sub: str, text: str) -> list[dict]:
    """Substring, case-insensitive, because a member says "stop tracking
    flights" and not a uuid. The caller decides what an ambiguous match
    means."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM eve_routine"
                " WHERE member_sub = %s AND title ILIKE %s"
                " ORDER BY created_at DESC",
                (member_sub, f"%{text}%"),
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def delete(member_sub: str, routine_id: str) -> bool:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM eve_routine WHERE id = %s AND member_sub = %s",
            (routine_id, member_sub),
        )
        return cur.rowcount > 0


async def update(
    member_sub: str, routine_id: str, expected_revision: int, **fields
) -> dict | None:
    """None when the row is absent, foreign, or at a different revision.

    The revision check is in the UPDATE's own WHERE clause rather than a
    read-then-write, so two concurrent writers cannot both pass it - the same
    guard `eve.widgets.store.update_filters` uses.
    """
    writable = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not writable:
        return await get(member_sub, routine_id)

    assignments = ", ".join(f"{name} = %s" for name in writable)
    values = [
        Jsonb(value) if name == "cadence" else value
        for name, value in writable.items()
    ]

    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"UPDATE eve_routine SET {assignments},"
                " revision = revision + 1, updated_at = now()"
                " WHERE id = %s AND member_sub = %s AND revision = %s"
                f" RETURNING {_COLUMNS}",
                (*values, routine_id, member_sub, expected_revision),
            )
            return _row(await cur.fetchone())


async def claim_due(now: datetime) -> list[dict]:
    """Every active routine that is due, claimed in the same statement that
    finds it.

    Household-wide, which is the one query in this module without a
    `member_sub` filter: the ambient tick polls once per tick for everyone
    (`per_member=False`) and each row carries its own member. The claim
    happens HERE, before any signal is emitted, so a compose turn still
    running when the next tick arrives cannot fire the same occurrence twice.

    The claim writes a LEASE, not `now`. Setting `next_run_at = now()` looks
    right and is wrong: `now()` is the transaction timestamp, the predicate is
    `next_run_at <= now()`, so the row is still due the instant the statement
    commits and the very next tick re-claims it. Verified against real
    Postgres while writing this plan: the naive form returns the same row on
    two consecutive claims. Pushing `next_run_at` a bounded interval into the
    future makes the claim idempotent for the length of the lease, and
    `eve_ambient.sources.routines.poll` then overwrites it with the real
    cadence time via `schedule_next`.

    The lease is also the crash-recovery story. A worker that dies between
    claiming and calling `schedule_next` leaves the row leased rather than
    lost, so the firing is retried once the lease expires instead of being
    silently dropped forever. `CLAIM_LEASE_MINUTES` is therefore an upper
    bound on how long a crashed firing stays invisible, and must comfortably
    exceed one compose turn.

    `scheduled_for` is the value of `next_run_at` this claim consumed, not
    the lease; it is what the signal key is built from.

    A routine whose `next_run_at` is far in the past (the service was down
    overnight) fires ONCE and schedules forward from now, never a backlog:
    six silent 8am checks delivered at once is the most annoying possible
    behaviour, and the member wanted a habit rather than an audit trail.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"""
                WITH due AS (
                  SELECT id, next_run_at FROM eve_routine
                   WHERE status = 'active' AND next_run_at <= %(now)s
                   ORDER BY next_run_at
                   FOR UPDATE SKIP LOCKED
                )
                UPDATE eve_routine AS r
                   SET next_run_at = %(now)s
                                     + make_interval(mins => %(lease)s),
                       updated_at = now()
                  FROM due
                 WHERE r.id = due.id
                RETURNING {', '.join('r.' + c.strip() for c in _COLUMNS.split(','))},
                          due.next_run_at AS scheduled_for
                """,
                {"now": now, "lease": CLAIM_LEASE_MINUTES},
            )
            return [_row(dict(row)) for row in await cur.fetchall()]


async def schedule_next(routine_id: str, next_run_at: datetime) -> None:
    """Set the next firing time after a claim. Separate from `claim_due`
    because the cadence maths lives in `eve.routines.cadence` and the store
    holds no vocabulary."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_routine SET next_run_at = %s, updated_at = now()"
            " WHERE id = %s",
            (next_run_at, routine_id),
        )


async def record_run(routine_id: str, outcome: str, failure_limit: int) -> None:
    """`outcome` is `spoke`, `silent`, or `error`.

    Only `error` increments. A `silent` run is the routine working correctly
    - Eve looked and there was nothing worth saying - and counting it as a
    failure would auto-pause every well-behaved routine within a week.

    The pause is applied in the same statement as the increment, so a routine
    cannot be observed at the limit and still active.
    """
    pool = await get_pool()
    async with pool.connection() as conn:
        if outcome == "error":
            await conn.execute(
                """
                UPDATE eve_routine
                   SET consecutive_failures = consecutive_failures + 1,
                       status = CASE
                         WHEN consecutive_failures + 1 >= %(limit)s THEN 'paused'
                         ELSE status
                       END,
                       last_run_at = now(),
                       last_outcome = 'error',
                       updated_at = now()
                 WHERE id = %(id)s
                """,
                {"id": routine_id, "limit": failure_limit},
            )
            return
        await conn.execute(
            "UPDATE eve_routine"
            " SET consecutive_failures = 0, last_run_at = now(),"
            "     last_outcome = %s, updated_at = now()"
            " WHERE id = %s",
            (outcome, routine_id),
        )


async def expire(routine_id: str) -> None:
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_routine SET status = 'expired', updated_at = now()"
            " WHERE id = %s",
            (routine_id,),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routines_store.py -m integration -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/routines/store.py tests/test_routines_store.py
git commit -m "feat(routines): the eve_routine store"
```

---

## Task 5: Settings

**Files:**
- Modify: `src/eve/settings.py` (append a block after the `coding_*` block, around line 210)
- Test: `tests/test_routines_settings.py`

**Interfaces:**
- Produces: `settings.routines_enabled: bool`, `settings.routine_failure_limit: int`, `settings.routine_max_instruction_chars: int`, `settings.routine_max_title_chars: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_routines_settings.py`:

```python
from __future__ import annotations


def test_routines_are_off_by_default(monkeypatch):
    """Standing spend must be opted into, the same posture ambient_enabled,
    sandbox_enabled, computer_enabled and coding_enabled all take."""
    monkeypatch.delenv("EVE_ROUTINES_ENABLED", raising=False)

    from eve.settings import Settings

    assert Settings().routines_enabled is False


def test_the_failure_limit_defaults_to_five(monkeypatch):
    monkeypatch.delenv("EVE_ROUTINE_FAILURE_LIMIT", raising=False)

    from eve.settings import Settings

    assert Settings().routine_failure_limit == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routines_settings.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'routines_enabled'`

- [ ] **Step 3: Write the implementation**

Append to the `Settings` class in `src/eve/settings.py`, after the `coding_*` block:

```python
    # EVE-25 (Scheduled routines). See docs/superpowers/specs/
    # 2026-09-21-eve-routines-design.md.
    #
    # Off by default for the same reason ambient_enabled and coding_enabled
    # are: a routine is a recurring paid VOICE-tier turn that nobody is
    # watching, so a deployment that has not deliberately accepted standing
    # spend must run none.
    routines_enabled: bool = False
    # Consecutive INFRASTRUCTURE failures before a routine pauses itself and
    # says so once. A NOTHING veto is not a failure: silence is the routine
    # working. Five is roughly a day of hourly retries.
    routine_failure_limit: int = 5
    routine_max_title_chars: int = 80
    # The instruction is replayed into a VOICE turn on every firing, so its
    # length is a standing cost rather than a one-off one.
    routine_max_instruction_chars: int = 2000
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_routines_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eve/settings.py tests/test_routines_settings.py
git commit -m "feat(routines): settings, off by default"
```

---

## Task 6: The three tools

**Files:**
- Create: `src/eve/routines/tools.py`
- Test: `tests/test_routines_tools.py`

**Interfaces:**
- Consumes: `eve.routines.cadence` (Task 2), `eve.routines.store` (Task 4), `eve.settings` (Task 5), `eve.state.turn_is_ambient` (Task 1), `eve.specialists.permissions.permission_denial`.
- Produces: `schedule_routine`, `list_routines`, `cancel_routine`, and `PERMISSION = "routines"`. Task 7 binds all three.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_routines_tools.py`:

```python
"""These three tools are the only way a routine is created or destroyed, so
every guard that matters is here.

Note how the ambient tests below build a real marked message rather than
setting a config key: a test that invents its own marker of ambience can pass
against a guard production never triggers, which is exactly how
save_widget's broken `is_ambient` check survived review (EVE-30).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langchain_core.messages import HumanMessage

from eve.state import ambient_marker

CADENCE = {"daily_at": "08:00"}

MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "timezone": "America/Vancouver",
    "permissions": ["routines"],
}

CONFIG = {"configurable": {"member": MEMBER}}
NO_PERMS = {
    "configurable": {"member": {**MEMBER, "permissions": []}}
}

TYPED = {"messages": [HumanMessage(content="Track Aeroplan fares to Los Cabos.")]}
AMBIENT = {
    "messages": [
        HumanMessage(content=ambient_marker("Noah") + "\nA routine fired.")
    ]
}

ROW = {
    "id": "r-1",
    "member_sub": "sub-noah",
    "title": "Flights",
    "instruction": "Check fares.",
    "cadence": CADENCE,
    "timezone": "America/Vancouver",
    "status": "active",
    "next_run_at": datetime(2026, 1, 11, 16, 0, tzinfo=UTC),
    "last_run_at": None,
    "last_outcome": None,
    "consecutive_failures": 0,
    "expires_at": None,
    "revision": 1,
}


def _call(tool, args, config=CONFIG, state=TYPED):
    return tool.ainvoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "args": {**args, "state": state},
            "id": "t1",
        },
        config=config,
    )


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("EVE_ROUTINES_ENABLED", "true")


async def test_scheduling_stores_a_routine_for_the_authenticated_member(monkeypatch):
    from eve.routines import tools

    seen = {}

    async def fake_create(**kwargs):
        seen.update(kwargs)
        return ROW

    monkeypatch.setattr(tools.store, "create", fake_create)

    result = await _call(
        tools.schedule_routine,
        {
            "title": "Flights",
            "instruction": "Check Aeroplan fares YVR to SJD.",
            "cadence": CADENCE,
        },
    )

    assert seen["member_sub"] == "sub-noah"
    assert seen["timezone"] == "America/Vancouver"
    assert seen["cadence"] == CADENCE
    assert "every day at 08:00" in result.content


async def test_an_ambient_turn_cannot_schedule_a_routine(monkeypatch):
    """The fork-bomb guard. A routine firing is an ambient turn, so a routine
    cannot create a routine."""
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("an ambient turn must not author a routine")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "Y", "cadence": CADENCE},
        state=AMBIENT,
    )

    assert "cannot" in result.content.lower()


async def test_an_ambient_turn_cannot_cancel_a_routine(monkeypatch):
    """A routine that reasons its way to "I should stop" cannot act on it."""
    from eve.routines import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("an ambient turn must not cancel a routine")

    monkeypatch.setattr(tools.store, "find_by_title", unreachable)
    monkeypatch.setattr(tools.store, "delete", unreachable)

    result = await _call(tools.cancel_routine, {"reference": "Flights"}, state=AMBIENT)

    assert "cannot" in result.content.lower()


async def test_an_ambient_turn_cannot_list_routines(monkeypatch):
    from eve.routines import tools

    async def unreachable(*args, **kwargs):
        raise AssertionError("an ambient turn must not enumerate routines")

    monkeypatch.setattr(tools.store, "list_for", unreachable)

    result = await _call(tools.list_routines, {}, state=AMBIENT)

    assert "cannot" in result.content.lower()


async def test_an_invalid_cadence_is_refused_with_a_diagnostic(monkeypatch):
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("must not store an invalid cadence")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "Y", "cadence": {"every_hours": 0}},
    )

    assert "hourly" in result.content.lower()


async def test_a_member_without_the_grant_is_refused(monkeypatch):
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("must not store without the grant")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "Y", "cadence": CADENCE},
        config=NO_PERMS,
    )

    assert "permission denied" in result.content.lower()


async def test_an_over_long_instruction_is_refused(monkeypatch):
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("must not store an over-long instruction")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "y" * 5000, "cadence": CADENCE},
    )

    assert "too long" in result.content.lower()


async def test_a_malformed_expiry_is_refused(monkeypatch):
    from eve.routines import tools

    async def unreachable(**kwargs):
        raise AssertionError("must not store a malformed expiry")

    monkeypatch.setattr(tools.store, "create", unreachable)

    result = await _call(
        tools.schedule_routine,
        {
            "title": "X",
            "instruction": "Y",
            "cadence": CADENCE,
            "expires_at": "end of February",
        },
    )

    assert "iso-8601" in result.content.lower()


async def test_listing_reports_cadence_and_last_outcome(monkeypatch):
    from eve.routines import tools

    async def fake_list_for(member_sub):
        return [{**ROW, "last_outcome": "silent", "last_run_at": datetime(2026, 1, 10, tzinfo=UTC)}]

    monkeypatch.setattr(tools.store, "list_for", fake_list_for)

    result = await _call(tools.list_routines, {})

    assert "Flights" in result.content
    assert "every day at 08:00" in result.content


async def test_listing_says_so_when_there_are_none(monkeypatch):
    from eve.routines import tools

    async def fake_list_for(member_sub):
        return []

    monkeypatch.setattr(tools.store, "list_for", fake_list_for)

    result = await _call(tools.list_routines, {})

    assert "no routines" in result.content.lower()


async def test_cancelling_by_title_deletes_the_match(monkeypatch):
    from eve.routines import tools

    deleted = {}

    async def fake_find(member_sub, text):
        return [ROW]

    async def fake_delete(member_sub, routine_id):
        deleted["id"] = routine_id
        return True

    monkeypatch.setattr(tools.store, "find_by_title", fake_find)
    monkeypatch.setattr(tools.store, "delete", fake_delete)

    result = await _call(tools.cancel_routine, {"reference": "flights"})

    assert deleted["id"] == "r-1"
    assert "Flights" in result.content


async def test_an_ambiguous_title_cancels_nothing_and_lists_the_candidates(monkeypatch):
    from eve.routines import tools

    async def fake_find(member_sub, text):
        return [ROW, {**ROW, "id": "r-2", "title": "Flight club"}]

    async def unreachable(*args, **kwargs):
        raise AssertionError("an ambiguous reference must delete nothing")

    monkeypatch.setattr(tools.store, "find_by_title", fake_find)
    monkeypatch.setattr(tools.store, "delete", unreachable)

    result = await _call(tools.cancel_routine, {"reference": "flight"})

    assert "Flights" in result.content
    assert "Flight club" in result.content


async def test_a_storage_failure_degrades_to_a_string(monkeypatch):
    """The global constraint: every tool returns, never raises."""
    from eve.routines import tools

    async def boom(**kwargs):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(tools.store, "create", boom)

    result = await _call(
        tools.schedule_routine,
        {"title": "X", "instruction": "Y", "cadence": CADENCE},
    )

    assert result.content.startswith("error:")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routines_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eve.routines.tools'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/routines/tools.py`:

```python
"""The only way a routine is created, listed, or destroyed.

Checks run in the same order `eve.widgets.tools` uses, and for the same
reason: refuse the turn before validating the input, validate the input
before checking the grant, check the grant before touching storage. The
cheapest and most categorical refusals come first.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.routines import cadence as cadence_rules, store
from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.state import EveState, turn_is_ambient

logger = logging.getLogger(__name__)

PERMISSION = "routines"

_AMBIENT_REFUSAL = (
    "A routine cannot create, change, or cancel a routine. Only {name} can, "
    "by asking me directly."
)

_SCHEDULE_DESCRIPTION = """Set up a standing request you will carry out on a schedule.

Use this when the member wants something checked or done repeatedly ("every
morning", "keep an eye on", "let me know if"), not for a one-off answer.

`instruction` is what you will read back to yourself on every run, so write it
as a complete standing request in the member's own terms, including anything
you will need that this conversation established. You will not see this
conversation again.

`cadence` is exactly one of:
  {"every_hours": <1 to 168>}
  {"daily_at": "HH:MM"}
  {"weekly_at": {"day": "monday".."sunday", "time": "HH:MM"}}

Nothing may run more often than hourly. Times are in the member's own
timezone.

`expires_at` is an optional ISO-8601 timestamp. Set it whenever the request
has a natural horizon ("flights in February"), so the routine stops on its own
instead of running forever."""


def _member(config: RunnableConfig) -> dict:
    return (config.get("configurable") or {}).get("member") or {}


def _refuse_ambient(member: dict) -> str:
    return _AMBIENT_REFUSAL.format(name=member.get("name") or "a family member")


@tool(description=_SCHEDULE_DESCRIPTION)
async def schedule_routine(
    title: str,
    instruction: str,
    cadence: dict,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
    expires_at: str | None = None,
) -> str:
    member = _member(config)
    settings = get_settings()

    # First, and categorically: a routine firing is an ambient turn, and an
    # ambient turn may not create a durable resource in a member's account.
    # This is what makes a fork bomb unreachable.
    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)

    error = cadence_rules.validate(cadence)
    if error is not None:
        return f"That schedule was rejected: {error}."

    if len(title) > settings.routine_max_title_chars:
        return (
            f"The routine title is too long: {len(title)} characters, "
            f"the limit is {settings.routine_max_title_chars}."
        )
    if len(instruction) > settings.routine_max_instruction_chars:
        return (
            f"The instruction is too long: {len(instruction)} characters, "
            f"the limit is {settings.routine_max_instruction_chars}."
        )

    expiry = None
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
        except ValueError:
            return f"expires_at must be ISO-8601, got {expires_at!r}."
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)

    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial

    timezone = member.get("timezone") or "UTC"
    now = datetime.now(UTC)
    try:
        first = cadence_rules.next_after(cadence, timezone, now)
        row = await store.create(
            member_sub=member["sub"],
            title=title,
            instruction=instruction,
            cadence=cadence,
            timezone=timezone,
            next_run_at=first,
            expires_at=expiry,
        )
    except Exception as exc:
        logger.warning("schedule_routine failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    ending = f", until {expiry.date().isoformat()}" if expiry else ""
    return (
        f"Scheduled \"{row['title']}\" {cadence_rules.describe(cadence)}"
        f"{ending}. First run {first.isoformat()}."
    )


@tool
async def list_routines(
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """List the standing requests you are already running for this member.

    Use this whenever they ask what you are watching, tracking, or checking
    for them."""
    member = _member(config)

    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)

    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial

    try:
        rows = await store.list_for(member["sub"])
    except Exception as exc:
        logger.warning("list_routines failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    if not rows:
        return "No routines are set up right now."

    lines = []
    for row in rows:
        last = row.get("last_outcome")
        seen = (
            f"last run {row['last_run_at'].isoformat()} ({last})"
            if row.get("last_run_at")
            else "not run yet"
        )
        state_note = "" if row["status"] == "active" else f" [{row['status']}]"
        lines.append(
            f"- \"{row['title']}\"{state_note}: "
            f"{cadence_rules.describe(row['cadence'])}, {seen}. "
            f"{row['instruction']}"
        )
    return "\n".join(lines)


@tool
async def cancel_routine(
    reference: str,
    state: Annotated[EveState, InjectedState],
    config: RunnableConfig,
) -> str:
    """Stop a standing request permanently. `reference` is the routine's title
    or part of it, as the member said it."""
    member = _member(config)

    if turn_is_ambient(state.get("messages") or []):
        return _refuse_ambient(member)

    denial = permission_denial(member.get("permissions") or [], PERMISSION)
    if denial is not None:
        return denial

    try:
        matches = await store.find_by_title(member["sub"], reference)
    except Exception as exc:
        logger.warning("cancel_routine lookup failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    if not matches:
        return f"No routine matches {reference!r}."
    if len(matches) > 1:
        titles = ", ".join(f"\"{row['title']}\"" for row in matches)
        return (
            f"{reference!r} matches more than one routine ({titles}). "
            "Nothing was cancelled; ask which one they mean."
        )

    row = matches[0]
    try:
        removed = await store.delete(member["sub"], row["id"])
    except Exception as exc:
        logger.warning("cancel_routine failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"

    if not removed:
        return f"No routine matches {reference!r}."
    return f"Cancelled \"{row['title']}\". It will not run again."
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routines_tools.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add src/eve/routines/tools.py tests/test_routines_tools.py
git commit -m "feat(routines): schedule, list and cancel tools"
```

---

## Task 7: Bind the tools and grant the permission

**Files:**
- Modify: `src/eve/graph.py` (imports, `_static_tools` around line 103, `_TOOL_LABELS`)
- Modify: `family.yaml`
- Modify: `tests/fixtures/family.yaml`
- Test: `tests/test_routines_graph.py`

**Interfaces:**
- Consumes: `schedule_routine`, `list_routines`, `cancel_routine` from Task 6; `settings.routines_enabled` from Task 5.
- Produces: the three tools bound into the graph, gated on the switch.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_routines_graph.py`:

```python
from __future__ import annotations


def _names(tools):
    return {tool.name for tool in tools}


def test_the_routine_tools_are_bound_when_enabled(monkeypatch):
    monkeypatch.setenv("EVE_ROUTINES_ENABLED", "true")

    from eve.graph import _static_tools
    from eve.settings import get_settings

    get_settings.cache_clear()
    names = _names(_static_tools())

    assert {"schedule_routine", "list_routines", "cancel_routine"} <= names


def test_the_routine_tools_are_absent_when_disabled(monkeypatch):
    """A deployment that has not accepted standing spend must not even be
    able to be asked for it."""
    monkeypatch.setenv("EVE_ROUTINES_ENABLED", "false")

    from eve.graph import _static_tools
    from eve.settings import get_settings

    get_settings.cache_clear()
    names = _names(_static_tools())

    assert not {"schedule_routine", "list_routines", "cancel_routine"} & names


def test_every_routine_tool_has_a_label(monkeypatch):
    """graph.py's own test_every_labelled_tool_is_a_real_tool checks the
    other direction; this checks these three are not missed."""
    monkeypatch.setenv("EVE_ROUTINES_ENABLED", "true")

    from eve.graph import _TOOL_LABELS

    for name in ("schedule_routine", "list_routines", "cancel_routine"):
        assert name in _TOOL_LABELS
        label = _TOOL_LABELS[name]
        assert label == label[0].upper() + label[1:]
        assert not label.endswith((".", "..."))
        assert len(label) <= 60
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routines_graph.py -v`
Expected: FAIL, the tools are not bound.

- [ ] **Step 3: Wire the graph**

In `src/eve/graph.py`, add to the imports (keeping alphabetical order within the `eve.` block):

```python
from eve.routines.tools import cancel_routine, list_routines, schedule_routine
```

In `_static_tools`, after the `coding_enabled` block:

```python
    if settings.routines_enabled:
        tools.append(schedule_routine)
        tools.append(list_routines)
        tools.append(cancel_routine)
```

In `_TOOL_LABELS`, add three entries. Each must finish the sentence "Right now it is ...", in sentence case, with no terminal period and no trailing ellipsis:

```python
    "schedule_routine": "Setting that up to run on a schedule",
    "list_routines": "Checking what I'm watching for you",
    "cancel_routine": "Stopping that routine",
```

- [ ] **Step 4: Grant the permission**

In `family.yaml`, add to **both** members' `permissions` lists:

```yaml
      # EVE-25: creating and cancelling standing requests Eve runs on a
      # schedule. A bare noun like `finances`, because the tools are the
      # only write surface and there is no read-only half to distinguish.
      - routines
```

Do the same in `tests/fixtures/family.yaml` for whichever members it defines, so integration tests see the grant.

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_routines_graph.py -v
uv run pytest tests/test_graph.py tests/test_family.py -v
```

Expected: PASS. In particular `test_every_labelled_tool_is_a_real_tool` and `test_every_tool_label_reads_like_an_activity` must still pass.

- [ ] **Step 6: Commit**

```bash
git add src/eve/graph.py family.yaml tests/fixtures/family.yaml tests/test_routines_graph.py
git commit -m "feat(routines): bind the tools behind the switch and grant the permission"
```

---

## Task 8: The ambient source

**Files:**
- Create: `src/eve_ambient/sources/routines.py`
- Modify: `src/eve_ambient/sources/__init__.py`
- Test: `tests/test_ambient_sources_routines.py`

**Interfaces:**
- Consumes: `eve.routines.store.claim_due`, `schedule_next`, `record_run`, `expire` (Task 4); `eve.routines.cadence.next_after`, `describe` (Task 2); `eve_ambient.types.Signal`.
- Produces:
  - `poll(_member_sub: str) -> list[Signal]`
  - `record_outcome(signal: Signal, resolution: str) -> None`, called by Task 9's pipeline change.
  - `SOURCE_NAME = "routines"`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ambient_sources_routines.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from eve_ambient.sources import routines

DUE = {
    "id": "r-1",
    "member_sub": "sub-noah",
    "title": "Flights",
    "instruction": "Check Aeroplan fares YVR to SJD.",
    "cadence": {"daily_at": "08:00"},
    "timezone": "America/Vancouver",
    "status": "active",
    "scheduled_for": datetime(2026, 1, 11, 16, 0, tzinfo=UTC),
    "last_run_at": None,
    "expires_at": None,
    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
}


@pytest.fixture(autouse=True)
def _store(monkeypatch):
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[]))
    monkeypatch.setattr(routines.store, "schedule_next", AsyncMock())
    monkeypatch.setattr(routines.store, "record_run", AsyncMock())
    monkeypatch.setattr(routines.store, "expire", AsyncMock())


async def test_a_due_routine_becomes_a_signal_addressed_to_its_member(monkeypatch):
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))

    signals = await routines.poll("")

    assert len(signals) == 1
    signal = signals[0]
    assert signal.source == "routines"
    assert signal.member_sub == "sub-noah"
    assert signal.payload["routine_id"] == "r-1"
    assert signal.payload["instruction"] == DUE["instruction"]


async def test_the_key_carries_the_occurrence_not_just_the_routine(monkeypatch):
    """Two firings of the same routine are two signals. A key of just the
    routine id would make every run after the first look like a duplicate and
    be dropped by the cooldown check forever."""
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))

    signals = await routines.poll("")

    assert signals[0].key == "r-1:2026-01-11T16:00:00+00:00"


async def test_firing_schedules_the_next_occurrence(monkeypatch):
    schedule = AsyncMock()
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))
    monkeypatch.setattr(routines.store, "schedule_next", schedule)

    await routines.poll("")

    schedule.assert_awaited_once()
    routine_id, when = schedule.await_args.args
    assert routine_id == "r-1"
    assert when > datetime.now(UTC)


async def test_an_expired_routine_is_retired_instead_of_fired(monkeypatch):
    expire = AsyncMock()
    lapsed = {**DUE, "expires_at": datetime(2026, 1, 1, tzinfo=UTC)}
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[lapsed]))
    monkeypatch.setattr(routines.store, "expire", expire)

    signals = await routines.poll("")

    assert signals == []
    expire.assert_awaited_once_with("r-1")


async def test_one_bad_routine_does_not_lose_the_others(monkeypatch):
    """Same posture as every other source: a poll tick is best-effort per
    item, never all-or-nothing."""
    broken = {**DUE, "id": "r-bad", "cadence": {"nonsense": True}}
    monkeypatch.setattr(
        routines.store, "claim_due", AsyncMock(return_value=[broken, DUE])
    )

    signals = await routines.poll("")

    assert [s.payload["routine_id"] for s in signals] == ["r-1"]


async def test_a_sent_resolution_records_a_spoke_run(monkeypatch):
    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    signal = (await _one_signal(monkeypatch))

    await routines.record_outcome(signal, "sent")

    assert record.await_args.args[0] == "r-1"
    assert record.await_args.args[1] == "spoke"


async def test_a_vetoed_resolution_records_a_silent_run(monkeypatch):
    """Silence is the routine working correctly, so it must not count as a
    failure or the auto-pause would fire on every healthy routine."""
    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    signal = await _one_signal(monkeypatch)

    await routines.record_outcome(signal, "vetoed")

    assert record.await_args.args[1] == "silent"


async def test_a_deferred_resolution_records_an_error(monkeypatch):
    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    signal = await _one_signal(monkeypatch)

    await routines.record_outcome(signal, "deferred")

    assert record.await_args.args[1] == "error"


async def test_a_stale_resolution_records_nothing(monkeypatch):
    """`stale` means the cooldown dropped it before anything ran. Recording a
    run for it would be recording a run that never happened."""
    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    signal = await _one_signal(monkeypatch)

    await routines.record_outcome(signal, "stale")

    record.assert_not_awaited()


async def test_recording_an_outcome_for_another_source_is_a_no_op(monkeypatch):
    from eve_ambient.types import Signal

    record = AsyncMock()
    monkeypatch.setattr(routines.store, "record_run", record)
    other = Signal(
        source="calendar", key="c-1", occurred_at=datetime.now(UTC),
        member_sub="sub-noah", summary="x",
    )

    await routines.record_outcome(other, "sent")

    record.assert_not_awaited()


async def _one_signal(monkeypatch):
    monkeypatch.setattr(routines.store, "claim_due", AsyncMock(return_value=[DUE]))
    return (await routines.poll(""))[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ambient_sources_routines.py -v`
Expected: FAIL with `ImportError: cannot import name 'routines'`

- [ ] **Step 3: Write the implementation**

Create `src/eve_ambient/sources/routines.py`:

```python
"""Due routines as signals.

`per_member=False`, like `finances`, `computer` and `coding`: the query is
household-wide by construction and every claimed row carries its own member,
so polling once per member would issue one query per member to answer the
same question.

The relevance filter is bypassed for this source (see
`eve_ambient.pipeline._REQUESTED_SOURCES`), and so are quiet hours and the
daily cap. A routine is the most direct request in the system: the member did
not merely ask once, they asked for it to keep happening. An LLM deciding
that the answer to a standing request is "not relevant" and swallowing it is
the worst failure mode available, and a shared daily counter would let a
chatty calendar starve a routine the member deliberately created. The member
controls this spend by editing the routine, which they can see.

Quiet hours are bypassed for the same reason, with the schedule as the
mitigation: cadence is authored in the member's own timezone, so a member who
does not want a 3am notification schedules the routine for 8am.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from eve.routines import cadence as cadence_rules, store
from eve.settings import get_settings
from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

SOURCE_NAME = "routines"

# A routine's own cadence is the only thing that should decide when it runs
# again, so the pipeline's cooldown must never suppress a legitimate firing.
# The key already carries the scheduled instant, so two firings are never the
# same key; this bound exists only to make a retry of one occurrence idempotent.
COOLDOWN_HOURS = 1

# What a pipeline resolution means for the routine's own record. `stale`,
# `filtered` and anything else deliberately map to nothing: no run happened.
_OUTCOMES = {
    "sent": "spoke",
    "vetoed": "silent",
    "deferred": "error",
    "error": "error",
}


def _summary(routine: dict) -> str:
    return f"Routine due: {routine['title']} ({cadence_rules.describe(routine['cadence'])})."


async def poll(_member_sub: str) -> list[Signal]:
    """Claim every due routine and turn each into one signal.

    `store.claim_due` has already advanced `next_run_at` by the time this
    sees a row, which is what stops a compose turn still running on the next
    tick from firing the same occurrence twice. This function then computes
    and writes the real next time from the cadence.

    A routine past its expiry is retired here rather than fired: the tick
    that would have run it is the natural place to notice, and it costs no
    extra query.
    """
    now = datetime.now(UTC)
    try:
        claimed = await store.claim_due(now)
    except Exception:
        logger.warning("could not claim due routines this tick", exc_info=True)
        return []

    signals = []
    for routine in claimed:
        try:
            expires_at = routine.get("expires_at")
            if expires_at is not None and expires_at <= now:
                await store.expire(routine["id"])
                continue

            # Scheduled before the signal is emitted, and from `now` rather
            # than from the missed slot, so a service that was down overnight
            # fires once and moves on instead of delivering a backlog.
            await store.schedule_next(
                routine["id"],
                cadence_rules.next_after(routine["cadence"], routine["timezone"], now),
            )

            scheduled_for = routine["scheduled_for"]
            signals.append(
                Signal(
                    source=SOURCE_NAME,
                    key=f"{routine['id']}:{scheduled_for.isoformat()}",
                    occurred_at=scheduled_for,
                    member_sub=routine["member_sub"],
                    summary=_summary(routine),
                    payload={
                        "routine_id": routine["id"],
                        "title": routine["title"],
                        "instruction": routine["instruction"],
                        "cadence": routine["cadence"],
                        "created_at": routine["created_at"].isoformat(),
                        "last_run_at": (
                            routine["last_run_at"].isoformat()
                            if routine.get("last_run_at")
                            else None
                        ),
                    },
                    cooldown_hours=COOLDOWN_HOURS,
                )
            )
        except Exception:
            # One malformed routine must not cost every other member their
            # tick. The row keeps its advanced next_run_at, so a persistently
            # broken cadence is skipped rather than retried in a tight loop.
            logger.warning(
                "could not turn routine %s into a signal", routine.get("id"),
                exc_info=True,
            )
            continue
    return signals


async def record_outcome(signal: Signal, resolution: str) -> None:
    """Write this firing's result back to the routine.

    Called by `eve_ambient.pipeline.handle_signal` once the resolution is
    known. Only infrastructure failure counts against the auto-pause: a
    `vetoed` resolution means Eve looked and found nothing worth saying,
    which is the routine working.
    """
    if signal.source != SOURCE_NAME:
        return
    outcome = _OUTCOMES.get(resolution)
    if outcome is None:
        return
    routine_id = signal.payload.get("routine_id")
    if not routine_id:
        return
    try:
        await store.record_run(
            routine_id, outcome, get_settings().routine_failure_limit
        )
    except Exception:
        # The notification has already been delivered. Losing this row is
        # strictly better than letting it escape and re-resolve the signal.
        logger.warning(
            "could not record the run for routine %s", routine_id, exc_info=True
        )
```

- [ ] **Step 4: Register the source**

In `src/eve_ambient/sources/__init__.py`, add `routines` to the import line and add the entry to `SOURCES`:

```python
from eve_ambient.sources import calendar, coding, computer, finances, mail, routines
```

```python
    Source("routines", False, "routines", routines.poll),
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_ambient_sources_routines.py tests/test_ambient_sources.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/eve_ambient/sources/routines.py src/eve_ambient/sources/__init__.py tests/test_ambient_sources_routines.py
git commit -m "feat(routines): due routines become ambient signals"
```

---

## Task 9: Pipeline and prompt

**Files:**
- Modify: `src/eve_ambient/pipeline.py` (line 29 and the return paths)
- Modify: `src/eve_ambient/notify.py` (`compose_prompt`)
- Test: `tests/test_ambient_pipeline.py`, `tests/test_ambient_notify.py`

**Interfaces:**
- Consumes: `eve_ambient.sources.routines.record_outcome` from Task 8.
- Produces: routines treated as a requested source; the routine branch of `compose_prompt`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ambient_pipeline.py`:

```python
async def test_a_routine_signal_skips_the_relevance_filter(monkeypatch):
    """A standing request the member wrote is not a guess about what they
    might want to know."""
    from eve_ambient import pipeline

    async def unreachable(signal):
        raise AssertionError("a routine must not be filtered")

    monkeypatch.setattr(pipeline, "judge", unreachable)

    assert "routines" in pipeline._REQUESTED_SOURCES


async def test_a_routine_resolution_is_recorded_against_the_routine(monkeypatch):
    from datetime import UTC, datetime

    from eve_ambient import pipeline
    from eve_ambient.types import Signal

    recorded = {}

    async def fake_record(signal, resolution):
        recorded["key"] = signal.key
        recorded["resolution"] = resolution

    monkeypatch.setattr(pipeline.routines_source, "record_outcome", fake_record)
    monkeypatch.setattr(
        pipeline.store, "is_fresh", _async_return(False)
    )

    signal = Signal(
        source="routines", key="r-1:2026-01-11T16:00:00+00:00",
        occurred_at=datetime.now(UTC), member_sub="sub-noah",
        summary="Routine due: Flights.", payload={"routine_id": "r-1"},
    )

    await pipeline.handle_signal(signal)

    assert recorded["key"] == signal.key
    assert recorded["resolution"] == "stale"


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn
```

Append to `tests/test_ambient_notify.py`:

```python
def test_a_routine_prompt_carries_the_members_own_instruction():
    from datetime import UTC, datetime

    from eve.family import Member
    from eve_ambient.notify import compose_prompt
    from eve_ambient.types import FilterVerdict, Signal

    signal = Signal(
        source="routines",
        key="r-1:2026-01-11T16:00:00+00:00",
        occurred_at=datetime(2026, 1, 11, 16, 0, tzinfo=UTC),
        member_sub="sub-noah",
        summary="Routine due: Flights.",
        payload={
            "routine_id": "r-1",
            "title": "Flights",
            "instruction": "Check Aeroplan fares YVR to SJD in February.",
            "cadence": {"daily_at": "08:00"},
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_run_at": None,
        },
    )
    member = Member(
        sub="sub-noah", name="Noah", role="adult",
        timezone="America/Vancouver", permissions=["routines"],
    )
    verdict = FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="asked")

    prompt = compose_prompt(signal, member, verdict)

    assert "Check Aeroplan fares YVR to SJD in February." in prompt
    assert "NOTHING" in prompt
    # The member DID ask for this, so the ambient wording must not appear.
    assert "nobody asked you" not in prompt


def test_a_routine_prompt_is_still_ambient_marked():
    """The marker is what keeps may_author and turn_is_ambient closed against
    text replayed unattended and indefinitely. Dropping it to make routines
    feel more "real" would reopen both."""
    from datetime import UTC, datetime

    from eve.family import Member
    from eve.state import is_ambient_text
    from eve_ambient.notify import compose_prompt
    from eve_ambient.types import FilterVerdict, Signal

    signal = Signal(
        source="routines", key="r-1:x", occurred_at=datetime.now(UTC),
        member_sub="sub-noah", summary="Routine due.",
        payload={"routine_id": "r-1", "title": "T", "instruction": "Do it.",
                 "cadence": {"daily_at": "08:00"},
                 "created_at": "2026-01-01T00:00:00+00:00", "last_run_at": None},
    )
    member = Member(
        sub="sub-noah", name="Noah", role="adult",
        timezone="America/Vancouver", permissions=["routines"],
    )
    verdict = FilterVerdict(notify=True, audience=["sub-noah"], urgent=False, why="asked")

    assert is_ambient_text(compose_prompt(signal, member, verdict))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ambient_pipeline.py tests/test_ambient_notify.py -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Change the pipeline**

In `src/eve_ambient/pipeline.py`, add the import:

```python
from eve_ambient.sources import routines as routines_source
```

Change line 29:

```python
_REQUESTED_SOURCES = ("computer", "coding", "routines")
```

`_resolved` is the helper every return path goes through, so the routine bookkeeping hangs off it: a resolution that skipped it would be a firing the Routines screen never learns about. It is currently **synchronous**, with exactly **eight** call sites, all of the form `return _resolved(...)`, at lines 45, 70, 87, 92, 179, 184, 191 and 192 of `src/eve_ambient/pipeline.py`.

Make it `async` and add the record call as its first statement, leaving the existing logging body untouched:

```python
async def _resolved(
    signal: Signal, verdict: FilterVerdict | None, audience: list[str], outcome: str
) -> str:
    """One line per signal, whatever happened to it (design section 9).

    Also where a routine firing's own bookkeeping happens: every return path
    in `handle_signal` passes through here, so hanging it off this function
    is what makes it impossible for a new early return to forget it.
    """
    await routines_source.record_outcome(signal, outcome)
    ...  # existing body, entirely unchanged from `notify = verdict.notify ...`
```

Then change all eight call sites from `return _resolved(...)` to `return await _resolved(...)`. Verify none were missed:

```bash
grep -n "_resolved(" src/eve_ambient/pipeline.py
```

Expected: the definition plus eight `await _resolved(` call sites, and no bare `return _resolved(`.

- [ ] **Step 4: Change the compose prompt**

In `src/eve_ambient/notify.py`, add near the top:

```python
_ROUTINE_SOURCE = "routines"
```

Add to `compose_prompt`, as the first branch:

```python
def compose_prompt(signal: Signal, member: Member, verdict: FilterVerdict) -> str:
    """A marked human message, not a developer one: recall.py and extract.py
    both key off the last HumanMessage, so a developer message would silently
    cost this turn its episodic recall and half its extraction (design 6.2).
    The marker also tells Eve she was not spoken to, and leaves the thread
    showing what prompted her.
    """
    if signal.source == _ROUTINE_SOURCE:
        return _routine_prompt(signal, member)
    ...  # existing body, unchanged


def _routine_prompt(signal: Signal, member: Member) -> str:
    """A routine is not something Eve noticed; it is a standing request the
    member wrote. "You noticed this; nobody asked you" is exactly wrong here
    and would push her toward the veto on work she was explicitly asked to
    do.

    Still ambient-marked. The instruction is member-written, but it is
    replayed unattended and indefinitely, which is the property the marker
    exists to contain: `may_author` and `turn_is_ambient` both key off it.
    """
    payload = signal.payload
    instruction = str(payload.get("instruction") or "").strip()
    cadence = payload.get("cadence") or {}
    last_run = payload.get("last_run_at")
    since = (
        f"You last ran it on {last_run}."
        if last_run
        else "This is its first run."
    )
    return (
        f"{ambient_marker(member.name)}\n"
        f"{instruction}\n\n"
        f"This is a standing request {member.name} set up on "
        f"{payload.get('created_at')}, to run {cadence_rules.describe(cadence)}. "
        f"{since}\n"
        f"Carry it out now, then decide whether there is anything worth "
        f"telling {member.name}. If there is, say it in one or two sentences "
        f"in your own voice. If there is nothing worth saying, reply with "
        f"exactly {VETO} and nothing else."
    )
```

Add the import:

```python
from eve.routines import cadence as cadence_rules
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_ambient_pipeline.py tests/test_ambient_notify.py tests/test_ambient_sources_routines.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/eve_ambient/pipeline.py src/eve_ambient/notify.py tests/test_ambient_pipeline.py tests/test_ambient_notify.py
git commit -m "feat(routines): route firings through the requested-source path"
```

---

## Task 10: The combined HTTP app

`aegra.json`'s `http.app` takes exactly one app. This task introduces the app that holds both routers, with no behaviour change to the widget routes. Do this **before** the routines router exists so that any breakage is unambiguously this refactor's.

**Files:**
- Create: `src/eve/http_app.py`
- Modify: `aegra.json`
- Test: `tests/test_http_app.py`, plus the existing `tests/test_widgets_app.py` unchanged

**Interfaces:**
- Consumes: `eve.widgets.app.router`.
- Produces: `eve.http_app.app`, the FastAPI app `aegra.json` points at. Task 11 adds the routines router to it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_http_app.py`:

```python
"""One app, two routers. The widget routes must behave exactly as they did
when they were mounted directly."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from eve import http_app
    from eve.widgets import app as widgets_app

    http_app.app.dependency_overrides[widgets_app.require_auth] = lambda: None
    http_app.app.dependency_overrides[widgets_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": ["health"]}
    )
    yield TestClient(http_app.app)
    http_app.app.dependency_overrides.clear()


def test_the_widget_capabilities_route_is_still_mounted(client):
    response = client.get("/provider-resources/v1/capabilities")

    assert response.status_code == 200
    assert response.json()["protocol"] == "provider-resource/1.0"


def test_aegra_points_at_the_combined_app():
    """A refactor that leaves aegra.json behind silently serves the old app."""
    with open("aegra.json") as handle:
        config = json.load(handle)

    assert config["http"]["app"] == "./src/eve/http_app.py:app"


def test_the_conflict_flattener_still_applies(client, monkeypatch):
    """The 409-carries-a-snapshot behaviour lives on the app, not the router,
    so a new app must re-register it or every conflict changes shape."""
    from eve.widgets import app as widgets_app

    async def fake_get(member_sub, resource_id):
        return {"id": "res-1", "kind": "chart", "title": "A",
                "recipe": {"sources": [], "metric": {"op": "count"}},
                "filters": {}, "revision": 5}

    async def fake_update(*args, **kwargs):
        return None

    async def fake_snapshot(resource, member_sub, **kwargs):
        return {"resourceId": "res-1", "revision": 5, "view": {}, "sources": {}}

    monkeypatch.setattr(widgets_app.store, "get", fake_get)
    monkeypatch.setattr(widgets_app.store, "update_filters", fake_update)
    monkeypatch.setattr(widgets_app.resolve, "snapshot", fake_snapshot)

    response = client.post(
        "/provider-resources/v1/resources/res-1/actions",
        json={"type": "filters.replace", "input": {}, "expectedRevision": 1},
    )

    assert response.status_code == 409
    # Flattened: the snapshot IS the body, not nested under `detail`.
    assert response.json()["resourceId"] == "res-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_http_app.py -v`
Expected: FAIL with `ImportError: cannot import name 'http_app'`

- [ ] **Step 3: Write the implementation**

Create `src/eve/http_app.py`:

```python
"""The one custom HTTP app Aegra mounts.

`aegra.json`'s `http.app` takes a single app, and this deployment now serves
two resource APIs (widgets and routines). This module is that single app and
nothing else: it owns no routes of its own, so a route's auth boundary and
its handler stay in the same file as each other.

The exception handler is registered HERE rather than on either feature's
module, because it is an app-level concern: a 409 carries a whole snapshot
rather than a message, and returning it nested under `detail` would make a
client unwrap conflicts differently from every other response.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eve.widgets.app import router as widgets_router

app = FastAPI(title="eve-provider-resources")


@app.exception_handler(HTTPException)
async def _flatten(request: Request, exc: HTTPException) -> JSONResponse:
    if exc.status_code == 409 and isinstance(exc.detail, dict):
        return JSONResponse(status_code=409, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.include_router(widgets_router)
```

In `src/eve/widgets/app.py`, leave `app`, its own `include_router`, and its own exception handler in place. `tests/test_widgets_app.py` drives that app directly and must keep passing unchanged; the module is now both a standalone app for those tests and a router source for `http_app`.

In `aegra.json`, change the `http` block:

```json
  "http": {
    "app": "./src/eve/http_app.py:app",
    "enable_custom_route_auth": false
  }
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_http_app.py tests/test_widgets_app.py -v
```

Expected: PASS, both files. `test_widgets_app.py` must be **unmodified**.

- [ ] **Step 5: Verify against a real server**

```bash
docker compose -f docker-compose.test.yml up -d
uv run eve-migrate
uv run aegra dev
```

In another shell:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:2026/provider-resources/v1/capabilities
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:2026/health
```

Expected: `401` for the first (the route exists and is guarded), and the health probe's normal unauthenticated success for the second. A `404` on the first means the app is not mounted.

- [ ] **Step 6: Commit**

```bash
git add src/eve/http_app.py aegra.json tests/test_http_app.py
git commit -m "refactor(http): one app holding every provider-resource router"
```

---

## Task 11: The routines resource API

**Files:**
- Create: `src/eve/routines/app.py`
- Modify: `src/eve/http_app.py`
- Test: `tests/test_routines_app.py`

**Interfaces:**
- Consumes: `eve.routines.store` (Task 4), `eve.routines.cadence` (Task 2), `eve.http_app` (Task 10).
- Produces: `router`, `PREFIX = "/provider-resources/v1/routines"`, `PROTOCOL = "provider-routine/1.0"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_routines_app.py`:

```python
"""Authentication is Aegra's; ownership is ours. Deliberately no POST: a
routine is authored in conversation, where Eve can push back on a vague
instruction, never from the screen."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

ROW = {
    "id": "r-1",
    "member_sub": "sub-noah",
    "title": "Flights",
    "instruction": "Check fares.",
    "cadence": {"daily_at": "08:00"},
    "timezone": "America/Vancouver",
    "status": "active",
    "next_run_at": datetime(2026, 1, 11, 16, 0, tzinfo=UTC),
    "last_run_at": None,
    "last_outcome": None,
    "consecutive_failures": 0,
    "expires_at": None,
    "revision": 1,
    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
}


@pytest.fixture
def client():
    from eve import http_app
    from eve.routines import app as routines_app

    http_app.app.dependency_overrides[routines_app.require_auth] = lambda: None
    http_app.app.dependency_overrides[routines_app.current_member] = (
        lambda: {"sub": "sub-noah", "permissions": ["routines"]}
    )
    yield TestClient(http_app.app)
    http_app.app.dependency_overrides.clear()


def test_capabilities_names_the_protocol_and_the_cadence_vocabulary(client):
    response = client.get("/provider-resources/v1/routines/capabilities")

    assert response.status_code == 200
    body = response.json()
    assert body["protocol"] == "provider-routine/1.0"
    assert set(body["cadenceKinds"]) == {"every_hours", "daily_at", "weekly_at"}
    assert body["limits"]["minEveryHours"] == 1


def test_there_is_no_create_route(client):
    """Authoring happens in conversation. A POST here would let a client
    write an instruction Eve never got to question."""
    response = client.post("/provider-resources/v1/routines", json={})

    assert response.status_code in (404, 405)


def test_listing_returns_only_this_members_routines(client, monkeypatch):
    from eve.routines import app as routines_app

    seen = {}

    async def fake_list_for(member_sub):
        seen["member_sub"] = member_sub
        return [ROW]

    monkeypatch.setattr(routines_app.store, "list_for", fake_list_for)

    response = client.get("/provider-resources/v1/routines")

    assert response.status_code == 200
    assert seen["member_sub"] == "sub-noah"
    assert response.json()["routines"][0]["routineId"] == "r-1"


def test_a_foreign_routine_is_404_not_403(client, monkeypatch):
    from eve.routines import app as routines_app

    async def fake_update(*args, **kwargs):
        return None

    async def fake_get(member_sub, routine_id):
        return None

    monkeypatch.setattr(routines_app.store, "update", fake_update)
    monkeypatch.setattr(routines_app.store, "get", fake_get)

    response = client.patch(
        "/provider-resources/v1/routines/r-x",
        json={"status": "paused", "expectedRevision": 1},
    )

    assert response.status_code == 404


def test_pausing_is_accepted(client, monkeypatch):
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen.update(fields)
        return {**ROW, "status": "paused", "revision": 2}

    monkeypatch.setattr(routines_app.store, "update", fake_update)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"status": "paused", "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert seen["status"] == "paused"


def test_expired_cannot_be_assigned_by_a_client(client, monkeypatch):
    """`expired` is the server's to assign, from the expiry it was given."""
    from eve.routines import app as routines_app

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not write a client-supplied expired status")

    monkeypatch.setattr(routines_app.store, "update", unreachable)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"status": "expired", "expectedRevision": 1},
    )

    assert response.status_code == 400


def test_an_invalid_cadence_is_refused(client, monkeypatch):
    from eve.routines import app as routines_app

    async def unreachable(*args, **kwargs):
        raise AssertionError("must not store an invalid cadence")

    monkeypatch.setattr(routines_app.store, "update", unreachable)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"cadence": {"every_hours": 0}, "expectedRevision": 1},
    )

    assert response.status_code == 400


def test_a_cadence_change_recomputes_the_next_run(client, monkeypatch):
    """Computed server-side: a client that sent its own next_run_at could
    schedule a routine to fire immediately and repeatedly."""
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen.update(fields)
        return {**ROW, "revision": 2}

    monkeypatch.setattr(routines_app.store, "get", _async_return(ROW))
    monkeypatch.setattr(routines_app.store, "update", fake_update)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"cadence": {"every_hours": 6}, "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert "next_run_at" in seen


def test_a_client_supplied_next_run_at_is_dropped(client, monkeypatch):
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen.update(fields)
        return {**ROW, "revision": 2}

    monkeypatch.setattr(routines_app.store, "update", fake_update)

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"title": "New", "next_run_at": "2026-01-01T00:00:00+00:00",
              "expectedRevision": 1},
    )

    assert response.status_code == 200
    assert "next_run_at" not in seen


def test_a_member_sub_in_the_body_is_ignored(client, monkeypatch):
    """A resource id is a locator, and a body field is never an identity."""
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen["member_sub"] = member_sub
        seen.update(fields)
        return {**ROW, "revision": 2}

    monkeypatch.setattr(routines_app.store, "update", fake_update)

    client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"title": "New", "member_sub": "sub-kendra", "expectedRevision": 1},
    )

    assert seen["member_sub"] == "sub-noah"


def test_a_stale_revision_is_409_carrying_the_current_routine(client, monkeypatch):
    from eve.routines import app as routines_app

    async def fake_update(*args, **kwargs):
        return None

    monkeypatch.setattr(routines_app.store, "update", fake_update)
    monkeypatch.setattr(routines_app.store, "get", _async_return({**ROW, "revision": 7}))

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"title": "New", "expectedRevision": 1},
    )

    assert response.status_code == 409
    assert response.json()["revision"] == 7


def test_resuming_a_paused_routine_clears_its_failures(client, monkeypatch):
    """The member resuming it is the acknowledgement the counter waited for."""
    from eve.routines import app as routines_app

    seen = {}

    async def fake_update(member_sub, routine_id, expected_revision, **fields):
        seen.update(fields)
        return {**ROW, "revision": 2}

    monkeypatch.setattr(routines_app.store, "update", fake_update)
    monkeypatch.setattr(routines_app.store, "clear_failures", _async_noop())

    response = client.patch(
        "/provider-resources/v1/routines/r-1",
        json={"status": "active", "expectedRevision": 1},
    )

    assert response.status_code == 200


def test_deleting_a_missing_routine_is_404(client, monkeypatch):
    from eve.routines import app as routines_app

    async def fake_delete(member_sub, routine_id):
        return False

    monkeypatch.setattr(routines_app.store, "delete", fake_delete)

    response = client.delete("/provider-resources/v1/routines/r-x")

    assert response.status_code == 404


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


def _async_noop():
    async def _fn(*args, **kwargs):
        return None

    return _fn
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routines_app.py -v`
Expected: FAIL with `ImportError: cannot import name 'app' from 'eve.routines'`

- [ ] **Step 3: Add `clear_failures` to the store**

Append to `src/eve/routines/store.py`:

```python
async def clear_failures(member_sub: str, routine_id: str) -> None:
    """Resuming a paused routine is the member acknowledging the failures the
    counter was holding, so it starts clean rather than pausing again on the
    next single error."""
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_routine SET consecutive_failures = 0, updated_at = now()"
            " WHERE id = %s AND member_sub = %s",
            (routine_id, member_sub),
        )
```

- [ ] **Step 4: Write the router**

Create `src/eve/routines/app.py`:

```python
"""The routines resource API: read and manage standing requests without a
model call.

Mounted through `eve.http_app`. The auth boundary is declared on this router
as `Depends(require_auth)` rather than relying on Aegra's
`enable_custom_route_auth` walk, which is a no-op in aegra-api 0.10.3 (it
rewrites `route.dependencies` after the routes are built, but FastAPI
resolves dependencies from the dependant constructed at route-creation time).
Same reasoning, same shape as `eve.widgets.app`.

There is deliberately NO create route. Writing an instruction Eve will act on
unattended belongs in conversation, where she can ask what "a good deal"
means before it is committed to a table. This surface manages what exists.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aegra_api.core.auth_deps import require_auth

from eve.routines import cadence as cadence_rules, store

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

PREFIX = "/provider-resources/v1/routines"
PROTOCOL = "provider-routine/1.0"

# What a client may assign. `expired` is absent deliberately: it is the
# server's conclusion from the expiry it was given, not a state to be claimed.
_ASSIGNABLE_STATUS = ("active", "paused")


class PatchRequest(BaseModel):
    title: str | None = None
    cadence: dict | None = None
    status: str | None = None
    expires_at: str | None = None
    expectedRevision: int

    # No `member_sub` and no `next_run_at` field, deliberately: pydantic drops
    # unknown keys, so a body carrying either is ignored rather than trusted.
    # The owner comes from the authenticated principal and the schedule is
    # computed from the cadence.


def current_member(request: Request) -> dict:
    """The authenticated principal, as the router's `require_auth` left it.

    Overridden in tests. In production the explicit `Depends(require_auth)`
    has already rejected an unauthenticated request; the guard here is for a
    misconfiguration, where failing closed is the only safe answer.
    """
    user = request.scope.get("user")
    if user is None or not getattr(user, "identity", None):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {
        "sub": user.identity,
        "permissions": list(getattr(user, "permissions", []) or []),
    }


def _public(row: dict) -> dict:
    """The wire shape. `member_sub` is never echoed: the caller is the owner
    by construction, so it carries no information and inviting a client to
    read it invites one to send it."""
    return {
        "routineId": row["id"],
        "title": row["title"],
        "instruction": row["instruction"],
        "cadence": row["cadence"],
        "status": row["status"],
        "nextRunAt": row["next_run_at"].isoformat() if row.get("next_run_at") else None,
        "lastRunAt": row["last_run_at"].isoformat() if row.get("last_run_at") else None,
        "lastOutcome": row.get("last_outcome"),
        "consecutiveFailures": row.get("consecutive_failures", 0),
        "expiresAt": row["expires_at"].isoformat() if row.get("expires_at") else None,
        "revision": row["revision"],
    }


@router.get(f"{PREFIX}/capabilities")
async def capabilities(member: dict = Depends(current_member)) -> dict:
    """What this deployment supports. A client that 404s here concludes the
    provider has no routine support at all; a transport failure means
    temporarily unavailable, which is a different thing entirely and must not
    hide the destination."""
    return {
        "protocol": PROTOCOL,
        "cadenceKinds": ["every_hours", "daily_at", "weekly_at"],
        "statuses": list(_ASSIGNABLE_STATUS),
        "limits": {
            "minEveryHours": cadence_rules.MIN_EVERY_HOURS,
            "maxEveryHours": cadence_rules.MAX_EVERY_HOURS,
        },
        # Authoring is conversational; the client must not offer a create form.
        "canCreate": False,
    }


@router.get(PREFIX)
async def list_routines(member: dict = Depends(current_member)) -> dict:
    rows = await store.list_for(member["sub"])
    return {"routines": [_public(row) for row in rows]}


@router.patch(f"{PREFIX}/{{routine_id}}")
async def patch_routine(
    routine_id: str,
    body: PatchRequest,
    member: dict = Depends(current_member),
) -> dict:
    fields: dict = {}

    if body.title is not None:
        fields["title"] = body.title

    if body.status is not None:
        if body.status not in _ASSIGNABLE_STATUS:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of: {', '.join(_ASSIGNABLE_STATUS)}",
            )
        fields["status"] = body.status

    if body.expires_at is not None:
        try:
            expiry = datetime.fromisoformat(body.expires_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="expires_at must be ISO-8601")
        fields["expires_at"] = (
            expiry if expiry.tzinfo else expiry.replace(tzinfo=UTC)
        )

    if body.cadence is not None:
        error = cadence_rules.validate(body.cadence)
        if error is not None:
            raise HTTPException(status_code=400, detail=f"invalid cadence: {error}")
        fields["cadence"] = body.cadence
        # Recomputed here, never accepted from the client: a caller that
        # supplied its own next_run_at could schedule a routine to fire
        # immediately, and then again on every tick.
        existing = await store.get(member["sub"], routine_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="not found")
        fields["next_run_at"] = cadence_rules.next_after(
            body.cadence, existing["timezone"], datetime.now(UTC)
        )

    if not fields:
        raise HTTPException(status_code=400, detail="nothing to change")

    updated = await store.update(
        member["sub"], routine_id, body.expectedRevision, **fields
    )
    if updated is None:
        # Absent, foreign, or stale. A missing row is a 404 that reveals
        # nothing; a real row at another revision is a 409 carrying the truth.
        current = await store.get(member["sub"], routine_id)
        if current is None:
            raise HTTPException(status_code=404, detail="not found")
        raise HTTPException(status_code=409, detail=_public(current))

    if fields.get("status") == "active":
        # Resuming is the acknowledgement the failure counter waited for.
        await store.clear_failures(member["sub"], routine_id)

    return _public(updated)


@router.delete(f"{PREFIX}/{{routine_id}}", status_code=204)
async def delete_routine(
    routine_id: str, member: dict = Depends(current_member)
) -> None:
    if not await store.delete(member["sub"], routine_id):
        raise HTTPException(status_code=404, detail="not found")
```

In `src/eve/http_app.py`, add the router:

```python
from eve.routines.app import router as routines_router
```

```python
app.include_router(routines_router)
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_routines_app.py tests/test_http_app.py tests/test_widgets_app.py -v
```

Expected: PASS, all three files.

- [ ] **Step 6: Commit**

```bash
git add src/eve/routines/app.py src/eve/routines/store.py src/eve/http_app.py tests/test_routines_app.py
git commit -m "feat(routines): the routines resource API"
```

---

## Task 12: End-to-end integration test

**Files:**
- Create: `tests/test_routines_integration.py`

**Interfaces:**
- Consumes: everything from Tasks 1 through 11.

- [ ] **Step 1: Write the test**

Create `tests/test_routines_integration.py`:

```python
"""One routine, from creation to a firing that speaks and a firing that does
not. Exercises the real store against the real schema."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _clean():
    from eve.memory.db import get_pool

    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM eve_routine")
    yield


async def test_a_routine_fires_once_then_waits_for_its_next_slot():
    from eve.routines import store
    from eve_ambient.sources import routines

    await store.create(
        member_sub="sub-noah",
        title="Flights",
        instruction="Check Aeroplan fares YVR to SJD.",
        cadence={"every_hours": 6},
        timezone="America/Vancouver",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=None,
    )

    first = await routines.poll("")
    second = await routines.poll("")

    assert len(first) == 1
    assert second == []

    row = (await store.list_for("sub-noah"))[0]
    assert row["next_run_at"] > datetime.now(UTC)


async def test_a_silent_firing_keeps_the_routine_healthy():
    from eve.routines import store
    from eve_ambient.sources import routines

    await store.create(
        member_sub="sub-noah", title="Flights", instruction="Check fares.",
        cadence={"every_hours": 6}, timezone="America/Vancouver",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1), expires_at=None,
    )
    signal = (await routines.poll(""))[0]

    await routines.record_outcome(signal, "vetoed")

    row = (await store.list_for("sub-noah"))[0]
    assert row["last_outcome"] == "silent"
    assert row["consecutive_failures"] == 0
    assert row["status"] == "active"


async def test_repeated_infrastructure_failure_pauses_the_routine():
    from eve.routines import store
    from eve.settings import get_settings
    from eve_ambient.sources import routines

    await store.create(
        member_sub="sub-noah", title="Flights", instruction="Check fares.",
        cadence={"every_hours": 1}, timezone="America/Vancouver",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1), expires_at=None,
    )

    for _ in range(get_settings().routine_failure_limit):
        row = (await store.list_for("sub-noah"))[0]
        await store.schedule_next(row["id"], datetime.now(UTC) - timedelta(minutes=1))
        signal = (await routines.poll(""))[0]
        await routines.record_outcome(signal, "deferred")

    row = (await store.list_for("sub-noah"))[0]
    assert row["status"] == "paused"

    # A paused routine is out of the due query even when its time has passed.
    await store.schedule_next(row["id"], datetime.now(UTC) - timedelta(minutes=1))
    assert await routines.poll("") == []


async def test_an_expired_routine_retires_instead_of_firing():
    from eve.routines import store
    from eve_ambient.sources import routines

    await store.create(
        member_sub="sub-noah", title="February flights", instruction="Check fares.",
        cadence={"every_hours": 6}, timezone="America/Vancouver",
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )

    assert await routines.poll("") == []
    assert (await store.list_for("sub-noah"))[0]["status"] == "expired"
```

- [ ] **Step 2: Run the test**

```bash
docker compose -f docker-compose.test.yml up -d
uv run eve-migrate
uv run pytest tests/test_routines_integration.py -m integration -v
```

Expected: PASS.

- [ ] **Step 3: Run the whole unit suite**

Run: `uv run pytest`
Expected: PASS, nothing regressed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_routines_integration.py
git commit -m "test(routines): end-to-end firing, silence, failure and expiry"
```

---

## Task 13: Server documentation

**Files:**
- Modify: `docs/architecture.md`

- [ ] **Step 1: Add the Routines section**

Add a `## Routines` section after `## Widget resources` in `docs/architecture.md`, covering:

- A routine is a stored prompt plus a cadence; each firing is an ordinary headless Eve turn, and silence is a successful outcome.
- The firing path is `eve_ambient.sources.routines`, a `per_member=False` polled source on the existing tick, so the gate chain, thread creation, the veto and the push are all reused.
- `routines` joins `computer` and `coding` in `_REQUESTED_SOURCES`, which bypasses the REFLEX filter, quiet hours, and the daily cap; the per-routine schedule in the member's own timezone is the quiet-hours control.
- `claim_due` advances `next_run_at` in the statement that selects it, which is what makes a double fire unreachable; a routine that is far overdue fires once and schedules forward, never a backlog.
- The cadence vocabulary is closed and validated in `eve.routines.cadence`, with a one-hour floor.
- The three tools refuse on an ambient turn via `eve.state.turn_is_ambient`, so a routine cannot author, pause, or cancel a routine. Note that `configurable["is_ambient"]` is NOT the mechanism and why (EVE-30).
- `EVE_ROUTINES_ENABLED` is off by default.
- `aegra.json`'s `http.app` now points at `src/eve/http_app.py`, which holds both the widget and routine routers.

Update these existing places in the same file:

- the module map, adding `src/eve/routines/` and `src/eve/http_app.py`
- the ambient source list, adding `routines`
- the migration list, adding `0011_eve_routine`
- the `aegra.json` description, which currently names the widgets app

- [ ] **Step 2: Verify the cross-references resolve**

```bash
grep -n "0011_eve_routine\|http_app\|routines" docs/architecture.md | head -20
ls src/eve/routines/ src/eve/http_app.py alembic/versions/0011_eve_routine.py
```

Expected: every path named in the docs exists.

- [ ] **Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: routines in the architecture document"
```

---

## Task 14: Client domain model and service interface

**Repository: `open-assistant`.** From here on, work in `~/GitHub/open-assistant/flutter-open-assistant`.

**Files:**
- Create: `lib/domain/models/routines/routine.dart`, `lib/data/services/agent/routine_service.dart`
- Test: `test/domain/models/routines/routine_test.dart`

**Interfaces:**
- Consumes: the wire shape from Task 11 (`routineId`, `title`, `instruction`, `cadence`, `status`, `nextRunAt`, `lastRunAt`, `lastOutcome`, `consecutiveFailures`, `expiresAt`, `revision`).
- Produces: `Routine`, `RoutineCadence`, `RoutineStatus`, `RoutineService`, `RoutineUnsupported`, `RoutineConflict`.

- [ ] **Step 1: Write the failing tests**

Create `test/domain/models/routines/routine_test.dart`:

```dart
import 'package:assistant/domain/models/routines/routine.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('RoutineCadence', () {
    test('describes an hourly cadence', () {
      expect(
        RoutineCadence.fromJson({'every_hours': 1}).describe(),
        'Every hour',
      );
    });

    test('describes a multi-hour cadence', () {
      expect(
        RoutineCadence.fromJson({'every_hours': 6}).describe(),
        'Every 6 hours',
      );
    });

    test('describes a daily cadence', () {
      expect(
        RoutineCadence.fromJson({'daily_at': '08:00'}).describe(),
        'Every day at 08:00',
      );
    });

    test('describes a weekly cadence', () {
      expect(
        RoutineCadence.fromJson({
          'weekly_at': {'day': 'sunday', 'time': '19:00'},
        }).describe(),
        'Every Sunday at 19:00',
      );
    });

    test('an unrecognised shape degrades to a neutral phrase', () {
      // A newer server may grow a shape this build has never heard of. The
      // routine must still be listable and pausable.
      expect(RoutineCadence.fromJson({'lunar': true}).describe(), 'On a schedule');
    });

    test('round-trips through json', () {
      const source = {'daily_at': '08:00'};
      expect(RoutineCadence.fromJson(source).toJson(), source);
    });
  });

  group('Routine', () {
    Map<String, Object?> json() => {
          'routineId': 'r-1',
          'title': 'Flights',
          'instruction': 'Check fares.',
          'cadence': {'daily_at': '08:00'},
          'status': 'active',
          'nextRunAt': '2026-01-11T16:00:00Z',
          'lastRunAt': null,
          'lastOutcome': null,
          'consecutiveFailures': 0,
          'expiresAt': null,
          'revision': 1,
        };

    test('parses a well-formed row', () {
      final routine = Routine.fromJson(json());

      expect(routine, isNotNull);
      expect(routine!.id, 'r-1');
      expect(routine.status, RoutineStatus.active);
      expect(routine.nextRunAt, isNotNull);
    });

    test('a row missing its id is rejected rather than half-built', () {
      final broken = json()..remove('routineId');

      expect(Routine.fromJson(broken), isNull);
    });

    test('an unknown status degrades to paused rather than throwing', () {
      // Fail safe: an unrecognised state must never render as "running".
      final row = json()..['status'] = 'hibernating';

      expect(Routine.fromJson(row)!.status, RoutineStatus.paused);
    });

    test('knows when it auto-paused', () {
      final row = json()
        ..['status'] = 'paused'
        ..['consecutiveFailures'] = 5;

      expect(Routine.fromJson(row)!.pausedByFailure, isTrue);
    });

    test('a manually paused routine is not a failed one', () {
      final row = json()..['status'] = 'paused';

      expect(Routine.fromJson(row)!.pausedByFailure, isFalse);
    });
  });
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `flutter test test/domain/models/routines/routine_test.dart`
Expected: FAIL, the target of URI doesn't exist.

- [ ] **Step 3: Write the model**

Create `lib/domain/models/routines/routine.dart`:

```dart
/// A standing request the agent carries out on a schedule.
///
/// Provider-agnostic: nothing here knows which agent framework produced the
/// row. A provider that does not implement routines simply has no service,
/// and the destination is hidden.
enum RoutineStatus { active, paused, expired }

/// The closed cadence vocabulary.
///
/// Rendered client-side from the structured object rather than from a
/// server-sent string, so the prose stays localizable and an editor can only
/// produce shapes the server will accept.
class RoutineCadence {
  const RoutineCadence(this.raw);

  factory RoutineCadence.fromJson(Map<String, Object?> json) =>
      RoutineCadence(Map<String, Object?>.from(json));

  final Map<String, Object?> raw;

  Map<String, Object?> toJson() => raw;

  static const _days = {
    'monday': 'Monday',
    'tuesday': 'Tuesday',
    'wednesday': 'Wednesday',
    'thursday': 'Thursday',
    'friday': 'Friday',
    'saturday': 'Saturday',
    'sunday': 'Sunday',
  };

  /// Plain English. An unrecognised shape degrades to a neutral phrase rather
  /// than throwing: a newer server may grow a cadence this build predates,
  /// and the member must still be able to see and pause that routine.
  String describe() {
    final hours = raw['every_hours'];
    if (hours is int) {
      return hours == 1 ? 'Every hour' : 'Every $hours hours';
    }

    final daily = raw['daily_at'];
    if (daily is String) return 'Every day at $daily';

    final weekly = raw['weekly_at'];
    if (weekly is Map) {
      final day = _days[weekly['day']?.toString().toLowerCase()];
      final time = weekly['time'];
      if (day != null && time is String) return 'Every $day at $time';
    }

    return 'On a schedule';
  }
}

class Routine {
  const Routine({
    required this.id,
    required this.title,
    required this.instruction,
    required this.cadence,
    required this.status,
    required this.revision,
    required this.consecutiveFailures,
    this.nextRunAt,
    this.lastRunAt,
    this.lastOutcome,
    this.expiresAt,
  });

  /// Null when the row is unusable. Defensive by design: one malformed row
  /// must not take down the whole list.
  static Routine? fromJson(Map<String, Object?> json) {
    final id = json['routineId'];
    final title = json['title'];
    if (id is! String || title is! String) return null;

    final cadence = json['cadence'];

    return Routine(
      id: id,
      title: title,
      instruction: json['instruction'] is String
          ? json['instruction'] as String
          : '',
      cadence: RoutineCadence.fromJson(
        cadence is Map ? Map<String, Object?>.from(cadence) : const {},
      ),
      status: _status(json['status']),
      revision: json['revision'] is int ? json['revision'] as int : 0,
      consecutiveFailures: json['consecutiveFailures'] is int
          ? json['consecutiveFailures'] as int
          : 0,
      nextRunAt: _time(json['nextRunAt']),
      lastRunAt: _time(json['lastRunAt']),
      lastOutcome: json['lastOutcome'] is String
          ? json['lastOutcome'] as String
          : null,
      expiresAt: _time(json['expiresAt']),
    );
  }

  final String id;
  final String title;
  final String instruction;
  final RoutineCadence cadence;
  final RoutineStatus status;
  final int revision;
  final int consecutiveFailures;
  final DateTime? nextRunAt;
  final DateTime? lastRunAt;
  final String? lastOutcome;
  final DateTime? expiresAt;

  bool get isActive => status == RoutineStatus.active;

  /// Paused by the server after repeated failures, rather than by the member.
  /// Worth saying out loud on the card: the member did not do this and needs
  /// to know why it stopped.
  bool get pausedByFailure =>
      status == RoutineStatus.paused && consecutiveFailures > 0;

  /// An unknown status reads as paused, never as active: rendering an
  /// unrecognised state as running would claim work is happening that may
  /// not be.
  static RoutineStatus _status(Object? raw) => switch (raw) {
        'active' => RoutineStatus.active,
        'expired' => RoutineStatus.expired,
        _ => RoutineStatus.paused,
      };

  static DateTime? _time(Object? raw) =>
      raw is String ? DateTime.tryParse(raw)?.toLocal() : null;
}
```

- [ ] **Step 4: Write the service interface**

Create `lib/data/services/agent/routine_service.dart`:

```dart
import 'package:assistant/domain/models/routines/routine.dart';

/// The provider does not implement routines at all.
///
/// Distinct from every transport failure on purpose: this hides the Routines
/// destination, and a network hiccup must never do that.
class RoutineUnsupported implements Exception {
  const RoutineUnsupported();
}

/// Somebody else moved first. Carries the provider's current row so the caller
/// can show fresh data instead of an error the member cannot act on.
class RoutineConflict implements Exception {
  const RoutineConflict(this.current);

  final Routine current;
}

/// What the provider supports. `canCreate` is false against every provider
/// that authors routines conversationally, which is why this screen has no
/// create affordance.
class RoutineCapabilities {
  const RoutineCapabilities({
    required this.cadenceKinds,
    required this.canCreate,
  });

  factory RoutineCapabilities.fromJson(Map<String, Object?> json) =>
      RoutineCapabilities(
        cadenceKinds: json['cadenceKinds'] is List
            ? List<String>.from(
                (json['cadenceKinds'] as List).whereType<String>(),
              )
            : const [],
        canCreate: json['canCreate'] == true,
      );

  final List<String> cadenceKinds;
  final bool canCreate;
}

/// The optional routine capability of an agent provider.
///
/// Deliberately not folded into `AgentService`: managing a routine is a plain
/// resource call that must not pass through the run pipeline, the turn
/// reducer, or the event stream. A provider that cannot do this returns null
/// from `AgentService.routines` and the UI hides the destination.
///
/// There is no `create`. A routine is authored in conversation, where the
/// agent can question a vague instruction before it is committed.
abstract class RoutineService {
  /// Throws [RoutineUnsupported] when the provider has no routine API.
  Future<RoutineCapabilities?> capabilities();

  Future<List<Routine>> list();

  /// Throws [RoutineConflict] when [expectedRevision] is stale.
  Future<Routine> update(
    String id, {
    required int expectedRevision,
    String? title,
    RoutineCadence? cadence,
    RoutineStatus? status,
  });

  Future<void> delete(String id);
}
```

- [ ] **Step 5: Run the tests**

```bash
flutter test test/domain/models/routines/routine_test.dart
flutter analyze lib/domain/models/routines lib/data/services/agent/routine_service.dart
```

Expected: tests PASS, analyzer clean.

- [ ] **Step 6: Commit**

```bash
git add lib/domain/models/routines/routine.dart lib/data/services/agent/routine_service.dart test/domain/models/routines/routine_test.dart
git commit -m "feat(routines): domain model and provider-agnostic service interface"
```

---

## Task 15: The LangGraph routine service

**Files:**
- Create: `lib/data/services/agent/langgraph_routine_service.dart`
- Test: `test/data/services/agent/langgraph_routine_service_test.dart`

**Interfaces:**
- Consumes: `LangGraphClient`, the `RoutineService` interface from Task 14.
- Produces: `LangGraphRoutineService`, consumed by Task 16's repository wiring.

Read `lib/data/services/agent/langgraph_widget_service.dart` first: this class mirrors it exactly, including how it maps a 404 to unsupported and a 409 to a conflict.

- [ ] **Step 1: Write the failing tests**

Create `test/data/services/agent/langgraph_routine_service_test.dart`. Mirror the existing widget-service test's fake-client setup (read `test/data/services/agent/langgraph_widget_service_test.dart` for the established shape) and cover:

```dart
// The five behaviours that matter, in this order:
//
// 1. capabilities() on a 404 throws RoutineUnsupported (hides the menu item)
// 2. capabilities() on a 500 throws something that is NOT RoutineUnsupported
//    (a server error must never hide the destination)
// 3. list() drops a malformed row and keeps the good ones
// 4. update() on a 409 throws RoutineConflict carrying the parsed current row
// 5. update() sends expectedRevision and never sends a member field
```

Write each as a real test with a fake `LangGraphClient`, following the widget test's structure.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `flutter test test/data/services/agent/langgraph_routine_service_test.dart`
Expected: FAIL, the target of URI doesn't exist.

- [ ] **Step 3: Write the implementation**

Create `lib/data/services/agent/langgraph_routine_service.dart`:

```dart
import 'package:assistant/core/logging.dart';
import 'package:assistant/data/services/agent/langgraph_client.dart';
import 'package:assistant/data/services/agent/routine_service.dart';
import 'package:assistant/domain/models/routines/routine.dart';

/// A LangGraph provider's routine API, reached over the same base URL and
/// bearer the agent already uses.
///
/// These are ordinary authenticated resource routes mounted beside the Agent
/// Protocol endpoints, not graph runs: listing or pausing a routine never
/// creates a thread, never appends a turn, and never spends a model call.
class LangGraphRoutineService implements RoutineService {
  LangGraphRoutineService({required LangGraphClient client}) : _client = client;

  static const _prefix = '/provider-resources/v1/routines';

  final LangGraphClient _client;

  @override
  Future<RoutineCapabilities?> capabilities() async {
    final body = await _get('$_prefix/capabilities');
    return body is Map<String, Object?>
        ? RoutineCapabilities.fromJson(body)
        : null;
  }

  @override
  Future<List<Routine>> list() async {
    final body = await _get(_prefix);
    final rows = body is Map ? body['routines'] : null;
    if (rows is! List) return const [];

    return [
      for (final row in rows)
        if (row is Map)
          if (Routine.fromJson(Map<String, Object?>.from(row))
              case final routine?)
            routine,
    ];
  }

  @override
  Future<Routine> update(
    String id, {
    required int expectedRevision,
    String? title,
    RoutineCadence? cadence,
    RoutineStatus? status,
  }) async {
    final payload = <String, Object?>{'expectedRevision': expectedRevision};
    if (title != null) payload['title'] = title;
    if (cadence != null) payload['cadence'] = cadence.toJson();
    if (status != null) payload['status'] = status.name;

    try {
      final body = await _client.patchJson(
        '$_prefix/${Uri.encodeComponent(id)}',
        payload,
      );
      final routine = body is Map<String, Object?>
          ? Routine.fromJson(body)
          : null;
      if (routine == null) {
        throw Exception('The provider returned an unusable routine.');
      }
      return routine;
    } on LangGraphException catch (error) {
      if (error.statusCode == 409) {
        // The body is the provider's current row, not a message, so the
        // caller can adopt the truth instead of showing an error.
        final current = error.body is Map<String, Object?>
            ? Routine.fromJson(error.body as Map<String, Object?>)
            : null;
        if (current != null) throw RoutineConflict(current);
      }
      rethrow;
    }
  }

  @override
  Future<void> delete(String id) =>
      _client.delete('$_prefix/${Uri.encodeComponent(id)}');

  Future<Object?> _get(String path) async {
    try {
      return await _client.getJson(path);
    } on LangGraphException catch (error) {
      if (error.statusCode == 404) {
        // The provider has no routine API at all. Every other status is a
        // real failure and must surface as one: a 500 that hid the
        // destination would look like the feature was never there.
        throw const RoutineUnsupported();
      }
      log.warning('LangGraphRoutineService: $path failed (${error.statusCode})');
      rethrow;
    }
  }
}
```

If `LangGraphClient` has no `patchJson` or `delete`, add them alongside the existing `postJson`/`getJson`, following those methods' exact error-mapping shape. Do not change the existing methods.

- [ ] **Step 4: Run the tests**

```bash
flutter test test/data/services/agent/langgraph_routine_service_test.dart
flutter analyze lib/data/services/agent
```

Expected: PASS, analyzer clean.

- [ ] **Step 5: Commit**

```bash
git add lib/data/services/agent/langgraph_routine_service.dart lib/data/services/agent/langgraph_client.dart test/data/services/agent/langgraph_routine_service_test.dart
git commit -m "feat(routines): the LangGraph routine service"
```

---

## Task 16: Expose routines through the agent seam

**Files:**
- Modify: `lib/data/services/agent/agent_service.dart`, `lib/data/services/agent/langgraph_agent_service.dart`, `lib/data/repositories/agent_repository.dart`
- Test: `test/data/repositories/agent_repository_test.dart`

**Interfaces:**
- Consumes: `LangGraphRoutineService` (Task 15).
- Produces: `AgentService.routines` (null by default) and `AgentRepository.routines`, consumed by Task 17's view model.

Read how `widgets` is exposed on all three and mirror it exactly. It is the same seam with a different facet.

- [ ] **Step 1: Write the failing test**

Add to `test/data/repositories/agent_repository_test.dart`:

```dart
test('the mock provider has no routine facet', () {
  // Provider-agnostic by construction: only a provider that implements the
  // API gets a service, and the destination hides for the rest.
  expect(MockAgentService().routines, isNull);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `flutter test test/data/repositories/agent_repository_test.dart`
Expected: FAIL, `routines` is not defined.

- [ ] **Step 3: Add the facet**

In `lib/data/services/agent/agent_service.dart`, beside the `widgets` getter:

```dart
  /// The provider's routine capability, or null when it has none.
  ///
  /// Null is the default and the honest answer for most providers: routines
  /// are an optional resource API, not part of the agent protocol.
  RoutineService? get routines => null;
```

In `lib/data/services/agent/langgraph_agent_service.dart`, override it to return a `LangGraphRoutineService` built on the same client, constructed the same way and at the same point in the lifecycle as the widget service.

In `lib/data/repositories/agent_repository.dart`, expose `RoutineService? get routines => _service?.routines;`, mirroring the widget getter.

- [ ] **Step 4: Run the tests**

```bash
flutter test test/data
flutter analyze lib/data
```

Expected: PASS, analyzer clean.

- [ ] **Step 5: Commit**

```bash
git add lib/data/services/agent/agent_service.dart lib/data/services/agent/langgraph_agent_service.dart lib/data/repositories/agent_repository.dart test/data/repositories/agent_repository_test.dart
git commit -m "feat(routines): expose the routine facet through the agent seam"
```

---

## Task 17: The Routines view model

**Files:**
- Create: `lib/ui/features/routines/view_models/routines_view_model.dart`
- Test: `test/ui/features/routines/routines_view_model_test.dart`

**Interfaces:**
- Consumes: `AgentRepository.routines` (Task 16).
- Produces: `RoutinesViewModel` with `supported`, `loading`, `routines`, `error`, `load()`, `setPaused(Routine, bool)`, `remove(Routine)`.

- [ ] **Step 1: Write the failing tests**

Create `test/ui/features/routines/routines_view_model_test.dart`:

```dart
import 'package:assistant/data/services/agent/routine_service.dart';
import 'package:assistant/domain/models/routines/routine.dart';
import 'package:assistant/ui/features/routines/view_models/routines_view_model.dart';
import 'package:flutter_test/flutter_test.dart';

Routine _routine({
  String id = 'r-1',
  RoutineStatus status = RoutineStatus.active,
  int revision = 1,
}) =>
    Routine(
      id: id,
      title: 'Flights',
      instruction: 'Check fares.',
      cadence: const RoutineCadence({'daily_at': '08:00'}),
      status: status,
      revision: revision,
      consecutiveFailures: 0,
    );

class _FakeService implements RoutineService {
  _FakeService({this.unsupported = false, this.failListing = false});

  final bool unsupported;
  final bool failListing;
  List<Routine> rows = [_routine()];
  int updates = 0;

  @override
  Future<RoutineCapabilities?> capabilities() async {
    if (unsupported) throw const RoutineUnsupported();
    return const RoutineCapabilities(cadenceKinds: [], canCreate: false);
  }

  @override
  Future<List<Routine>> list() async {
    if (unsupported) throw const RoutineUnsupported();
    if (failListing) throw Exception('network');
    return rows;
  }

  @override
  Future<Routine> update(
    String id, {
    required int expectedRevision,
    String? title,
    RoutineCadence? cadence,
    RoutineStatus? status,
  }) async {
    updates++;
    return _routine(status: status ?? RoutineStatus.active, revision: expectedRevision + 1);
  }

  @override
  Future<void> delete(String id) async => rows = [];
}

void main() {
  test('starts supported so the destination is not hidden before the probe',
      () {
    // A menu item that flickers out on every cold start is worse than one
    // that briefly shows against a provider without routines.
    expect(RoutinesViewModel(service: _FakeService()).supported, isTrue);
  });

  test('an explicit unsupported hides the destination', () async {
    final model = RoutinesViewModel(service: _FakeService(unsupported: true));

    await model.load();

    expect(model.supported, isFalse);
  });

  test('a transport failure never hides the destination', () async {
    // The whole reason RoutineUnsupported is a distinct exception.
    final model = RoutinesViewModel(service: _FakeService(failListing: true));

    await model.load();

    expect(model.supported, isTrue);
    expect(model.error, isNotNull);
  });

  test('loading populates the list', () async {
    final model = RoutinesViewModel(service: _FakeService());

    await model.load();

    expect(model.routines, hasLength(1));
    expect(model.loading, isFalse);
  });

  test('pausing sends the routine current revision', () async {
    final service = _FakeService();
    final model = RoutinesViewModel(service: service);
    await model.load();

    await model.setPaused(model.routines.first, true);

    expect(service.updates, 1);
    expect(model.routines.first.status, RoutineStatus.paused);
  });

  test('a conflict adopts the provider truth instead of surfacing an error',
      () async {
    final model = RoutinesViewModel(service: _ConflictingService());
    await model.load();

    await model.setPaused(model.routines.first, true);

    expect(model.routines.first.revision, 9);
    expect(model.error, isNull);
  });

  test('removing drops the routine from the list', () async {
    final model = RoutinesViewModel(service: _FakeService());
    await model.load();

    await model.remove(model.routines.first);

    expect(model.routines, isEmpty);
  });
}

class _ConflictingService extends _FakeService {
  @override
  Future<Routine> update(
    String id, {
    required int expectedRevision,
    String? title,
    RoutineCadence? cadence,
    RoutineStatus? status,
  }) async =>
      throw RoutineConflict(_routine(revision: 9));
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `flutter test test/ui/features/routines/routines_view_model_test.dart`
Expected: FAIL, the target of URI doesn't exist.

- [ ] **Step 3: Write the view model**

Create `lib/ui/features/routines/view_models/routines_view_model.dart`. Follow `lib/ui/features/widgets/view_models/widgets_view_model.dart`'s established shape: prefer the repository form so a model built before `AgentRepository.initialize` still sees the real service, keep the pinned `service` for tests, resolve `_service` at call time rather than at construction, and start `_supported` true so only an explicit `RoutineUnsupported` hides the destination. A `RoutineConflict` replaces the local row with the carried truth and sets no error.

- [ ] **Step 4: Run the tests**

```bash
flutter test test/ui/features/routines
flutter analyze lib/ui/features/routines
```

Expected: PASS, analyzer clean.

- [ ] **Step 5: Commit**

```bash
git add lib/ui/features/routines/view_models/routines_view_model.dart test/ui/features/routines/routines_view_model_test.dart
git commit -m "feat(routines): the Routines view model"
```

---

## Task 18: The Routines screen and navigation

**Files:**
- Create: `lib/ui/features/routines/views/routines_screen.dart`, `lib/ui/features/routines/views/routine_card.dart`
- Modify: `lib/config/router.dart`, `lib/ui/features/chat/views/chat_drawer.dart`, `lib/ui/core/di/app_providers.dart`
- Test: `test/ui/features/routines/routines_screen_test.dart`, `test/ui/features/chat/chat_drawer_test.dart`

**Interfaces:**
- Consumes: `RoutinesViewModel` (Task 17).
- Produces: the `/routines` route and the drawer entry.

Before writing any widget code, read `PRODUCT.md`, then `BRANDKIT.md`, then `DESIGN.md`, in that order, per `RULES.md` §3.1. `BRANDKIT.md` wins any conflict. Spacing, radii and motion come from `AppSpacing`, `AppRadii` and `AppMotion`; no raw doubles.

- [ ] **Step 1: Write the failing tests**

Create `test/ui/features/routines/routines_screen_test.dart` covering:

```dart
// 1. an active routine renders its title, its cadence prose, and its next run
// 2. a routine that has never run reads "Not run yet" rather than an empty slot
// 3. an auto-paused routine says why it stopped
// 4. the empty state explains that routines are created by asking, since the
//    screen deliberately has no create button
// 5. there is no create affordance anywhere on the screen
```

Add to `test/ui/features/chat/chat_drawer_test.dart`:

```dart
// 6. the Routines entry appears when the view model reports supported
// 7. the Routines entry is absent when it reports unsupported
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `flutter test test/ui/features/routines test/ui/features/chat/chat_drawer_test.dart`
Expected: FAIL.

- [ ] **Step 3: Build the screen**

Create `routine_card.dart` and `routines_screen.dart`. Each card shows:

- the title
- the cadence in plain English, from `RoutineCadence.describe()`
- the next run, and the last check with its outcome, so a routine that has been quietly finding nothing still visibly ran
- a pause toggle
- an overflow with delete, which asks first

A paused routine is visually distinct and sorts below active ones. One that auto-paused says why. A routine that has never spoken has no thread to open, and the card says so rather than offering a dead tap.

The empty state says routines are created by asking in conversation. This screen has no create button: that is the design, not an omission.

Per `RULES.md` §3.1, every state carries a shape rather than colour alone, and every interactive element has a hit area of at least 48dp.

- [ ] **Step 4: Wire navigation**

In `lib/config/router.dart`, add a `/routines` route beside `widgets`:

```dart
          GoRoute(
            path: 'routines',
            builder: (context, state) => const RoutinesScreen(),
          ),
```

In `lib/ui/features/chat/views/chat_drawer.dart`, add a Routines entry to `_buildMenu`, beside the Widgets one, hidden on `!routines.supported`. That method's own comment already anticipates this sibling.

In `lib/ui/core/di/app_providers.dart`, provide `RoutinesViewModel` the same way `WidgetsViewModel` is provided.

- [ ] **Step 5: Run the tests**

```bash
flutter test
flutter analyze
```

Expected: PASS, analyzer clean, nothing else regressed.

- [ ] **Step 6: Commit**

```bash
git add lib/ui/features/routines lib/config/router.dart lib/ui/features/chat/views/chat_drawer.dart lib/ui/core/di/app_providers.dart test/ui
git commit -m "feat(routines): the Routines screen and its drawer entry"
```

---

## Task 19: Client documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the capability**

Add Routines beside Widgets in `CLAUDE.md`'s layer map, and a short section stating:

- Routines are an **optional provider capability**, like widgets. `AgentService.routines` returns null by default; only a provider implementing the resource API gets a service.
- The destination hides only on an explicit `RoutineUnsupported` (a 404 from the capabilities probe), never on a transport failure. `_supported` starts true for that reason.
- The screen manages routines; it never creates them. Authoring is conversational, so the agent can question a vague instruction before it is committed. `canCreate` is false in the capabilities payload.
- Cadence prose is rendered client-side from the structured object, so the closed vocabulary keeps the prose localizable and the editor honest.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: routines as an optional provider capability"
```

---

## Final verification

- [ ] **Server suite**

```bash
cd ~/GitHub/eve-ai
uv run pytest
docker compose -f docker-compose.test.yml up -d && uv run eve-migrate
uv run pytest -m integration
```

Expected: PASS.

- [ ] **Client suite**

```bash
cd ~/GitHub/open-assistant/flutter-open-assistant
flutter analyze
flutter test
```

Expected: PASS.

- [ ] **Walk the definition of done**

From the spec:

- [ ] A member can ask Eve to do something on a schedule; she creates it, confirms in prose, and it fires.
- [ ] A firing that finds nothing produces no notification, and the screen still shows it ran.
- [ ] A firing that finds something produces a notification and a repliable thread.
- [ ] "What are you watching for me?" and "stop tracking flights" both work as ordinary turns.
- [ ] The Routines screen lists, pauses, reschedules and deletes, and is absent against a provider without routines.
- [ ] A routine cannot create, cancel, or reschedule a routine.
- [ ] A routine that fails five times running pauses itself and says so once.
- [ ] `EVE_ROUTINES_ENABLED=false` leaves the tools unbound and the source unregistered.

- [ ] **Live smoke test** (needs `EVE_ROUTINES_ENABLED=true`, `EVE_AMBIENT_ENABLED=true`, and ntfy configured)

Ask Eve, in the client: "Check the weather every hour and tell me only if it's going to rain." Then confirm:

1. `list_routines` reports it.
2. A row exists with `next_run_at` about an hour out.
3. Setting `next_run_at` to the past makes the next ambient tick fire it exactly once.
4. A `NOTHING` reply leaves `last_outcome = 'silent'` and sends no push.
5. The Routines screen lists it, and pausing it stops the firing.
