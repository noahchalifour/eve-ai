# Eve Phase 4 — Ambient — Design

**Date:** 2026-08-23
**Status:** Approved, not yet implemented.
**Scope of this document:** the full design for Phase 4, "Ambient." Program
context and phase decomposition live in
[`2026-08-17-eve-core-design.md`](2026-08-17-eve-core-design.md); memory
mechanics in [`2026-08-18-eve-memory-design.md`](2026-08-18-eve-memory-design.md);
the tools loop, specialists, `eve-tools`, and permission enforcement in
[`2026-08-21-eve-specialists-design.md`](2026-08-21-eve-specialists-design.md).
All three are assumed throughout and not repeated.

**Delivers:** requirement R9 — "Eve is proactive; she initiates contact based
on real signals." When this phase ships, Eve speaks first.

---

## 1. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | CalDAV endpoint and per-member app passwords in Vault (`kv/credentials/eve-caldav`) | Not started |
| P2 | An ntfy instance reachable in-cluster, with a topic and access token. If the lab runs none, deploying it belongs to the `infrastructure` repository, not here. | Not started |
| P3 | A Home Assistant automation that POSTs watched state changes to `eve-ambient` (see §4.4). Authored in Home Assistant, not in this repository. | Not started |
| P4 | `EVE_AMBIENT_TOKEN` generated and stored in Vault (`kv/credentials/eve`), delivered to the `eve-ambient` pod only. | Not started |

None blocks the start of implementation. The signal interface, the gate chain,
the impersonation auth path, and the notification path can all be built and
unit-tested against fakes. P1 and P3 block live verification of the calendar
and home sources; P2 blocks live verification of delivery.

---

## 2. What "Eve speaks first" has to mean

Three things, in order of how badly they fail when missed:

1. **She is right to interrupt.** A proactive assistant that is wrong twice
   gets muted forever. Every gate in §5 exists to make silence the default
   outcome.
2. **She sounds like Eve.** A proactive message is written by the same
   `VOICE`-tier model, with the same persona, memory, and tools as a
   conversational answer. It is not a templated alert with a persona sticker
   on it.
3. **You can answer her.** Every notification lands in a real thread owned by
   the member, so replying continues the conversation Eve started instead of
   requiring the member to re-explain it in a new one.

### 2.1 Non-goals

- **No learned interrupt-worthiness.** Eve does not tune her own filter from
  which notifications got a reply. That is Phase 5, and it needs the
  `eve_ambient_notice` history this phase writes.
- **No digesting or batching.** No "here are three things from this morning."
  Each signal that survives the gates is its own notification in its own
  thread.
- **No Home Assistant polling.** Push only (§4.4).
- **No per-member notification preferences surface.** Caps, quiet hours, and
  watched entities are operator configuration, not a family-facing setting.
- **No new specialist.** The calendar gains a *client* in `eve-tools` because
  the calendar source needs one; it does not gain an `ask_calendar` specialist
  tool. That is a small, separate change whenever it is wanted.

---

## 3. Architecture overview

```
eve-ambient  (new deployment, this repository, src/eve_ambient/)
  |
  |-- poll loop      asyncio, EVE_AMBIENT_POLL_INTERVAL_SECONDS (300)
  |                  sources: calendar, mail, finances  ->  eve-tools /invoke
  |-- POST /signals/home-assistant   Home Assistant pushes, shared secret
  |
  '-- pipeline   normalize -> dedup/cooldown -> REFLEX filter
                 -> permission gate -> budget -> quiet hours -> notify

notify:  create a thread as the member (impersonating service token)
         -> run the `eve` graph with a synthetic developer message
         -> read the final AI message
         -> ntfy push, unless Eve declined to speak

state:   three eve-db tables, installed by the existing eve-migrate entrypoint
```

`eve-ambient` holds **no third-party credential** — ADR 0006 stands unchanged.
It has exactly two secrets: the `eve-tools` API key it needs in order to poll,
and the impersonation token of §6.1 — which is necessarily *shared* with the
`eve` pod, because that is where it gets verified, exactly as
`EVE_TOOLS_API_KEY` is already one secret shared between `eve` and
`eve-tools`. Every third-party call still terminates in `eve-tools`.

