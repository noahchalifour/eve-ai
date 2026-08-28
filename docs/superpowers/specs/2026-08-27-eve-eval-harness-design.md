# Eve Phase 5b — Eval Harness — Design

**Date:** 2026-08-27
**Status:** Approved, not yet implemented.
**Scope of this document:** Phase 5b — the measurement layer that tells you
whether Eve's self-authored behaviour is making her better or worse. Program
context lives in
[`2026-08-17-eve-core-design.md`](2026-08-17-eve-core-design.md); the memory
mechanics in [`2026-08-18-eve-memory-design.md`](2026-08-18-eve-memory-design.md);
the tools loop in
[`2026-08-21-eve-specialists-design.md`](2026-08-21-eve-specialists-design.md);
the ambient filter in
[`2026-08-23-eve-ambient-design.md`](2026-08-23-eve-ambient-design.md); and
self-authored rules and procedures in
[`2026-08-27-eve-self-improvement-design.md`](2026-08-27-eve-self-improvement-design.md).
All five are assumed throughout and not repeated.

**Delivers:** the safety net the program spec promises Phase 5 — "its safety
net is an eval harness over Langfuse datasets" (§13 of the core design). When
this phase ships, "did that change make Eve worse" is a command, not an
argument.

---

## 1. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Langfuse reachable with a project key pair that can write datasets and dataset runs. The keys Phase 1 already provisions (`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`) carry this scope. | Met |
| P2 | The `langfuse` Python SDK added to `pyproject.toml` dependencies. Phase 1 gets its tracing from LiteLLM's `langfuse_otel` callback and Aegra's native emission, so no SDK is present today. | Not started |
| P3 | Phase 5a shipped, with at least a few authored rules in the database. An A/B over an empty rule set measures nothing. | Blocked on 5a |
| P4 | Phase 4 ambient enabled long enough to have produced `eve_ambient_notice` rows. | Not started |

P1 and P2 are unblocking work. P3 and P4 gate *useful output*, not
implementation: every scorer, the runner, and the gate can be built and
tested against fixtures.

---

## 2. What a safety net has to mean

Three things, in order of how badly they fail when missed:

1. **It answers the question 5a raises.** Phase 5a lets Eve write standing
   instructions for herself. The question that creates — "is the rule set
   helping, or has it accumulated into a contradictory mess?" — is not
   answerable by reading rows. §6.2's A/B is the whole reason this phase
   exists, and everything else here is scaffolding for it.
2. **It costs almost nothing to run.** Both model providers behind the
   `VOICE`, `DEEP`, `MECHANICAL`, and `CODE` tiers are subscription proxies
   with a shared `max_budget: 20` per 30 days (core design §2.1). A harness
   that spends Noah's own working quota gets run once and then never again.
   §6.3 is what keeps it cheap.
3. **A red result is trustworthy.** A flaky harness is worse than none: it
   trains you to ignore it. §7 puts the failure threshold above the noise
   floor rather than at zero.

### 2.1 Non-goals

- **No CI gate.** The harness makes paid, nondeterministic model calls. Wiring
  it to block merges buys flaky builds and a budget bill. It runs on demand
  and on a weekly schedule (§7.2).
- **No general conversational-quality score.** "Is Eve a good assistant" is
  not measurable at this scale with five users and no annotators. Every scorer
  here answers a narrow, checkable question.
- **No human annotation workflow.** No labelling UI, no inter-annotator
  agreement. Labels come from production behaviour (§5) or from a small
  hand-written golden file.
- **No automated remediation.** The harness reports; it does not retire rules
  by itself. §8 is the one exception and it is separately gated and advisory.
- **No trace-mining.** The harness does not reconstruct inputs by reading
  Langfuse traces. See §4.1.
- **No replacement of Phase 1's tracing.** LiteLLM's `langfuse_otel` callback
  and Aegra's native span emission are untouched. This phase adds datasets and
  runs alongside them.

---

## 3. Architecture overview

