# Scheduled routines: design

**Issue:** [EVE-25 Add scheduled jobs/routines](https://linear.app/chalifour-development/issue/EVE-25/add-scheduled-jobsroutines)
**Status:** Design approved, not yet implemented
**Date:** 2026-09-21

## What this is

A member asks Eve to keep doing something on a schedule, in their own words:

> "Can you track flight prices in Aeroplan points from Vancouver to Los Cabos
> in February and notify me if there are any good deals?"

Eve writes that down as a **routine**: a standing instruction plus a cadence.
From then on, on that cadence, Eve runs a full turn on her own behalf with the
member's stored instruction as the prompt, her whole toolset available, and one
decision to make at the end: is there anything worth saying? If there is, the
member gets a notification and a thread they can reply in. If there is not, she
says nothing, and the routine's row records that it ran and found nothing.

Routines are managed conversationally ("what are you watching for me?", "stop
tracking flights") and visible in a **Routines** screen in the Open Assistant
client, where they can be paused, rescheduled, and deleted.

This work spans both repositories. The server side is `eve-ai`. The client side
is `open-assistant`, and it is built as a generic, optional provider capability
in the shape `WidgetService` already established, so nothing Eve-specific
enters that repository.

## What a routine is, and what it is not

**A routine is a stored prompt on a schedule.** Each firing is an ordinary
headless Eve turn. She reads the instruction, decides for herself what to
check and how, uses whatever specialists, skills, and computer access the
request needs, and answers in her own voice.

The rejected alternative was a **declarative watcher**: Eve translates the
request into structured fields (what to poll, what threshold counts as
interesting) and a cheap evaluator runs those fields on a schedule. That is
cheaper and more auditable, and it fails the motivating example outright.
"A good deal on Aeroplan points to Los Cabos" has no credentialed reader in
`eve-tools`, no threshold anyone can name in advance, and no structured feed.
Answering it requires judgement and a browser, which is to say it requires
Eve. A design that cannot serve the request that prompted it is not a cheaper
design; it is a different feature.

The cost of the prompt shape is honest and accepted: **a routine is a
recurring paid VOICE-tier turn.** Section "Bounds" is where that is contained.

### Non-goals

- **No routine authoring from the client.** The Routines screen manages
  routines that exist; it never creates one. Writing an instruction Eve will
  act on unattended belongs in conversation, where she can ask what "a good
  deal" means before committing it to a table.
- **No condition or threshold language.** No cheap pre-check gating the
  expensive turn. If that is ever wanted, it is a later optimization against
  a table that already has real routines in it.
- **No digesting.** Each firing that speaks is its own notification and its
  own thread, consistent with the ambient design's existing choice.
- **No routine that edits routines.** See "The ambient guard" below. This is
  a security property, not a simplification.
- **No shared or household routines.** A routine belongs to one member, like
  a widget.

## Architecture

No new service, no new deployment, no new secret. The firing path is a new
source on a loop that already ticks.

```
eve (graph)                    a member asks in chat
  schedule_routine  ─────────────────────────────────┐
  list_routines                                      │
  cancel_routine                                     ▼
                                          eve_routine  (one table)
                                                     ▲
eve-ambient (existing tick, 300s)                    │
  sources/routines.py  ── due? ──────────────────────┘
      └─> Signal(source="routines", key="<id>:<scheduled_time>")
              └─> pipeline.handle_signal   (filter bypassed, as computer/coding)
                      └─> notify.deliver   (fresh thread, runs eve, NOTHING veto, ntfy)

eve/routines/app.py            mounted beside widgets
  GET/PATCH/DELETE  ◄──────────  open-assistant "Routines" screen
```

Everything downstream of the emitted `Signal` already exists and is already
tested: dedup and cooldown (`eve_ambient.store.is_fresh`,
`already_notified`), the permission gate (`gates.scoped_audience`), thread
creation and the headless run as the member (`notify.deliver`), the `NOTHING`
veto, the ntfy push, the click-through URL, and the `eve_ambient_notice` row
that records what was sent and on which thread.

The new code in `eve_ambient` is one file, `sources/routines.py`, plus one
entry in the `SOURCES` tuple:

```python
Source("routines", False, "routines", routines.poll),
```

`per_member=False`, like `finances`, `computer`, and `coding`: the table is
queried once per tick for every due routine across the household, and each
signal carries its own `member_sub` from the row. Polling once per member
would issue one query per member to answer the same question.

### Requested, not noticed

`eve_ambient.pipeline._REQUESTED_SOURCES` becomes
`("computer", "coding", "routines")`.

That tuple currently means one thing and is about to mean it more strongly:
these are signals a member explicitly asked for, so the REFLEX relevance
filter is bypassed, and so are quiet hours and the daily cap. An LLM deciding
that the answer to a direct request is "not relevant" and swallowing it is the
worst failure mode available, and a routine is the most direct request in the
system: the member did not merely ask once, they asked for it to keep
happening.

The daily cap bypass deserves its own sentence, because the alternative was
considered and rejected. Counting routine firings against
`ambient_daily_cap` would let a chatty calendar starve a routine the member
deliberately created. Standing spend is bounded by the routines table, which
the member can see and edit, not by a shared counter they cannot.

Quiet hours are bypassed for the same structural reason, with a practical
mitigation rather than a gate: because cadence is authored per routine in the
member's own timezone, a member who does not want to hear from a routine at
3am schedules it for 8am. The schedule is the quiet-hours control.

### Why not a graph node, a cron, or a new loop

Three alternatives, each already argued somewhere in this repository.

**Aegra's built-in cron scheduler.** Rejected in the Phase 4 ambient design
and still rejected: creating the notification thread from inside a graph node
means importing Aegra's private `_prepare_run`. Nothing about routines changes
that calculus.

**A dedicated loop in `eve-ambient`, beside `_supervise_forever`.** The coding
supervisor earned its own loop because it is a control loop with an agent
waiting on the other end, where 300 seconds of latency per conversational turn
would make Eve a bad correspondent. A routine has no such counterparty. Its
finest cadence is hourly, so a 300-second tick is three orders of magnitude
more precise than it needs to be, and a separate loop would have to either
re-implement the gate chain or bypass it.

**A Kubernetes CronJob.** Gives up the reply-in-place behaviour that makes a
notification a conversation, which the ambient design calls load-bearing.

### What this does not contradict

[ADR 0005](../../adr/0005-memory-storage.md) says Phase 2 introduces no cron,
no worker, and no scheduled job of any kind. That statement is about **memory
decay**, which stays read-time and is untouched here. The Phase 4 rejection of
cron is about **firing graph runs from inside the graph**, which this design
also does not do. Neither position is revised: this adds a source to a loop
that already exists. No new ADR is warranted; the spec reference is enough.

### The compose prompt

`eve_ambient.notify.compose_prompt` currently ends every ambient turn with
"You noticed this; nobody asked you." That sentence is correct for a calendar
event and wrong for a routine, where the member did ask, in advance, in
writing.

`compose_prompt` gains one branch for `source == "routines"`. The routine
branch supplies the member's own instruction as the body, states that it is a
standing request the member set up and when, and keeps the veto instruction
verbatim so silence remains expressible:

```
{ambient_marker(member.name)}
{routine.instruction}
This is a standing request {member.name} set up on {created_at:%B %-d}. It runs
{cadence in prose} and this is its {ordinal} run.
Decide whether there is anything worth telling them right now. If there is, say
it in one or two sentences in your own voice. If there is nothing worth saying,
reply with exactly NOTHING and nothing else.
```

The `ambient_marker` prefix stays, and this is not incidental. `is_ambient_text`
fails closed on that marker, and `may_author` keys off it to keep a turn driven
by non-member-typed text from authoring rules or procedures. A routine's
instruction is member-written, but it is replayed unattended and indefinitely,
which is exactly the property the marker exists to contain. Dropping it to make
routines feel more "real" would quietly reopen that path.

## Data model

One table, one migration, `0011_eve_routine`, revising `0010_eve_widget_resource`.

| column | type | notes |
|---|---|---|
| `id` | `uuid` primary key | a high-entropy locator, never a capability |
| `member_sub` | `text not null` | in every `WHERE` clause, no exception |
| `title` | `text not null` | what the Routines screen lists |
| `instruction` | `text not null` | the standing prompt, in the member's words |
| `cadence` | `jsonb not null` | closed vocabulary, validated in Python |
| `timezone` | `text not null` | snapshotted at creation from the member |
| `status` | `text not null default 'active'` | `active`, `paused`, `expired` |
| `next_run_at` | `timestamptz not null` | the column the tick queries |
| `last_run_at` | `timestamptz` | drives "last checked" in the UI |
| `last_outcome` | `text` | `spoke`, `silent`, `error` |
| `consecutive_failures` | `int not null default 0` | auto-pause counter |
| `expires_at` | `timestamptz` | optional horizon, null means indefinite |
| `revision` | `bigint not null default 1` | optimistic concurrency |
| `created_at` / `updated_at` | `timestamptz not null default now()` | |

One index, which is the tick's only query:

```sql
CREATE INDEX eve_routine_due ON eve_routine (status, next_run_at);
```

`src/eve/routines/store.py` owns every statement against this table, and every
one of them carries `member_sub`, with exactly one deliberate exception: the
due-routines query the ambient tick runs, which is household-wide by
construction and returns each row's own `member_sub` for the signal to carry.
That exception is the same shape `eve.computer.store.recently_resolved_tasks`
already has, and it is named here so it reads as a decision rather than an
oversight.

### Why `cadence` is jsonb

The same reason the widget recipe is jsonb rather than columns: the set of
legal shapes will grow, and a migration per shape is the per-domain cost that
pattern exists to avoid. The vocabulary is closed and validated in Python, in
`src/eve/routines/cadence.py`, which imports nothing from `eve` and holds two
pure functions:

```python
validate(cadence: dict) -> str | None
next_after(cadence: dict, timezone: str, after: datetime) -> datetime
```

Three shapes are legal:

```json
{"every_hours": 6}
{"daily_at": "08:00"}
{"weekly_at": {"day": "sunday", "time": "19:00"}}
```

`every_hours` is an integer from 1 to 168. **One hour is a hard floor in the
validator**, not a policy setting: `{"every_hours": 0}` is a tight loop, and a
bound that prevents a nonsensical value belongs to the vocabulary rather than
to configuration. A time is `HH:MM` in 24-hour form. A day is a lowercase
English weekday name.

`next_after` computes in the routine's local timezone and converts to UTC,
which is why `timezone` is a stored column and not a lookup against
`family.yaml`. A member who moves keeps their existing routines firing at the
local times they chose, and a routine crossing a DST boundary fires at 08:00
local on both sides of it rather than drifting an hour.

### Why `next_run_at` is stored rather than computed

A schedule computed at read time makes "has this already fired this hour" a
function of when the tick happened to run, which is a race with no owner. A
stored `next_run_at` makes firing a compare-and-advance instead:

```sql
UPDATE eve_routine
   SET next_run_at = %(next)s, updated_at = now()
 WHERE id = %(id)s AND next_run_at = %(observed)s AND status = 'active'
```

The source claims the row by advancing `next_run_at` **before** emitting the
signal, and the guard on the observed value means a second worker, a restarted
pod, or a compose turn still running when the next tick arrives cannot fire the
same occurrence twice. The signal key is `<routine_id>:<scheduled_time>`, so
the existing `is_fresh` and `already_notified` checks are a second net behind
that, keyed on the occurrence rather than the routine.

A routine whose `next_run_at` is far in the past (the service was down for a
day) fires **once** and schedules forward from now, never a backlog of missed
occurrences. Six silent 8am checks delivered at once would be the single most
annoying possible behaviour, and the member wanted a habit, not an audit trail.

## Eve's three tools

Bound in `graph.py` alongside the specialists, behind a new `routines`
permission granted to both members in `family.yaml`, each with an entry in
`_TOOL_LABELS`.

```python
schedule_routine(title, instruction, cadence, expires_at=None) -> str
list_routines() -> str
cancel_routine(reference) -> str
```

`schedule_routine` checks in this order, which is the `save_widget` order for
the same reason: refuse the turn before validating the input, validate the
input before checking the grant, check the grant before touching storage.

1. Not an ambient turn (see below).
2. `cadence.validate` passes.
3. Title within its length bound; instruction within its length bound.
4. `permission_denial(permissions, "routines")` is `None`.
5. Store, computing `next_run_at` from `cadence.next_after`.

It returns prose Eve can relay rather than a bare id, so the confirmation she
speaks is grounded in what was actually written: "I'll check that every morning
at 8:00, starting tomorrow, until the end of February."

`cancel_routine` takes a `reference` that matches an id or a title, because
the member will say "stop tracking flights" and not a uuid. An ambiguous title
returns the candidate titles and cancels nothing.

`list_routines` is member-scoped and exists so "what are you watching for me?"
is an ordinary turn rather than a trip to the Routines screen.

### The ambient guard

All three tools refuse when the turn was composed by the ambient pipeline
rather than typed by a member, returning a sentence rather than raising.

This is the load-bearing guard in the whole design. A routine firing **is** an
ambient turn, so:

- A routine cannot create a routine. There is no fork bomb.
- A routine cannot cancel or pause a routine, including itself. A routine that
  reasons its way to "I should stop running" cannot act on it.
- A routine cannot edit its own cadence to run more often.

The reason is the one `save_widget`'s docstring already gives: the ambient
credential can impersonate any member, so an ambient turn must not create or
destroy durable resources in someone's account. The only ways a routine ends
are the member, expiry, and auto-pause.

**How the guard is actually implemented, and a bug it uncovered.** The obvious
move is to copy `save_widget`, which checks
`config["configurable"]["is_ambient"]`. That check does not work. Nothing in
`src/` ever sets `is_ambient`: `eve_ambient.notify.deliver` calls
`client.runs.wait(thread_id, "eve", input={...})` and passes no `config` at
all, so the key is absent on every real ambient turn and
`configurable.get("is_ambient")` is always falsy in production. The only place
it is ever set is `tests/test_widgets_tools.py`, which constructs the config by
hand, so the test passes while the deployed guard does nothing.

These three tools therefore use the mechanism that **does** work, the one
`write_skill` already uses: take `state: Annotated[EveState, InjectedState]`,
read backwards to the last `HumanMessage`, and test it with
`eve.state.is_ambient_text`, which keys off the `ambient_marker` prefix that
`compose_prompt` puts on every ambient turn and fails closed on anything
ambiguous. That is a real signal on a real ambient turn, and `may_author` is
built on it for exactly this reason.

Two consequences, both deliberate:

- The routines tools must not be given an `is_ambient` check even as a belt to
  the braces, because a guard that reads as protection while doing nothing is
  worse than no guard: the next person copies it.
- **`save_widget`'s guard is broken today and should be fixed the same way.**
  It is out of scope for this design and belongs in its own small change, but
  it is a live hole (an ambient turn can currently create a widget in any
  member's account), so it should be filed rather than absorbed silently here.
  If the implementation plan can fix it cheaply alongside the shared helper
  below, that is strictly better than leaving it.

Because three tools plus `write_skill` plus, eventually, `save_widget` all want
the same predicate, it belongs in one place: a small helper beside
`may_author` in `eve/state.py` that takes the message list and answers whether
this turn was member-typed. One predicate, one test, no second copy to drift.

This is exactly the failure `eve/state.py`'s own docstring warns about: "Two
copies of this boolean would be two guards to keep in step."

## Bounds

A routine is standing spend, so the failure modes worth bounding are the ones
that spend without producing anything.

**The cadence floor** is one hour, enforced in `cadence.validate`.

**Expiry.** `expires_at` is optional and Eve sets it when the request has a
natural horizon. "Track flights in February" should stop existing in March
rather than run forever. A routine past its expiry is moved to `expired` by
the same tick that would have fired it, and it stops being queried; it stays
in the table and in the screen so the member can see what lapsed and restart
it.

**Consecutive-failure auto-pause.** `consecutive_failures` increments only on
infrastructure failure: a `DeliveryError`, or a run that produced no final
assistant message at all. It **never** increments on a `NOTHING` veto, because
silence is the routine working correctly. It resets to zero on any run that
completes, whether Eve spoke or not.

At five consecutive failures, roughly a day of hourly retries, the routine
flips to `paused`, one notification goes out saying so, and `last_outcome`
keeps the reason visible in the screen. The threshold is
`EVE_ROUTINE_FAILURE_LIMIT`, defaulting to 5.

A paused routine is not deleted and not silently forgotten; it waits for the
member. This is the same posture the rest of the ambient subsystem takes: the
failure that silences Eve forever is worse than the one that lets a
notification through, so a routine that cannot run says so once rather than
either retrying forever or vanishing.

**`EVE_ROUTINES_ENABLED`** defaults to `false`, the same posture as
`ambient_enabled`, `sandbox_enabled`, and `computer_enabled`. When off, the
source is not registered and the three tools are not bound, so a deployment
that has not thought about standing spend does not acquire it by upgrading.

## The Routines screen

### Server: the resource API

`src/eve/routines/app.py`, mounted the way the widget API is, with the same
contract: the router declares `Depends(require_auth)` on itself rather than
relying on Aegra's `enable_custom_route_auth` walk, the member is resolved from
the authenticated principal, an id is a locator and never a capability, absent
and foreign ids answer identically, and a stale write is a 409 carrying the
current snapshot.

```
GET    /provider-resources/v1/routines/capabilities
GET    /provider-resources/v1/routines
PATCH  /provider-resources/v1/routines/{id}
DELETE /provider-resources/v1/routines/{id}
```

`PATCH` accepts `status` (`active` or `paused` only; `expired` is the
server's to assign), `cadence`, `title`, and `expires_at`, each guarded by
`expectedRevision`. A cadence change recomputes `next_run_at` server-side.
Resuming a paused routine resets `consecutive_failures` to zero, because the
member resuming it is the acknowledgement the counter was waiting for.

**There is deliberately no POST.** Creating a routine means writing an
instruction Eve will act on unattended; that belongs in conversation where she
can push back on a vague one. The screen manages what exists.

`capabilities` exists so the client can tell "this provider has no routines"
from "the network is down." A 404 means the former and hides the menu item;
anything else means the latter and must not.

**One refactor this requires.** `aegra.json`'s `http.app` takes a single app,
currently `./src/eve/widgets/app.py:app`. This design needs two routers behind
one app, so a small top-level app (`src/eve/http_app.py`) includes both and
`aegra.json` points at it. This is real work with a real risk of breaking the
widget routes, not a footnote: the widget path prefixes, the auth dependency,
and Aegra's unauthenticated health probes must all behave exactly as they do
today, and the existing widget app tests are the check on that.

### Client: a generic provider capability

`open-assistant` must not learn what Eve is, so routines enter it as an
optional provider capability in the exact shape `WidgetService` established.

New files:

```
lib/data/services/agent/routine_service.dart          abstract + RoutineUnsupported
lib/data/services/agent/langgraph_routine_service.dart the implementation
lib/domain/models/routines/routine.dart
lib/ui/features/routines/view_models/routines_view_model.dart
lib/ui/features/routines/views/routines_screen.dart
lib/ui/features/routines/views/routine_card.dart
```

Touched: `agent_service.dart` gains a `routines` facet returning null by
default, so `mock` and `openclaw` are unsupported by construction;
`agent_repository.dart` exposes it; `router.dart` gains `/routines`;
`chat_drawer.dart`'s `_buildMenu` gains a **Routines** entry beside Widgets,
which is the sibling that method's own comment anticipates.

`RoutineUnsupported` is a distinct exception from every transport failure, and
`_supported` starts `true` so the destination is never hidden before the probe
answers and is only ever hidden by an explicit unsupported. A network hiccup
must never remove a menu item.

The screen is a list of cards. Each shows the title, the cadence rendered in
plain English ("Every morning at 8:00", "Every 6 hours", "Sundays at 7:00 PM"),
the next run, and the last check with its outcome, so a routine that has been
quietly finding nothing still visibly ran. Each card has a pause toggle and an
overflow with reschedule and delete. Deleting asks first.

Cadence rendering happens client-side from the structured `cadence` object,
not from a server-sent string. The closed vocabulary is what makes that
possible, and it keeps the prose localizable and the picker honest: the same
three shapes the validator accepts are the three the editor can produce.

Tapping a card opens the thread from that routine's most recent firing, which
is already stored: `eve_ambient_notice.thread_id`. A routine that has never
spoken has no thread, and the card says so rather than offering a dead tap.

A paused routine is visually distinct and sorts below active ones. One that
auto-paused after failures says why.

## Testing

Server, following this repository's existing tiers and naming:

- `tests/test_routines_cadence.py`: pure. Every legal shape, every rejection,
  the one-hour floor, DST boundaries in both directions, and the
  weekly-rollover and month-end cases `next_after` has to get right.
- `tests/test_routines_store.py`: integration, against the compose services.
  Member scoping on every statement, and the compare-and-advance claim under a
  simulated concurrent tick.
- `tests/test_routines_tools.py` covers the three tools: validation order, the
  permission denial, the title-matching cancel including the ambiguous case,
  and **the ambient refusal on all three**, which is the test that pins the
  security property rather than the behaviour. That test must drive the refusal
  through a message list carrying the real `ambient_marker`, the way
  `compose_prompt` produces it, and never by hand-setting a config key. A test
  that constructs its own marker of ambience can pass against a guard that
  production never triggers, which is precisely how `save_widget`'s broken
  check survived review.
- `tests/test_ambient_sources_routines.py` covers due selection, the claim, expiry
  transitions, failure counting, the reset on success, and that a `NOTHING`
  veto does **not** count as a failure.
- `tests/test_routines_app.py` pins the route contract: 401, 404 for absent and
  foreign alike, 409 with a fresh snapshot, the rejection of a `member_sub` in
  a body, and that `PATCH status` refuses `expired`.
- `tests/test_widgets_app.py` and the alembic graph test must keep passing
  unchanged through the `http.app` refactor. That is the check on it.

Client, in `open-assistant`'s existing style: view-model tests for the
supported/unsupported probe and its start-true posture, cadence-prose rendering
for all three shapes, and a widget test that the drawer entry appears only when
supported.

## Definition of done

- A member can ask Eve in conversation to do something on a schedule, and she
  creates it, confirms it in prose, and it fires.
- A firing that finds nothing worth saying produces no notification, and the
  screen still shows that it ran.
- A firing that finds something produces a notification and a thread the member
  can reply in.
- "What are you watching for me?" and "stop tracking flights" both work as
  ordinary turns.
- The Routines screen lists, pauses, reschedules, and deletes, and is absent
  against a provider that does not implement routines.
- A routine cannot create, cancel, or reschedule a routine.
- A routine that fails five times running pauses itself and says so once.
- `EVE_ROUTINES_ENABLED=false` leaves the tools unbound and the source
  unregistered.

## Consequences for existing documents

- `docs/architecture.md` gains a **Routines** section and the module map gains
  `src/eve/routines/`; the ambient source list gains `routines`; the migration
  list gains `0011_eve_routine`; the `aegra.json` note is updated for the
  two-router app.
- `family.yaml` gains the `routines` permission for both members, with the
  comment convention the other grants follow.
- `README.md`'s phase table is unchanged. This is not a new phase; it is a
  capability on the ambient one.
- `open-assistant`'s `CLAUDE.md` gains Routines beside Widgets in the layer
  map and a short section stating that it is an optional provider capability
  whose absence is normal.
- A separate issue should be filed for `save_widget`'s inert `is_ambient`
  check (see "The ambient guard"). It is a live gap in an already-shipped
  feature, found by this design rather than created by it, and it should not
  be buried in a routines changelog.

## What this deliberately does not do

- **No routine-authored routines**, by the ambient guard above.
- **No shared routines.** One owner, like a widget. Two members who want the
  same watch create two, which costs a row and keeps the audience question
  from ever arising.
- **No per-firing cost accounting.** The bound is the cadence floor, the
  expiry, and a table the member can see. Metering a household assistant's
  spend per routine is a reporting feature, and nobody has asked for it.
- **No backfill of missed occurrences.** One catch-up firing, then forward.
- **No sub-hourly cadence**, and no cron expressions. A model-authored
  `* * * * *` is a VOICE turn every minute, and the closed vocabulary is what
  makes the mobile editor and the plain-English rendering possible.