The `eve` graph is **not modified**. Ambient is a new *caller* of it, which is
what the Phase 3 spec §15 predicted when it noted that the
specialist-as-tool shape is invocable from outside a conversational turn.

**Why a separate service.** Two alternatives were considered and rejected.
Aegra's built-in cron scheduler is real, enabled by default, in-process, and
fires runs as the cron's owner, so an `ambient` graph fired by crons would have
needed no new deployment and no new credential — but it can only poll, and
creating the notification thread from inside a graph node means importing
Aegra's private `_prepare_run`. A Kubernetes CronJob pushing to ntfy only is
smaller still, and gives up the reply-in-place behaviour §2 calls load-bearing.
A standing service was chosen for the webhook receiver, the clean HTTP boundary
against Aegra, and restart isolation from the request path; the cost is one
deployment and one impersonation secret, both accounted for below.

---

## 4. Signal sources

One interface, so the poll loop is the same code for every polled source and a
pushed source constructs `Signal`s directly:

```python
@dataclass(frozen=True)
class Signal:
    source: str             # calendar | mail | finances | home
    key: str                # stable identity, for dedup and cooldown
    occurred_at: datetime
    member_sub: str | None  # whose account it came from; None = household
    summary: str            # one plain line, for the filter and the prompt
    payload: dict           # detail Eve may want to reason over

async def poll(cursor: str | None) -> tuple[list[Signal], str | None]
```

`summary` exists so the REFLEX filter reads one line rather than a raw API
payload; `payload` exists so the compose turn has the detail the summary
dropped. Cursors are opaque strings owned by each source and persisted per
`(source, member_sub)`.

### 4.1 Calendar

The one genuinely new client. `eve-tools` gains `calendar.list_events` over
CalDAV (the `caldav` library), reading per-member credentials from
`EVE_TOOLS_CALDAV_CREDENTIALS_JSON` — a JSON object keyed by member sub,
exactly the shape `gmail.py` already uses for its per-member tokens, so that
service keeps one credential pattern rather than two.

Two kinds of signal: an event whose start enters the
`EVE_AMBIENT_CALENDAR_LOOKAHEAD_MINUTES` (90) window, keyed on
`uid + start`; and an event whose `etag` changed since the last poll, keyed on
`uid + etag`, which covers new invites, reschedules, and cancellations without
a separate code path.

### 4.2 Mail

Reuses the existing `mail.list_messages(member_sub, query)` with
`is:unread newer_than:1d`. Keyed on message id. Polled only for members
holding `mail.read`, so a member without the permission costs no API call
rather than being filtered later. The calendar source polls only members
holding `calendar.read`, for the same reason.

### 4.3 Finances

Reuses `finances.list_transactions` every poll, keyed on transaction id, and
`finances.get_budgets` once per day, keyed on `budget id + period + state`, so
an overrun notifies once for the month rather than once per poll. Household
scope: `member_sub` is `None` and the audience comes entirely from §5.

### 4.4 Home Assistant — push only

`POST /signals/home-assistant` accepts
`{entity_id, state, friendly_name, occurred_at}` with
`EVE_AMBIENT_HA_WEBHOOK_SECRET` in a header, and constructs one household
`Signal` keyed on `entity_id + state`.

Polling was rejected rather than deferred: it would need a hand-maintained
watched-entity list in Eve's config duplicating logic Home Assistant already
expresses as automations, and it would still be up to a poll interval late.
Which entities are worth Eve's attention is a Home Assistant question,
answered where the entities live. If Home Assistant turns out unable to reach
the service, a watched-entity poller is the fallback, and it is additive.

### 4.5 Dedup and cooldown

`eve_ambient_seen(source, key)` is inserted `ON CONFLICT DO NOTHING`. A key
seen within `EVE_AMBIENT_COOLDOWN_HOURS` (6) is dropped, so a door that
reports `open` on every state change notifies once; the same key occurring
after the window may fire again, because six hours later it is news again.