```
   Postgres (Eve's own tables)              a small golden file
   eve_ambient_decision + eve_memory        tests/eval/turns.yaml
   (+ notice.replied_at)
                  |                                  |
                  +----------------+-----------------+
                                   |
                          build  (eve-eval build)
                                   |
                            dataset items
                                   |
                          run    (eve-eval run)
                                   |
                    +--------------+--------------+
                    |                             |
              replay the real                 score
              code path                       (deterministic | REFLEX judge)
              filter.judge() or                   |
              the compiled graph                  |
                    |                             |
                    +--------------+--------------+
                                   |
                    +--------------+--------------+
                    |                             |
              eve_eval_run                  Langfuse dataset run
              (local, authoritative)        (best-effort, for the UI)
                                   |
                          gate   (eve-eval gate)
                                   |
                          exit 0 or exit 1
```

One new console script, `eve-eval`, with three subcommands. No new service, no
new pod, nothing in the request path. The harness is an operator tool that
imports Eve's own modules and calls them directly.

### 3.1 Module layout

```
src/eve/eval/
  __init__.py
  types.py      # DatasetItem, ItemResult, RunScore — shapes only
  datasets.py   # build the two dataset shapes from Postgres and the golden file
  replay.py     # run one item through the real code path
  scorers.py    # deterministic scorers and the REFLEX judge
  store.py      # every eve_eval_run SQL statement
  publish.py    # best-effort Langfuse dataset + run upload
  cli.py        # eve-eval build | run | gate
```

Dependency order is `types` -> everything; `datasets` and `store` depend on
`eve.memory.db`; `replay` depends on `eve_ambient.filter` and `eve.graph`;
`scorers` depends on `eve.models`; `cli` depends on all of them. Nothing in
`src/eve/` outside this package imports from it, so the harness cannot affect
a production turn.

Two changes land **outside** this package, and they are the phase's only
production-code edits:

- `record_decision` goes in `src/eve_ambient/store.py`, which already owns
  every `eve_ambient_*` statement, and is called from `pipeline.handle_signal`
  (§4.2). Putting it in `eve/eval/` would make the ambient pipeline import the
  harness, inverting the dependency the paragraph above relies on.
- The `replied_at` UPDATE goes in `eve/memory/extract.py` beside the existing
  extraction writes (§5), reusing the ambient-marker constant Phase 5a moved
  into `eve/state.py`.

---

## 4. Where inputs come from

### 4.1 Postgres, not Langfuse traces

The program spec says "eval harness over Langfuse datasets," and this design
keeps the datasets in Langfuse while **building them from Eve's own tables**.
That is a deliberate refinement, not a drift.

Reconstructing an eval input from a trace means parsing a span tree that
exists for human debugging, whose shape is set by Aegra and LiteLLM rather
than by us, and which changes whenever either is upgraded. `eve_ambient_notice`
and `eve_memory` are tables this repository owns, with the labels already in
them. Langfuse is where results *go* — for the run-over-run comparison UI that
answers "is this worse than last week" without us building a dashboard.

The consequence, stated plainly: Langfuse is a **publishing target, not a
dependency**. §7.1 makes the gate work when Langfuse is down.

### 4.2 Shape 1 — ambient decisions

This is the cheap, high-value shape the ambient design predicted: "a second
dataset shape — signal in, notify-or-not out — which is cheaper to evaluate
than conversational quality and directly tied to the failure everyone
notices" (§14 of the ambient design).

**Phase 4's tables cannot supply it.** This is worth stating explicitly
because the ambient design's promise reads as though they can:

- `eve_ambient_seen` holds `(source, key, last_seen_at)` and nothing else. A
  suppressed signal leaves no record of *what* it was or *why* it was
  suppressed.
- `eve_ambient_notice` holds `(member_sub, source, key, urgent, thread_id,
  sent_at)`. It records that Eve interrupted, but not the `Signal` that
  caused it — no `summary`, no `payload`, no `occurred_at`.

