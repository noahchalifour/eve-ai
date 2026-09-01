# Eve — Health Coach Specialist — Design

**Date:** 2026-09-01
**Status:** Approved, not yet implemented.
**Issue:** [EVE-19](https://linear.app/chalifour-development/issue/EVE-19/add-health-coach-specialist)
**Scope of this document:** one new specialist (`ask_health`), its two
eve-tools clients (WHOOP and Oura), and the writable OAuth token store they
need. The specialist factory, the permission boundary, and the eve-tools
HTTP surface all already exist and are assumed, not re-derived; they are
described in
[`2026-08-21-eve-specialists-design.md`](2026-08-21-eve-specialists-design.md).

**Delivers:** a fourth specialist alongside Home, Mail, and Finances,
answering questions about sleep, recovery, and training load from the
family's wearables.

**Does not deliver:** proactive health signals. An
`eve_ambient/sources/health.py` is deliberately out of scope (§8), but the
data shapes in §4 are designed to be what it will consume.

---

## 1. Why this is not just another specialist

Adding a specialist is normally a bounded exercise: one module calling
`build_specialist`, some handler-table entries, a `family.yaml` grant, one
line in `graph.py`. Three of the four existing specialists were exactly
that.

This one carries one genuinely new problem. **WHOOP returns a new
`refresh_token` on every refresh** — its documented refresh response is
`{access_token, refresh_token, expires_in, scope, token_type}`, and the
old refresh token cannot be relied on afterwards. Every credential
eve-tools holds today is either static (Home Assistant token, Monarch
session token) or non-rotating (Google refresh tokens), which is why
storing them all in environment variables has worked so far. A rotating
token stored in an environment variable goes stale on first use and
auth breaks permanently at the next pod restart.

So eve-tools needs somewhere writable, and
[ADR 0006](../../adr/0006-eve-tools-isolation.md) deliberately gives it no
database, no cluster credentials, and no persistent state of its own.
Resolving that is §3, and it is recorded as ADR 0016 rather than left
implicit in code.

### 1.1 Uncertainty to resolve during implementation, not now

One secondary source reports that Oura retired Personal Access Tokens in
December 2025, which would put Oura on the same OAuth footing as WHOOP.
Oura's own authentication documentation still describes two authorization
flows. This design assumes **OAuth for both providers**, which is correct
either way: if PATs still work, the Oura row simply carries a `NULL`
`refresh_token` and never refreshes, a case §3 already handles. Confirm
against the live Oura developer dashboard when provisioning (§7), but do
not block on it.

---

## 2. Scope decisions

| Decision | Choice | Rationale |
|---|---|---|
| Conversational vs proactive | Conversational now; ambient later | Smallest surface that answers the issue. The §4 shapes are designed for an ambient source to consume without reshaping. |
| Credential scoping | Per-member, keyed by OIDC sub | Noah and Kendra each have a device. Mirrors `gmail_credentials_json` / `caldav_credentials_json`, which are already keyed by sub. |
| Provider abstraction | Normalized, provider-agnostic | Neither the specialist nor a future ambient source branches on provider. Costs a normalizer per provider (§4). |
| Raw passthrough | None | A `health.raw(provider, endpoint)` escape hatch is YAGNI until the normalizer is demonstrably a ceiling. Addable later without breaking the normalized surface. |
| Coaching posture | Coach, with a clinical guardrail | Guidance on training, rest, and sleep grounded in the returned numbers; explicitly no diagnosis, symptom interpretation, or medical advice (§5). |
| Feature flag | None | `ask_health` is unconditional, like the three existing specialists. `write_skill`, `propose_tool`, and `dispatch_computer_task` are switched because they *act*; three read tools that degrade cleanly when no device is connected do not need a kill switch. |

---

## 3. The OAuth token store

### 3.1 Table

Created by Eve's Alembic, revision `0005_eve_oauth_token`, against the
private `eve_alembic_version` table
([ADR 0011](../../adr/0011-alembic-with-a-private-version-table.md)).
eve-tools never runs migrations.

```sql
CREATE TABLE eve_oauth_token (
  provider      text        NOT NULL,   -- 'whoop' | 'oura'
  member_sub    text        NOT NULL,
  access_token  text        NOT NULL,
  refresh_token text,                   -- NULL for a non-rotating provider
  expires_at    timestamptz,            -- NULL means "does not expire"
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (provider, member_sub)
);
```

No index beyond the primary key: every read is a point lookup on the full
key.

`refresh_token` and `expires_at` are both nullable so that a long-lived
credential (an Oura PAT, if those still work) is a normal row rather than a
special case — a `NULL expires_at` means the refresh path is never entered.

### 3.2 What eve-tools is granted

New module `src/eve_tools/db.py`, holding its own `AsyncConnectionPool`
built from **`EVE_TOOLS_DATABASE_URL`** — a distinct connection string from
`EVE_DATABASE_URL`, resolving to a dedicated Postgres role granted:

```sql
GRANT SELECT, INSERT, UPDATE ON eve_oauth_token TO eve_tools;
```

No `DELETE`, no DDL, no grant on `eve_memory`, `eve_pat`, `eve_tool`,
`eve_computer_task`, or any Aegra table. `src/eve_tools/` imports nothing
from `src/eve/`, and that stays true: eve-tools does not reuse
`eve.memory.db.get_pool`.

ADR 0006's isolation claim therefore weakens from "no database" to **"one
table, its own role, no read access to anything else."** That is a real
reduction and is recorded as **ADR 0016**, amending 0006. ADR 0016 also
corrects 0006's statement that `member_sub` crossing the boundary is a
single narrow exception for `mail.*`: it is now two domains, `mail.*` and
`health.*`. The subs remain opaque, and eve-tools still learns no names,
roles, timezones, or permissions — but the ADR text should say two, not
one.

### 3.3 Refresh, and why it needs a row lock

Rotation makes concurrent refresh a correctness bug, not a performance
one. Two specialist calls landing in the same second would each refresh,
and each would rotate the other's token out from under it — leaving a
stored token that WHOOP has already invalidated, and auth broken until
someone re-runs the provisioning script. This is precisely the failure
that must not be silent.

```
BEGIN
SELECT access_token, refresh_token, expires_at
  FROM eve_oauth_token
  WHERE provider = %s AND member_sub = %s
  FOR UPDATE
-- Re-check expires_at *inside* the lock: the caller that held it a moment
-- ago may have already refreshed, in which case do nothing and use its
-- token.
-- If still stale: POST the provider's token endpoint, then
UPDATE eve_oauth_token SET access_token = ..., refresh_token = ...,
       expires_at = ..., updated_at = now()
  WHERE provider = %s AND member_sub = %s
COMMIT
```

Row-level `FOR UPDATE` rather than an advisory lock: contention is
per-member-per-provider, which is exactly the granularity the primary key
already gives. Compare `eve.memory.db`'s `_MIGRATION_LOCK`, which is
advisory because its contention is process-wide.

**Refresh triggers**, in order:

1. **Proactive** — `expires_at` is within 120 seconds of now. The skew
   window keeps a token from expiring mid-request.
2. **Reactive** — the API answered `401`. A token can be revoked
   server-side before its stated expiry. Exactly **one** retry after a
   successful refresh; a second `401` is returned as an error, not retried
   again.

A refresh that itself fails (the refresh token has been revoked, or the
member disconnected the app) returns a clear, non-retryable error naming
the provider and instructing that the member reconnect. It must not
present as "no data."

### 3.4 Client credentials

Client ID and secret do not rotate and stay in environment variables,
alongside every other eve-tools secret:

```
EVE_TOOLS_WHOOP_CLIENT_ID
EVE_TOOLS_WHOOP_CLIENT_SECRET
EVE_TOOLS_OURA_CLIENT_ID
EVE_TOOLS_OURA_CLIENT_SECRET
EVE_TOOLS_DATABASE_URL
```

---

## 4. The normalized data surface

Three entries in `eve_tools.app._HANDLERS`, all per-member, all defaulting
to the current day:

```
health.get_recovery(member_sub, days=1)
health.get_sleep(member_sub, days=1)
health.get_activity(member_sub, days=1)
```

`days` is clamped to 1..14 by the eve-tools handler, not trusted from the
caller. Trend questions ("has my sleep been getting
worse?") are the reason it exists at all; beyond two weeks the answer
belongs to a chart, not a conversation.

Each returns a flat list, one entry per day per source. A member with both
devices yields two entries per day, each labelled by `source`, and the
specialist reports both rather than silently preferring one.

```python
{"recovery": [
    {"date": "2026-09-01", "source": "whoop",
     "score_0_100": 68, "hrv_ms": 84.2, "resting_hr": 51,
     "temp_deviation_c": None},
]}

{"sleep": [
    {"date": "2026-09-01", "source": "oura",
     "score_0_100": 81, "hours": 7.4, "deep_hours": 1.2, "rem_hours": 1.8,
     "efficiency_pct": 92, "hrv_ms": 61.0, "resting_hr": 48},
]}

{"activity": [
    {"date": "2026-09-01", "source": "whoop",
     "score_0_100": None, "strain_0_21": 14.2, "active_calories": 812,
     "steps": None,
     "workouts": [{"sport": "cycling", "duration_min": 62, "avg_hr": 138}]},
]}
```

### 4.1 The normalizer's one hard contract

**`None` means "this provider does not measure this." It never means
zero.**

WHOOP has no step count; Oura has no strain score. A `0` in either field
would have the coach reporting that you took no steps, and would poison
any threshold a future ambient source sets. Every consumer — the
specialist prompt included (§5) — is written against this contract.

### 4.2 Field mapping

| Normalized field | WHOOP v2 | Oura v2 |
|---|---|---|
| `recovery.score_0_100` | `/v2/recovery` → `score.recovery_score` | `daily_readiness` → `score` |
| `recovery.hrv_ms` | `score.hrv_rmssd_milli` | not in readiness — join `sleep.average_hrv` |
| `recovery.resting_hr` | `score.resting_heart_rate` | join `sleep.lowest_heart_rate` |
| `recovery.temp_deviation_c` | `None` — WHOOP reports absolute `skin_temp_celsius`, not a deviation | `daily_readiness.temperature_deviation` |
| `sleep.score_0_100` | `score.sleep_performance_percentage` | `daily_sleep.score` |
| `sleep.hours` | `stage_summary.total_in_bed_time_milli` minus `total_awake_time_milli` | `sleep.total_sleep_duration` |
| `sleep.deep_hours` | `stage_summary.total_slow_wave_sleep_time_milli` | `sleep.deep_sleep_duration` |
| `sleep.rem_hours` | `stage_summary.total_rem_sleep_time_milli` | `sleep.rem_sleep_duration` |
| `sleep.efficiency_pct` | `score.sleep_efficiency_percentage` | `sleep.efficiency` |
| `activity.strain_0_21` | `/v2/cycle` → `score.strain` | `None` |
| `activity.active_calories` | `/v2/cycle` → `score.kilojoule`, converted (kJ ÷ 4.184) | `daily_activity.active_calories` |
| `activity.steps` | `None` | `daily_activity.steps` |
| `activity.score_0_100` | `None` | `daily_activity.score` |
| `activity.workouts` | `/v2/activity/workout` | `daily_activity` has no per-workout breakdown → `[]` |

`temp_deviation_c` is deliberately `None` for WHOOP rather than carrying
`skin_temp_celsius`: an absolute skin temperature and a deviation from
baseline are different quantities, and putting one in the other's field
would be the normalizer lying.

WHOOP hosts these under `https://api.prod.whoop.com/developer`. Oura hosts
under `https://api.ouraring.com/v2/usercollection`.

**Oura recovery is a two-request join.** `daily_readiness` exposes only
*contributor scores*, not raw HRV or resting heart rate; those live in the
detailed `sleep` collection. WHOOP returns everything recovery needs in
one call.

### 4.3 Four failure modes to build in, not discover

1. **WHOOP recovery does not exist until the sleep cycle closes.** Queried
   at 6am before the member has woken up, `/v2/recovery` returns nothing
   for that day. This is a normal, expected outcome — not an error, and
   not "your device is broken." §5's prompt states this explicitly so the
   coach says the right sentence.
2. **WHOOP records carry `score_state`.** A record with
   `PENDING_SCORE` or `UNSCORABLE` has no `score` object at all.
   The normalizer emits `None` for its fields rather than raising
   `KeyError`. Same defensive posture as `monarch.get_budgets`' `_is_number`
   and non-dict guards, and for the same reason: an upstream shape
   surprise should drop one day, not the whole answer.
3. **Day attribution without a timezone.** eve-tools knows member subs but
   not timezones, and this design does not start passing `timezone` across
   that boundary — that would be new roster data crossing it for the sake
   of date arithmetic. It does not have to: Oura returns a local `day`
   string, and WHOOP records carry their own `timezone_offset`. Use the
   provider's own day attribution. Deriving the date from a UTC instant
   would misfile every Vancouver night beginning after 5pm local.
4. **A member with no connected device** returns
   `{"recovery": [], "unconfigured": ["whoop", "oura"]}` — structurally
   distinct from a connected device with no data yet, which is a different
   sentence for the coach to say. The list key is named for its tool
   (`recovery` / `sleep` / `activity`), but `unconfigured` behaves
   identically on all three and names only the providers actually
   missing a row — one connected device yields `["oura"]`, not both. It
   is omitted entirely when every provider has a row.

### 4.4 Pagination and rate limits

Both providers paginate with a `next_token`. At `days <= 14` a single page
with an explicit `limit` covers every request this surface makes; the
clients pass `limit` and do not implement pagination. WHOOP's documented
limit is 100 requests/minute, far above anything a conversational surface
generates.

Both clients are plain `httpx` against documented REST APIs. Neither
resembles the Monarch situation — no reverse-engineered GraphQL, no
community package to pin, no base-URL monkeypatch.

---

## 5. The specialist

`src/eve/specialists/health.py`, built by `build_specialist` exactly as
`finances.py` is. Tools read the member from
`config["configurable"]["member"]`, the pattern `mail.py` established for
per-member calls (`base.py` puts it there). `config` comes first in the
signature because `days` carries a default and Python forbids a
non-defaulted parameter after one; `@tool` excludes `RunnableConfig`-annotated
parameters from the tool schema regardless of position.

```python
@tool
async def get_recovery(config: RunnableConfig, days: int = 1) -> str:
    """Recovery score, HRV, and resting heart rate for recent days."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke("health.get_recovery",
                        {"member_sub": member_sub, "days": days})
```

…and likewise `get_sleep` and `get_activity`. No per-tool permission
check: all three are reads, so unlike `mail.send` there is nothing to gate
beyond the coarse `ask_health` boundary.

### 5.1 System prompt

> You are the family's health coach. You answer questions about sleep,
> recovery, and training load using WHOOP and Oura data.
>
> State every number exactly as returned; never estimate or interpolate. A
> null field means that device does not measure it — say so rather than
> treating it as zero. An empty recovery result early in the morning means
> last night's sleep has not been scored yet, which is normal; say that
> rather than reporting a problem. If a member has two devices, report
> both rather than choosing between them.
>
> You give practical guidance on training, rest, and sleep habits grounded
> in these numbers. You do not diagnose, interpret symptoms, or give
> medical advice — if a question touches illness, injury, medication, or
> anything clinical, say it needs a doctor.

"State every number exactly as returned; never estimate" is lifted from
`finances.py` on purpose: a fabricated HRV reading and a fabricated dollar
amount are the same class of failure.

### 5.2 Permission

**`health`** — a bare noun, following the `finances` precedent for a
read-only domain rather than inventing `health.read`. There is no write
surface here to distinguish it from. Granted to Noah and Kendra in
`family.yaml`.

---

## 6. Files touched

| File | Change |
|---|---|
| `alembic/versions/0005_eve_oauth_token.py` | new — the table |
| `src/eve_tools/db.py` | new — eve-tools' own pool |
| `src/eve_tools/oauth_store.py` | new — read / refresh-under-lock / write |
| `src/eve_tools/whoop.py` | new — client + normalizer |
| `src/eve_tools/oura.py` | new — client + normalizer |
| `src/eve_tools/app.py` | three `_HANDLERS` entries |
| `src/eve_tools/settings.py` | five new fields (§3.4) |
| `src/eve/specialists/health.py` | new — `ask_health` |
| `src/eve/graph.py` | one entry in `_BASE_TOOLS` |
| `family.yaml` | `health` grant for Noah and Kendra |
| `scripts/health_oauth_setup.py` | new — provisioning (§7) |
| `.env.example` | five new vars |
| `docs/adr/0016-eve-tools-owns-a-credential-table.md` | new — amends 0006 |
| `docs/architecture.md` | health specialist + token store |

---

## 7. Prerequisites

Outside this repository, and both must land **before** the eve-ai release,
not with it.

| # | Prerequisite | Owner |
|---|---|---|
| P1 | WHOOP developer app registered; client ID/secret in Vault. Redirect URI must match what `scripts/health_oauth_setup.py` uses. | Noah |
| P2 | Oura developer app registered; client ID/secret in Vault. Confirm whether Personal Access Tokens still work (§1.1) while here. | Noah |
| P3 | Postgres role `eve_tools` created with exactly the §3.2 grants; `EVE_TOOLS_DATABASE_URL` added to eve-tools' `ExternalSecret` | `home-lab-infrastructure` PR |
| P4 | `NetworkPolicy` egress from eve-tools to the CNPG cluster, and to `api.prod.whoop.com` and `api.ouraring.com` | `home-lab-infrastructure` PR |

P3 and P4 are one infrastructure PR. **It is a hard prerequisite for
deployment**, not for implementation: everything in §3–§5 can be built and
unit-tested first, with the concurrency test (§9) running against
`docker-compose.test.yml`'s Postgres. Sequence the infrastructure PR early
enough that it is not discovered at deploy time.

Provisioning itself is `scripts/health_oauth_setup.py`, mirroring
`scripts/gmail_oauth_setup.py`: run locally, complete the authorization-code
flow in a browser, write the first `eve_oauth_token` row for that member and
provider. Run once per member per device. Note that a freshly registered
Oura app may be limited to a small number of users until Oura approves it —
ample for two.

---

## 8. Deliberately not in this design

- **`eve_ambient/sources/health.py`.** No proactive recovery or sleep-debt
  signals. The §4 shapes are what such a source would consume, and the
  clients' docstrings will note that seam the way
  `monarch.get_budgets` notes `eve_ambient.sources.finances`. Building it
  needs its own cooldown and audience decisions, which are a separate
  design.
- **A raw passthrough tool** (§2).
- **Writes to either provider.** Both APIs are read-only here. WHOOP and
  Oura both accept some written data; nothing asks for it.
- **Webhooks.** Both providers offer them and they would make an ambient
  source cheaper. They also need a public ingress endpoint and signature
  verification. Revisit when the ambient source is designed.
- **Historical backfill.** `days` clamps at 14. Multi-month trends are a
  charting problem.

---

## 9. Testing

| File | Covers |
|---|---|
| `tests/test_eve_tools_whoop.py` | field mapping; `PENDING_SCORE`/`UNSCORABLE` → `None`; `timezone_offset` day attribution; kJ→kcal conversion; proactive refresh on near-expiry; exactly one retry on `401` |
| `tests/test_eve_tools_oura.py` | `daily_readiness` + `sleep` join; field mapping; `None` (not `0`) for strain; `NULL refresh_token` never refreshes |
| `tests/test_eve_tools_oauth_store.py` | row read/write round trip; **two concurrent refreshes produce exactly one token rotation** |
| `tests/test_specialists_health.py` | a tool call reaches `invoke` with the right payload; permission denial short-circuits before the model is built (mirrors `test_specialists_finances.py`) |
| `tests/test_eve_tools_app.py` | the three new `_HANDLERS` entries dispatch |
| `tests/test_graph.py` | the `_BASE_TOOLS` name-set assertion (currently line 617) gains `ask_health` |

HTTP is faked with `respx`, already a dev dependency. The store's
concurrency test is the exception: it needs the real Postgres from
`docker-compose.test.yml`, because `FOR UPDATE` semantics are the thing
under test and a fake would assert nothing. It is also the test least
worth skipping — token rotation under concurrency is the failure mode that
breaks auth silently and stays broken.

---

## 10. Observability

`build_specialist` already emits `eve.specialist.called`,
`eve.specialist.permission_denied`, `eve.specialist.loop_exhausted`, and
`eve.specialist.latency_ms`; `ask_health` inherits all four with no work.

One addition worth its line, in the refresh path:
`eve.health.token_refreshed` with the provider as an attribute. A token
refreshing far more often than hourly means the skew window or the
locking is wrong, and that is not otherwise visible until auth breaks.