A signal is marked seen **only once the pipeline has resolved it**: rejected
by a gate, vetoed by Eve, or committed to a thread. A crash before the thread
exists leaves the signal unseen and the next poll retries it; a failure after
it exists does not, because the content is already delivered and a retry would
re-run the turn (§6.4). Marking seen on receipt would fail silently instead,
which is worse.

---

## 5. The gate chain

Four gates, cheapest first, so only survivors cost a `VOICE`-tier turn.

**1. The REFLEX filter.** One structured call, the same pattern
`memory/extract.py` established, over the signal's `summary` and `payload`,
the roster from `family.yaml`, and household-layer memory. It returns
`{notify: bool, audience: [member_sub], urgent: bool, why: str}`.

Household memory is included because it is a cheap always-on SQL read and it
is what stops "trash day tomorrow" firing at a family that already knows.
Per-member profile memory is deliberately *not* read here: the compose turn
does full recall anyway, and paying for per-member recall before knowing the
audience inverts the cost order.

`why` is stored and logged. A filter whose reasoning is invisible cannot be
corrected.

**Audience scope is bounded by source.** A `mail` signal may only ever notify
the member whose mailbox it came from — private correspondence is not the
filter's to redistribute, and no permission string expresses "may read Noah's
mail." `calendar`, `finances`, and `home` signals may notify any permitted
member the filter names, because a family calendar, a household budget, and an
open garage door are shared logistics by nature. The filter chooses *whether*
in the first case and *who* in the others.

**2. The permission gate.** Each candidate member is checked through the
existing `specialists/permissions.py` helper against the string the roster
actually uses today — `mail.read` for mail, `finances` for finances,
`home.control` for home — plus one new string, `calendar.read`, granted to both
adults in `family.yaml`, because the calendar source is new and no existing
permission covers it. A member the filter named who lacks the permission is
dropped and logged. This gate — not the filter's judgment — is what keeps
finance notifications away from a member who should not see them.

**3. The daily cap.** `EVE_AMBIENT_DAILY_CAP` (6) notifications per member per
member-local day, counted from `eve_ambient_notice` rows using the member's
`family.yaml` timezone.

**4. Quiet hours.** `EVE_AMBIENT_QUIET_HOURS` (`21:00-07:00`), member-local.

`urgent` bypasses gates 3 and 4. It never bypasses gate 2. The criteria are a
short closed list in the filter prompt — fire or smoke, water, security,
medical — and every bypass is logged at warning level and marked in the
notification title, because a 3am false alarm is only fixable if it is
visible.

Signals stopped by the cap or quiet hours are **dropped, not queued.** A queue
delivers yesterday's door-open at breakfast, which trains the family to ignore
Eve. If it mattered at 2am it was urgent.

---

## 6. The notification path

### 6.1 Impersonation

Aegra scopes threads to `user.identity`, and it does so before any handler in
`auth.py` runs (see `architecture.md`, "Auth and thread scoping"). For a
notification to land in a thread the member owns and can reply in,
`eve-ambient` must authenticate *as that member*.

`src/eve/auth.py` gains a third mode alongside `oidc` and `dev`: a service
token plus an `x-eve-on-behalf-of` header. The token is compared against
`EVE_AMBIENT_TOKEN` in constant time; a token under 32 characters is refused
at startup by `Settings.model_post_init`, the same place `dev` mode is refused
in production; the named subject must exist in `family.yaml`; and the resolved
principal is that member, with that member's permissions.

Unlike `dev`, this path is valid in production. It is therefore an
impersonation credential and is treated as one: it is issued to exactly two
pods — `eve-ambient`, which presents it, and `eve`, which verifies it — and
every use logs member, source, and signal key. Nothing else in the cluster
receives it. The
header is meaningless on any other auth path — a member's own bearer token
presenting `x-eve-on-behalf-of` is ignored, and there is a unit test whose only
job is to hold that true.

Rejected alternative: per-member Authentik service accounts. Four OAuth
clients, four secrets, and a token cache, buying nothing over one scoped
secret in a four-person family.

### 6.2 The compose turn