Replaying `filter.judge()` needs a whole `Signal`
([`types.py`](../../../src/eve_ambient/types.py)), and neither table has one.
The verdict Eve reached exists only in a log line
([`pipeline.py`](../../../src/eve_ambient/pipeline.py)'s `_resolved`), which is
right for diagnosis and wrong as a dataset source.

So this phase records the decision. One new table, one insert, at the one point
in the pipeline where a verdict exists:

```
verdict = await judge(signal)        # pipeline.handle_signal
    -> record_decision(signal, verdict)
```

Immediately after `judge()` returns, before the gate chain runs. That placement
is deliberate: the dataset's label is **the filter's verdict**, not the
eventual outcome. A signal the filter approved and the daily cap then
suppressed is still a `notify=true` decision, and scoring it as a suppression
would make the harness measure the cap instead of the filter. The two paths
that resolve without a verdict — `stale` and a `FilterError` defer — record
nothing, because there is no decision to record.

| Field | Source |
|---|---|
| `input` | The full `Signal`: source, key, occurred_at, member_sub, summary, payload |
| `expected` | The production `FilterVerdict`: notify, audience, urgent, why |
| `replied` | Joined from `eve_ambient_notice.replied_at` (§5) |

Scoring against the recorded verdict is exact comparison. No judge, no cost.

### 4.2.1 What the recorded verdict is worth

It is a **consistency** label, not a correctness one: it says what Eve decided,
not what she should have decided. That makes it genuinely useful for the
question this harness is most often asked — "did that prompt edit or model
retier change the filter?" — and useless for "is the filter any good." §5 adds
the one weak correctness signal available, and §6.4 keeps the two apart.

### 4.3 Shape 2 — turn behaviour

One item per entry in a hand-written `tests/eval/turns.yaml`:

```yaml
- id: budget-caveats
  member: noah
  message: "What's left in the grocery budget?"
  expects:
    - the response leads with a number
    - the response does not open with a caveat or disclaimer
```

Small and hand-authored on purpose — a dozen or two items covering the
behaviours the family actually cares about. `expects` are natural-language
assertions scored by the judge in §6.1.

This file is checked into the repository and reviewed like code, because it is
the definition of "working" that the A/B in §6.2 measures against. Generating
it from production turns would make it drift with the behaviour it is supposed
to pin.

---

## 5. The label the ambient shape is missing

A notification's ground truth is whether the member cared. Phase 4 already
delivers every notification into a real thread the member can reply in
(ambient design §6.2) — so **a reply is the label.** A member who answers the
notification found it worth receiving; one who never opens the thread did not.

Detecting a reply needs no new service. A turn in an ambient thread whose
human message does *not* carry the ambient marker is, by definition, a member
speaking into a thread Eve started. The graph already runs on that turn, and
`extract` already holds the `thread_id`. So one statement, best-effort,
alongside the existing extraction writes:

```sql
UPDATE eve_ambient_notice SET replied_at = now()
 WHERE thread_id = %(thread)s AND replied_at IS NULL
```

No lookup is needed first: a thread with no matching row is not an ambient
thread, and the UPDATE affects nothing. The marker constant Phase 5a moved
into `src/eve/state.py` is the same one that gates this, so the reply check
and the authoring guard cannot decouple.

### 5.1 What this label is and is not

It is a **weak positive signal**, and the spec should not pretend otherwise:

- A reply means the interruption was worth making. High confidence.
- **No reply does not mean it was not.** "Your 3pm moved to 4pm" is a perfect
  notification that nobody needs to answer. Treating silence as a negative
  label would train the harness to prefer notifications that provoke
  conversation, which is the opposite of what a good notification does.

So `replied_at` scores **precision on the notify=true items only**: of the
notifications Eve sent, what fraction earned a reply. Suppressed signals are
scored against Eve's own past verdict for *consistency* (§6.4), never against
a reply that could not exist. Recall — the notification Eve should have sent
and didn't — is not measurable from production data at all, and this phase
does not claim to measure it.

### 5.2 Only forward-looking

Both halves of shape 1 begin at deploy. `eve_ambient_decision` (§4.2) has no
history to backfill — the signals are gone — and `replied_at` is populated only
from the moment the UPDATE ships. Every `eve_ambient_notice` row written before
then is permanently unlabelled and excluded.

So shape 1 is empty on day one and thin for a few weeks. That is a real cost of
not having designed the recording into Phase 4, and the honest sequencing
consequence is that **shape 2 and the §6.2 A/B are what this phase delivers
first**; shape 1 becomes useful later, on its own schedule. The gate handles an
empty dataset by skipping it rather than passing it (§11).

---

## 6. Scoring

### 6.1 The judge

Shape 2's `expects` assertions need a model. The judge receives one assertion
and one response and returns a boolean plus one sentence, through the same
structured-output mechanism `filter.py` and `extract.py` already use.

**The judge runs on `REFLEX`, not `DEEP`.** `REFLEX` is the metered Gemini
route; every other tier is a subscription proxy sharing Noah's own quota
(core design §2.1). A judge is a narrow classification task — "does this
response lead with a number" — which is what flash-lite is good at, and
running it on `DEEP` would make the harness the most expensive thing in the
deployment.

> ponytail: REFLEX-tier judge, a weak model on a narrow question. If §10's
> spot-check agreement falls below ~85%, move the judge to `DEEP` and accept
> the budget cost — the tier is a one-line change in `scorers.py`.

§10 requires a recorded spot-check of judge agreement precisely so that
decision is made on a number.

### 6.2 The A/B that justifies Phase 5a

This is the centrepiece. Shape 2 runs **twice** against the same items:

| Arm | Rules |
|---|---|
| `with-rules` | Normal. Authored `rule`-layer memory is rendered into the system prompt. |
| `without-rules` | The rule section is suppressed. Everything else — profile, household, episodic, digest, persona — is identical. |

The delta between the two arms is the measured effect of everything Eve has
written about herself. Positive: self-authoring is working. Flat: it is
costing prompt budget for nothing. **Negative: the rule set has turned on
itself, and §8 or a manual `eve-skill revoke` is the response.**

Suppression is a parameter on `build_system_prompt`, which already takes the
bundle and decides what to render
([`context.py:47`](../../../src/eve/context.py)). The flag lives in the eval
package and is passed by the runner; production code paths never set it, and
§11 tests that the default is "render."

Two arms doubles the model calls for shape 2, which is why shape 2 is a dozen
items and not a thousand.

### 6.3 Keeping it cheap

| Lever | Effect |
|---|---|
| Judge on `REFLEX` | Metered, not subscription. Does not touch the `max_budget: 20`. |
| Shape 2 is hand-sized (~12–24 items) | The only shape that spends `VOICE` calls, twice per item for the A/B. |
| Shape 1 needs no judge at all | Exact boolean comparison. Free. |
| `--limit` on `eve-eval run` | Default caps shape 1 at 200 items; a full-history run is opt-in. |
| Weekly, not per-commit | §7.2. |

`eve-eval run` prints its estimated `VOICE`-tier call count before starting and
requires `--yes` to proceed past a configurable ceiling. A harness that can
silently spend the month's budget is a harness that will.

### 6.4 The scorer set

| Scorer | Shape | Cost | Question |
|---|---|---|---|
| `notify_agreement` | 1 | free | Does a replayed `filter.judge()` reach the same verdict as production did? A drop means a prompt or model change moved the filter. |
| `notify_precision` | 1 | free | Of notifications sent, what fraction earned a reply (§5.1)? |
| `audience_exact` | 1 | free | Does the replayed audience match, member for member? Catches a permission or roster regression. |
| `assertion_pass` | 2 | REFLEX | Fraction of `expects` assertions the judge marks satisfied. |
| `rule_delta` | 2 | derived | `assertion_pass(with-rules) - assertion_pass(without-rules)`. The §6.2 number. |

`notify_agreement` deserves a note: it is a **consistency** scorer, not a
correctness one. It compares Eve to her own past self, so it detects drift
from a prompt edit or a model retier without needing to know which verdict was
right. That makes it the most useful scorer in the set for the least money, and
the easiest to misread as an accuracy figure. The CLI labels it accordingly.

---

## 7. The gate

### 7.1 Local first, Langfuse second

`eve-eval run` writes its scores to `eve_eval_run` in Postgres and *then*
attempts to publish a Langfuse dataset run. A publish failure is logged and
ignored — the same best-effort posture `extract` takes, for the same reason:
the expensive work is already done and losing it to a reporting outage is
absurd.

`eve-eval gate` reads `eve_eval_run`, compares the newest run against the
previous one on the same dataset, and exits non-zero on a regression. It never
calls Langfuse. So the gate works with Langfuse down, and Langfuse remains the
place a human looks at history.

### 7.2 Thresholds and when it runs

| Scorer | Fails the gate when |
|---|---|
| `notify_agreement` | Drops more than 10 points from the previous run |
| `audience_exact` | Drops at all — a permission or roster regression is never noise |
| `assertion_pass` (`with-rules`) | Drops more than 10 points |
| `rule_delta` | Goes negative |

Ten points is above the noise floor of a nondeterministic replay over a couple
of hundred items and below any change worth investigating; it is a starting
number to be tuned once two or three runs exist, which is why it is a setting
and not a constant. `audience_exact` is exact because the thing it guards —
a member receiving a notification they lack the permission for — is the
failure Phase 4's definition of done treats as unacceptable.

It runs on demand, and weekly via a `CronJob` in the `infrastructure`
repository. Weekly because the thing being measured is a slow accumulation of
authored rules, and a daily run would spend four times the money to watch the
same number not move.

---

## 8. Rule-set hygiene

A harness that only reports leaves the operator to hand-revoke twenty rules.
The one automated response worth building operates on Eve's own rows rather
than on model behaviour, so it is cheap and checkable:

`eve-eval hygiene` reports, and with `--apply` acts on, three conditions:

| Condition | Detection | Action |
|---|---|---|
| Duplicate rules | Two `rule` rows in one scope with cosine similarity above a threshold, using embeddings that already exist | Supersede the weaker (lower salience) by the stronger |
| Contradictory rules | A `REFLEX` pass over the rule set for a given scope, asked only "do any two of these conflict" | **Report only.** Never auto-applied. |
| Dead rules | A `rule` row whose `last_seen_at` has not moved in `EVE_EVAL_DEAD_RULE_DAYS` and which never appeared in a passing assertion | Report only |

Duplicates are auto-applicable because "these two sentences mean the same
thing" is a claim a vector comparison can make without a model, and the loser
is superseded rather than deleted, so a wrong merge is recoverable from the
`superseded_by` chain.

Contradictions are report-only on purpose. Resolving a conflict means choosing
which of two things the family wants, and a flash-lite model picking between
them unattended is exactly the silent degradation this whole phase exists to
prevent.

`EVE_EVAL_HYGIENE_APPLY_ENABLED` gates `--apply` and defaults to `false`.

### 8.1 What this is not

This is not the reflection loop deferred in 5a §1.1. It does not read traces,
does not author new rules, and does not judge whether a rule is *good* — only
whether it is redundant, conflicting, or dormant. Eve authoring rules about
her own authoring remains out of scope for Phase 5 entirely; nothing in the
program requires it, and it is the point at which the audit trail stops
having a human anywhere in it.

---

## 9. State and configuration changes

### 9.1 Tables

One migration entry, `0005_eval`, containing all three changes:

```sql
ALTER TABLE eve_ambient_notice ADD COLUMN IF NOT EXISTS replied_at timestamptz;

-- Every filter verdict, with the Signal that caused it (§4.2). Phase 4
-- records neither: eve_ambient_seen keeps only (source, key), and
-- eve_ambient_notice keeps no signal content, so a replayable dataset item
-- cannot be reconstructed from either.
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
```

`scores` is `jsonb` rather than a column per scorer because the scorer set is
expected to change and a schema migration per new metric is machinery for a
table read by one CLI. The index is the exact lookup `eve-eval gate` performs.

`eve_ambient_decision` is the one table here that grows on its own — one row
per judged signal, at a five-minute poll across four sources, indefinitely. The
weekly CronJob (§9.3) prunes rows older than
`EVE_EVAL_DECISION_RETENTION_DAYS` in the same run. A dataset built from a
year-old signal is not measuring the current filter anyway, so retention costs
nothing the harness wanted.

This brings `MIGRATIONS` to **five** entries — precisely the threshold at
which [`db.py:11`](../../../src/eve/memory/db.py) says to move to Alembic.
All three changes are folded into one entry to stay at five rather than seven.
Phase 5c adds a table and crosses the line; **the Alembic migration is 5c's
first task**, not a loose end here.

### 9.2 Settings

| Setting | Default | Purpose |
|---|---|---|
| `EVE_EVAL_DATASET_LIMIT` | `200` | Shape 1 item cap per run (§6.3). |
| `EVE_EVAL_VOICE_CALL_CEILING` | `60` | Above this, `eve-eval run` requires `--yes`. |
| `EVE_EVAL_REGRESSION_POINTS` | `10` | Gate threshold (§7.2). |
| `EVE_EVAL_DEAD_RULE_DAYS` | `90` | Dormancy window (§8). |
| `EVE_EVAL_DECISION_RETENTION_DAYS` | `180` | `eve_ambient_decision` pruning (§9.1). |
| `EVE_EVAL_HYGIENE_APPLY_ENABLED` | `false` | Gates `hygiene --apply`. |
| `EVE_LANGFUSE_HOST` | `https://langfuse.chalifour.dev` | Publishing target. Keys come from the existing Phase 1 environment. |

### 9.3 Deployment and documentation

No new service in the request path. The `infrastructure` repository gains one
`CronJob` in the existing `eve` overlay, running `eve-eval run && eve-eval
gate` weekly in the same image, with a failure surfacing through the existing
Gatus/alerting path. Everything else is the settings above.

In this repository: `README.md`'s phase table, `docs/architecture.md`'s module
map and a short harness section, `.env.example`, and
`docs/adr/0009-eval-inputs-from-postgres.md` (§12).

---

## 10. Observability

The harness is the observability, so this section is about trusting it:

| Recorded | Answers |
|---|---|
| `eve_eval_run.git_sha` | Which code produced this score. Without it, run-over-run comparison is meaningless the moment two commits land in a week. |
| `eve_eval_run.arm` | Keeps the A/B arms from being compared against each other by accident. |
| Judge spot-check, in the run output | A sample of 10 judged assertions with the judge's one-sentence reason, printed for human eyeballing. The §6.1 tier decision depends on someone actually reading these. |
| Item-level results in the Langfuse run | Which item regressed, not just that the aggregate moved. |

The failure mode this phase most plausibly has is **a green harness that
measures nothing** — assertions so loose every response passes, so
`assertion_pass` sits at 100% and `rule_delta` at zero forever. The spot-check
output is the only defence, and §11 adds a deliberately-failing canary item to
`turns.yaml` so a run where everything passes is itself a red flag.

---

## 11. Testing

| Level | What | How |
|---|---|---|
| Unit | `record_decision` fires once per judged signal, with the full `Signal` round-tripping through `jsonb`; and **not** on the `stale` or `FilterError` paths (§4.2) | pytest |
| Unit | A verdict recorded as `notify=true` stays `notify=true` in the dataset after the daily cap or quiet hours suppressed it downstream (§4.2) | pytest |
| Unit | A `record_decision` failure does not break `handle_signal` — the pipeline's existing best-effort posture for non-essential writes | pytest with a raising fake |
| Unit | Shape 1 built from fixture `eve_ambient_decision` rows, joined to `replied_at`; rows with no notice excluded from precision (§5.1) | pytest with a fake pool |
| Unit | `gate` skips an empty dataset rather than passing it (§5.2) | pytest |
| Unit | Decision pruning deletes beyond the retention window and nothing inside it | pytest |
| Unit | `notify_precision` counts only `notify=true` items; silence is never a negative label (§5.1) | pytest |
| Unit | The reply UPDATE fires for a member turn in an ambient thread, and **not** for a turn carrying the ambient marker | pytest |
| Unit | `build_system_prompt` renders rules by default; suppression only when explicitly asked (§6.2) | pytest |
| Unit | Each scorer against hand-built results, including empty and all-fail inputs | pytest |
| Unit | The judge treats a malformed structured-output response as a fail, not a crash, mirroring `filter.py`'s malformed-response handling | pytest with a fake model |
| Unit | `gate` exits non-zero per each §7.2 threshold, and exits zero on a first run with no previous to compare | pytest |
| Unit | `run` refuses to exceed `EVE_EVAL_VOICE_CALL_CEILING` without `--yes` | pytest |
| Unit | A Langfuse publish failure does not fail the run or lose the local scores (§7.1) | pytest with a raising fake client |
| Unit | `hygiene` never auto-applies a contradiction; `--apply` is inert with the setting off | pytest |
| Unit | Nothing in `src/eve/` outside `eve/eval/` imports `eve.eval` | an import-graph assertion, like the existing acyclicity checks |
| Integration | `build` then `run` then `gate` end to end against real Postgres and a fake model, twice, with a seeded regression between runs | `docker-compose.test.yml` |
| Live | One `eve-eval run` against the real Langfuse and real models, recording the judge spot-check | marked `live`, run by hand |

`turns.yaml` carries one **canary** item whose assertion is written to fail
against correct behaviour. A run in which the canary passes means the judge is
rubber-stamping, and the gate fails on it. That is the only test here that
guards the harness against itself.

---

## 12. Definition of done

| # | Criterion |
|---|---|
| 0 | Every judged signal records a `eve_ambient_decision` row carrying the full `Signal` and verdict; `stale` and filter-error paths record nothing; a failure to record never breaks the pipeline. |
| 1 | `eve-eval build` produces both dataset shapes from real tables and the golden file, excluding unlabelled ambient rows, and `gate` skips an empty shape 1 rather than passing it. |
| 2 | `eve-eval run` replays shape 1 through the real `filter.judge()` and shape 2 through the real compiled graph, in both arms. |
| 3 | A member replying in an ambient thread stamps `replied_at`; an ambient-marked turn does not. |
| 4 | `rule_delta` is reported, and a seeded harmful rule makes it negative. |
| 5 | `eve-eval gate` exits non-zero on each §7.2 regression and zero on a clean run, with Langfuse unreachable. |
| 6 | A run stays under `EVE_EVAL_VOICE_CALL_CEILING` unless `--yes` is passed, and prints its estimate first. |
| 7 | The judge spot-check appears in run output with reasons, and agreement is recorded once against a human read. |
| 8 | The canary item fails as designed; a run where it passes fails the gate. |
| 9 | `eve-eval hygiene` finds a seeded duplicate rule and, with the setting on, supersedes the weaker; a seeded contradiction is reported and never applied. |
| 10 | `MIGRATIONS` has exactly five entries, and no production code path imports `eve.eval`. |

---

## 13. Decision records

| ADR | Change |
|---|---|
| 0002 (No LLM before first token) | **Untouched.** The harness runs entirely outside the request path. The one production-code change it makes — the `replied_at` UPDATE — runs in `extract`, after the answer has streamed. |
| 0004 (Model tier routing) | **Upheld, and load-bearing.** The judge is on `REFLEX` specifically because the ADR's tier separation makes "metered vs. subscription quota" a one-line choice (§6.1). |
| 0005 (Memory storage) | **Vindicated.** The `superseded_by` history the ADR preserved "for Phase 5's eval harness" is what §8's duplicate merge is recoverable through, and what makes rule revisions countable. |
| 0007 (Ambient impersonation) | **Untouched.** Shape 1 replays `filter.judge()`, which runs before any impersonated call. The harness never creates a thread or delivers anything. |
| — | **New (ADR 0009).** Eval inputs are built from Eve's own Postgres tables, not reconstructed from Langfuse traces; Langfuse is a publishing target and never a dependency of the gate (§4.1, §7.1). The corollary is that a subsystem wanting to be evaluated must *record* its decisions, not just log them — which is why this phase adds `eve_ambient_decision` rather than parsing `pipeline.py`'s log line. |

### 13.1 One promise from Phase 4 refined

The ambient design's §14 says Phase 5 gains "learned interrupt-worthiness" —
Eve tuning her own filter from which notifications got a reply. **This phase
measures that signal and deliberately does not act on it.** §5.1 is the
reason: a reply is a weak positive with no usable negative, so a loop
optimising against it would learn to prefer notifications that provoke
conversation over notifications that inform. Ambient caps, quiet hours, and
watched entities stay operator configuration, as Phase 4 has them. Phase 5c
§15 records this as a permanent boundary of the program rather than a deferral.

---

## 14. How 5c attaches

- **The gate becomes the approval aid.** 5c asks a human to approve executable
  code. "The eval suite is green on this commit" is a materially better basis
  for that decision than reading a diff at 11pm, and `eve-eval gate` is what
  produces it.
- **Alembic is 5c's first task.** §9.1 leaves `MIGRATIONS` at exactly the five
  entries `db.py` names as the threshold; 5c's `eve_tool` table is the sixth.
- **A third dataset shape is available but not required.** A sandboxed tool is
  a pure function (5c §6.2), so its inputs and outputs are exactly-comparable
  with no judge and no cost — the cheapest scorer in the program. 5c specs
  whether it wants one.