Create the thread, then run the `eve` graph and wait, with input of one
synthetic **developer** message — the phase-3 lesson that a non-human
instruction must be a developer message, not a system or user one. It states
the signal, the member, and one instruction: if nothing here is worth saying,
reply with exactly `NOTHING`.

The turn is an ordinary turn. Eve gets recall, specialists, skills, the tools
loop, and post-turn extraction. That is what lets her enrich a bare signal
("dentist at 3 — traffic's bad, leave by 2:30") and, more importantly, what
lets her decline after seeing memory the filter never read.

`NOTHING` means: delete the thread, mark the signal seen, send nothing. A
cheap second veto by the model that actually knows the household is worth more
than a cleverer filter.

### 6.3 Delivery

Otherwise: an ntfy push with Eve's text as the body, priority and tag derived
from `urgent`, and a click URL built from `EVE_AMBIENT_THREAD_URL_TEMPLATE`
pointing at the thread. Delivery sits behind a `Notifier` protocol with
exactly one implementation, `NtfyNotifier` — swappable as the program design
asked, without a factory for a single product.

### 6.4 Failure modes

Every failure loses the notification, never the content:

| Failure | Behaviour |
|---|---|
| ntfy unreachable | The thread exists with Eve's message in it. The signal is marked seen; it is not retried, because a retry would re-run the turn. |
| Aegra unreachable | No thread, no push, signal left unseen. The next poll retries it. |
| `eve-tools` unreachable | The poll for that source is skipped with its cursor unchanged. |
| REFLEX call fails | Treated as "do not notify," logged. Silence is the safe default. |
| One source raises | The other sources in the tick still run. The loop never dies on a source. |

---

## 7. Ambient turns may act

An ambient turn carries the full toolset. Eve can close a garage door or send
a reply on her own initiative, not only tell you about it.

This is a deliberate choice, and its blast radius is bounded by machinery
Phase 3 already built: the run authenticates as the member, so
`specialists/permissions.py` gates its tools to exactly what that member could
have asked Eve to do in conversation. Ambient adds no capability; it adds
initiative.

What this phase adds for it is auditability: every tool call made inside an
ambient run logs member, source, signal key, and tool name at info level, so
"why did the garage close" has an answer. The compose prompt also tells Eve
plainly that she was not asked — that acting unprompted is hers to justify in
the message she sends.

---

## 8. State and configuration changes

### 8.1 Tables

Three, appended to the ordered-DDL migration in `memory/db.py` so `eve-migrate`
stays the single schema entrypoint:

| Table | Purpose |
|---|---|
| `eve_ambient_seen(source, key, first_seen_at)` | Dedup and cooldown. `ON CONFLICT DO NOTHING`; rows pruned past 30 days. |
| `eve_ambient_cursor(source, member_sub, cursor, updated_at)` | Poll position. `member_sub` is `''` for household sources so the primary key needs no nullable column. |
| `eve_ambient_notice(id, member_sub, source, key, urgent, thread_id, sent_at)` | Every notification actually sent. |

`eve_ambient_notice` *is* the budget counter — the cap counts its rows for the
member's local day — and it is also the record of what Eve chose to interrupt,
which is the training signal Phase 5 needs. One table, no counter to drift out
of agreement with reality.

### 8.2 Settings

All on `eve.settings` with the `EVE_AMBIENT_` prefix, except the CalDAV
credential, which belongs to `eve-tools`:

`ENABLED` (**default false**, so nothing pushes until deliberately turned on),
`POLL_INTERVAL_SECONDS` (300), `DAILY_CAP` (6), `QUIET_HOURS`
(`21:00-07:00`), `COOLDOWN_HOURS` (6), `CALENDAR_LOOKAHEAD_MINUTES` (90),
`TOKEN`, `HA_WEBHOOK_SECRET`, `NTFY_BASE_URL`, `NTFY_TOPIC`, `NTFY_TOKEN`,
`THREAD_URL_TEMPLATE`, `AEGRA_BASE_URL`; plus
`EVE_TOOLS_CALDAV_CREDENTIALS_JSON` on `eve-tools`.

`EveState` is unchanged. Ambient state is relational, not graph state.

---

## 9. Observability

Ambient runs are ordinary Aegra runs, so Langfuse traces them with the
member's identity already attached — no new instrumentation. What the trace
does not show is the part that happens before a run exists, so `eve-ambient`
logs one structured line per signal: source, key, filter verdict and `why`,
audience after the permission gate, which gate stopped it, and the resulting
thread id when one was created. That line is the difference between "Eve is
too noisy" being a diagnosable claim and an argument.

---

## 10. Deployment

`Dockerfile.eve-ambient`, mirroring `Dockerfile.eve-tools`, published as a
third image by the existing `.github/workflows/build.yml`. Manifests stay in
the `infrastructure` repository at `kubernetes/apps/eve-ambient/`: Deployment,
Service (ClusterIP), ExternalSecret, a Gatus check on `/healthz`, and an
internal-only Ingress if Home Assistant cannot reach a ClusterIP directly.

**One replica, deliberately.** The poller has no leader election. The
`ON CONFLICT DO NOTHING` on `eve_ambient_seen` makes a double-fire mostly
harmless, but two replicas would double-count the daily cap. The constraint is
written into the manifest as a comment rather than left to be discovered.

---

## 11. Testing

**Unit** (no network, no services): per-source `Signal` normalization against
recorded payloads; the gate chain against faked filter verdicts — permission
drop, cap, quiet hours across two timezones and a midnight boundary, urgent
bypass; cooldown; the `NOTHING` veto path; ntfy payload shape via `respx`; and
the new auth mode — valid token, wrong token, under-length token refused at
startup, unknown subject, and `x-eve-on-behalf-of` ignored when the caller
authenticated as an ordinary member.

**Integration** (real Postgres, live `aegra serve`): the three tables migrate
cleanly; `eve-ambient` creates a thread as a member using the service token;
that member can read it; another member still gets 404.

**Live** (`EVE_LIVE_TESTS=1`): one fabricated Home Assistant webhook driving a
real REFLEX verdict, a real `eve` turn, and a real push to a test ntfy topic.

---

## 12. Definition of done

| # | Criterion |
|---|---|
| 1 | A calendar event an hour out produces exactly one notification, in Eve's voice, in a thread the member can reply in. |
| 2 | A Home Assistant webhook produces a notification without waiting for a poll. |
| 3 | A signal the filter rejects, and a signal Eve vetoes with `NOTHING`, both produce no push and leave no thread behind. |
| 4 | Quiet hours suppress a normal signal and pass an urgent one; the bypass is visible in the logs and in the notification title. |
| 5 | The daily cap holds per member, evaluated in that member's own timezone. |
| 6 | A member without the `finances` permission never receives a finances notification, even when the filter names them. |
| 7 | A family member's own token cannot impersonate another member. |
| 8 | `eve-tools`, Aegra, or ntfy being unreachable loses no signal permanently and never kills the poll loop. |
| 9 | With `EVE_AMBIENT_ENABLED=false`, the deployment starts, serves `/healthz`, and sends nothing. |

---

## 13. Decision records

| ADR | Change |
|---|---|
| 0006 | **Upheld.** `eve-ambient` holds no third-party credential; the new CalDAV client lives in `eve-tools` with the others. |
| 0002 | **Untouched.** The latency contract governs the request path. Ambient runs outside it and contends with nothing. |
| — | **New (ADR 0007).** Ambient runs impersonate family members through a single scoped service token rather than per-member identities, and thread ownership is the reason: Aegra scopes threads to the authenticated identity, so a proactive message that a member can reply to must be created as that member. To be written up when implementation lands, matching how Phases 2 and 3 finalized their ADRs post-implementation. |

---

## 14. How Phase 5 attaches

- **Eve-authored memory rules and skills** gain an obvious first subject:
  `eve_ambient_notice` plus reply behaviour is the record of which
  interruptions were worth making. This phase writes that history and reads
  none of it.
- **The eval harness** gets a second dataset shape — signal in, notify-or-not
  out — which is cheaper to evaluate than conversational quality and directly
  tied to the failure everyone notices.
