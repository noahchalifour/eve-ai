# Eve Phase 2 — Memory — Design

**Date:** 2026-08-18
**Status:** Approved, not yet implemented.
**Scope of this document:** the full design for Phase 2, "Memory." Program
context, the phase decomposition, and the Phase 1 design live in
[`2026-08-17-eve-core-design.md`](2026-08-17-eve-core-design.md) (the "Phase 1
spec"), which this document assumes throughout and does not repeat.

**Delivers:** requirement R4 — "persistent memory that accumulates and improves
over time." When this phase ships, Eve remembers.

---

## 1. Prerequisites

Two, both outside this repository, both assumed complete before the first task
of the implementation plan runs.

| # | Prerequisite | Owner | State |
|---|---|---|---|
| P1 | A metered **Gemini** API key in Vault, with two LiteLLM model entries: `gemini/gemini-flash-lite-latest` (REFLEX) and `gemini/gemini-embedding-001` (embeddings) | `infrastructure` | Not started |
| P2 | `eve-db` restore exercised from S3 — Phase 1 DoD item 7 | `infrastructure` | Open |

P2 is not a task in this plan; Noah is closing it directly. It is recorded here
because this phase begins writing the one asset in Eve that cannot be rebuilt,
and an untested backup is a hypothesis. If P2 is still open when the
implementation plan reaches the first write path, that is a decision to accept
risk, not an oversight to discover later.

P1 resolves the conditional that ADR 0003 has carried since Phase 1: the REFLEX
key is Gemini, so the embedding model becomes `gemini-embedding-001` rather
than `text-embedding-3-small`. See §8.

---

## 2. What "remembering" has to mean

The failure mode this phase exists to prevent is an assistant that has a vector
database and still feels like a stranger. Three things produce the feeling of
being known, in descending order of impact:

1. **She knows the standing facts without being asked.** Dietary restrictions,
   who works which days, the dog's name, which car is whose, when the trash
   goes out. This is not a retrieval problem — the set is small and bounded and
   belongs in every prompt.
2. **She notices when a standing fact changes.** "Kendra works Tuesdays" was
   true in March and is wrong in August. A memory system that accumulates
   without superseding gets *worse* over time, confidently.
3. **She can find the thing that was decided three weeks ago.** This is the
   retrieval problem, and it is the smallest of the three.

The design allocates effort in that order, which is why two of the four layers
are always-on and cost nothing to retrieve, and why contradiction handling is a
write-time concern rather than a background reconciler.

### 2.1 Non-goals

- **No memory UI.** Inspection and correction happen in conversation, or in
  `psql`. A management surface is not a Phase 2 deliverable.
- **No recall tool.** Eve cannot yet decide to search her memory; recall is an
  unconditional step. Deliberate search belongs in Phase 3's tools loop.
- **No cross-member inference.** Nothing derives facts about Kendra from
  Noah's conversation. Shared facts are written explicitly to the household
  scope, gated by permission (§7).
- **No self-authored memory rules.** Phase 5.

---

## 3. The four layers

Layers are distinguished by *retrieval policy*, not by structure.

| Layer | Scope | Read policy | Bound | Example |
|---|---|---|---|---|
| **profile** | one member | always injected, in full | 40 items | "Noah is vegetarian." |
| **household** | shared | always injected, in full | 60 items | "The dog is Cooper, a border collie." |
| **episodic** | member or shared | hybrid retrieval, top-K | unbounded | "2026-08-14: decided to replace the dishwasher in March." |
| **digest** | one thread | always injected for that thread | 1 row | Rolling summary of the conversation so far. |

**Why bounded always-on layers rather than retrieval over everything.** Forty
short facts is roughly 400 tokens. Retrieving them costs one indexed read and
cannot miss. Any retrieval scheme over the same forty facts is strictly worse:
it can fail, it costs more, and it buys nothing. Retrieval earns its complexity
only where the corpus genuinely cannot fit, which is episodic and only episodic.

**Why the bounds are small.** A profile that grows without limit stops being a
profile and becomes an episodic log with a misleading name. The cap forces
eviction (§6.3), and eviction forces the extraction step to decide what is
actually durable — which is the judgment that makes the layer valuable.

**Learned interaction preferences** ("Noah prefers short answers, no bullet
lists") are profile rows with `kind='preference'`, not a fifth layer. They are
read and written identically; only the prompt section they land in differs. The
spec's "memory rules" — rules Eve authors about her own behaviour — are Phase 5
and are not this.

**The digest is context compaction, not memory.** It exists so a 200-message
thread does not have to be re-read every turn, and it is discarded with its
thread. It is counted as a layer because it is stored, budgeted, and injected
by the same machinery, and giving it a separate mechanism would be duplication.

---

## 4. Storage

### 4.1 One table

```sql
CREATE TABLE eve_memory (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  layer          text        NOT NULL,   -- profile | household | episodic | digest
  scope_kind     text        NOT NULL,   -- member | household | thread
  scope_id       text        NOT NULL,   -- member sub | '' | thread id
  kind           text        NOT NULL,   -- fact | preference | event | decision | digest
  subject        text,                   -- normalised entity: 'cooper', 'kendra', 'honda'
  content        text        NOT NULL,   -- ONE self-contained sentence
  confidence     real        NOT NULL DEFAULT 0.7,
  salience       real        NOT NULL DEFAULT 0.5,
  source_thread  text,
  source_run     text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  superseded_by  uuid        REFERENCES eve_memory(id) ON DELETE SET NULL,
  superseded_why text,                   -- 'contradicted' | 'evicted' | 'merged'
  embedding      vector(1536),
  content_tsv    tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
```

Four layers, one table, because they differ in policy and not in shape. Four
tables would mean four queries, four migrations, and four places to fix the
same bug.

`content` is **one self-contained sentence** by contract, enforced in the
extraction prompt and validated on write. A memory that only makes sense
alongside its neighbours cannot be ranked, budgeted, or superseded
independently, and every one of those operations is per-row.

Indexes:

```sql
CREATE INDEX ON eve_memory USING gin (content_tsv);
CREATE INDEX ON eve_memory USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON eve_memory (scope_kind, scope_id, layer) WHERE superseded_why IS NULL;
CREATE INDEX ON eve_memory (subject) WHERE superseded_why IS NULL;
```

pgvector's HNSW rather than VectorChord's `vchordrq`: the image provides both,
and `vchordrq` is built for corpora two or three orders of magnitude larger
than this one. Revisit past a million rows, which this corpus will not reach.

### 4.2 Nothing is deleted

Retirement sets `superseded_why`, and `superseded_by` when a replacement
exists. **`superseded_why IS NULL` is the live predicate** — not
`superseded_by IS NULL`, which would be wrong for an eviction, where the row
is retired and replaced by nothing. The partial indexes make retired rows
vanish from every read at no cost, and the history stays for Phase 5's eval
harness, which needs to answer "what did Eve believe on the day she got that
wrong."

The one exception is an explicit instruction to forget, which hard-deletes.
"Eve, forget I said that" has to mean the row is gone; a tombstone that still
holds the text is not forgetting, and treating it as such would be a quiet lie
to a family member about their own data.

### 4.3 Migrations and the pool

Eve owns a schema for the first time. Phase 1 added no tables and Aegra runs
its own Alembic migrations at startup; ours are separate and must not
interleave with them.

An ordered list of DDL statements in `src/eve/memory/schema.py`, applied under
a Postgres advisory lock against a `eve_schema_version` table, exposed as a
console script and run before `aegra serve` in the Dockerfile CMD. Roughly
fifty lines and no new dependency.

> `ponytail:` hand-rolled because there is exactly one table. Move to Alembic
> if this list exceeds ~5 migrations.

Running it as a distinct command rather than on import means a schema failure
fails the pod visibly at start, instead of surfacing as a confusing runtime
error on somebody's first message.

Connection pooling uses **psycopg 3** — already present transitively via
`langgraph-checkpoint-postgres`, so no new dependency — with Eve's own
`AsyncConnectionPool` (`min_size=1, max_size=5`) on `EVE_DATABASE_URL`,
defaulting to `DATABASE_URL`. Production configuration therefore does not
change.

Eve does **not** reuse Aegra's internal `db_manager.lg_pool`. It is reachable
and it is tempting, but it is a private attribute path, and a silent rename in
an aegra-api bump would break memory in production for a saving of fifteen
lines.

### 4.4 Why not Aegra's store

Aegra builds a LangGraph `AsyncPostgresStore` and injects it into every node
(`core/database.py:79`, `services/graph_factory.py:348`), and its `index`
config in `aegra.json` enables vector search with no code at all. It was the
obvious candidate and it was rejected for two reasons.

Its `search` supports namespace prefix, JSON filter, and vector query. It has
no full-text arm and no way to express recency weighting, so hybrid recall
(§5.2) is not expressible against it — the lexical half would have to be raw
SQL over the store's own table, which is reaching around the abstraction while
still paying for it. And supersession, confidence and decay would live in JSON,
so every contradiction check becomes over-fetch-then-filter-in-Python instead
of an indexed predicate.

Worth recording for later phases: **in-graph store access is unscoped.** The
`["users", <identity>]` burial documented in the Phase 1 architecture notes is
applied by the HTTP route (`api/store.py:303`), not by the store object. Nodes
get the raw store. The `store.scopes` lever is therefore only needed if a
*client* must read memory over HTTP, which §2.1 rules out for this phase.

---

## 5. The graph

```
START → load_context → recall → eve → extract → END
```

Phase 1 predicted `recall` and this is it. `extract` is new to the shape and
sits after the answer, where its latency is invisible.

### 5.1 `load_context` — unchanged

Still pure local computation, still no model call.

### 5.2 `recall`

Reads the last human message and produces a `MemoryBundle` in state. It runs
the lexical query **immediately** and races the embedding against a budget:

1. Start `embed(query)` as a task with a 120 ms budget.
2. Run the lexical query without waiting: profile and household in full,
   digest for this thread, and episodic candidates ranked by
   `ts_rank(content_tsv, query) × subject_match × recency_decay × salience`.
3. Await the embedding task with the remaining budget. On success, run the
   vector query and fuse it into the episodic candidates by reciprocal rank.
   On timeout or error, cancel the task and ship lexical-only.
4. Apply the token budget (§6.1) and return.

**A degraded turn is a complete turn.** The always-on layers are unaffected by
the vector arm, and episodic falls back to a real lexical ranking rather than
to nothing. There is no path where Gemini being slow makes Eve unresponsive or
amnesiac — only slightly less good at paraphrase matching, for that one turn.

Which path was taken is recorded as a span attribute (§9), so the degrade rate
is a number in Langfuse rather than an assumption. If it turns out to be high,
the budget is a setting, and the honest response might be to drop the vector
arm entirely.

**Why hybrid rather than vectors alone.** Family memory is unusually dense in
names, numbers and dates — "Cooper", "Dr. Patel", "the Honda", "March 14" —
which is precisely where embeddings are weakest and lexical matching is
strongest. Vectors earn their place on paraphrase ("the thing in the kitchen" →
"the dishwasher"), which lexical cannot do at all. Each arm covers the other's
blind spot; the Phase 1 spec's own §7.3 reasoning — that recall here is
dominated by entity filtering and recency, not embedding benchmark position —
is the argument for making the lexical arm the one that cannot fail.

### 5.3 `eve`

Unchanged except that `build_system_prompt` gains a `## What you remember`
section assembled from the bundle. Memory is injected into the rebuilt system
prompt, not appended to `messages`, for the same reason the persona is: it must
reflect the current state of memory on every turn of an existing thread rather
than being frozen into history.

### 5.4 `extract`

Runs after `eve`, on REFLEX (`gemini/gemini-flash-lite-latest`), with
structured output. The user already has their answer, so this costs nothing
they can perceive.

Input: the user message, Eve's reply, and the *overlapping existing memories* —
retrieved by subject and by vector similarity — so the model judges new facts
against what is already believed rather than in a vacuum.

Output: a list of operations.

| Op | Effect |
|---|---|
| `add` | Insert a row in the named layer and scope. |
| `supersede(id)` | Set `superseded_by` on the old row, pointing at the new one, `superseded_why='contradicted'`. |
| `reinforce(id)` | Bump `last_seen_at` and raise `salience`. |
| `forget(id)` | Hard-delete. Only in response to an explicit instruction. |

Then: embed new rows in one batched call, refresh the thread digest, and evict
over-cap rows (§6.3).

**Why write-time contradiction handling and not a nightly reconciler.** The
turn that reveals a contradiction is the only place the context needed to
resolve it exists. A batch job at 3am sees two conflicting sentences and no way
to tell which is current; the extraction step sees someone saying "actually
I've moved to Wednesdays now."

**It runs on every turn, with no gating heuristic.** A cheap "does this look
fact-bearing" filter would be wrong exactly when it matters, and Flash Lite
over five people is a rounding error against the metered budget. If cost ever
becomes real, gate it then, with a bill to point at.

**It runs inside the run**, so run completion is delayed by the extraction
call — the tokens have already streamed, but the run is not `success` until
`extract` returns. This is a deliberate trade: an in-graph node needs no queue,
no worker, and no scheduler. If a client is ever observed to render that
interval as a spinner, the escape hatch is Aegra's cron service, and that is a
change to one node rather than to the design.

---

## 6. Budget, decay, eviction

### 6.1 Token budget

`EVE_MEMORY_TOKEN_BUDGET`, default 1200, allocated 400 / 400 / 400 across
profile, household and episodic, with unused allocation flowing to episodic.
The digest has its own separate cap. Over-budget items are dropped
lowest-score-first.

Tokens are estimated as `len(text) // 4`. A knob whose job is to stop the
prompt growing without bound does not earn a tokenizer dependency, and being
15% wrong about a 1200-token budget changes nothing.

### 6.2 Decay

`exp(-age_days / half_life)`, computed at read time. Ninety-day half-life for
episodic; effectively infinite for profile, household and digest, which are
always injected in full and so have nothing to decay *for*. `reinforce` resets
the clock by bumping `last_seen_at`.

Read-time decay needs no job, cannot fall behind, and stays correct if the pod
is down for a week. A `decayed_score` column updated nightly would be a cache
of an expression that is cheaper to evaluate than to maintain.

### 6.3 Eviction

Profile and household are capped. When `extract` pushes a scope over its cap,
the lowest `salience × recency` rows are superseded with
`superseded_why='evicted'` until it fits.

Eviction is what makes the cap meaningful, and superseding rather than deleting
means a mistakenly evicted fact is recoverable and, more usefully, *auditable*
— "why did she forget that" has an answer.

---

## 7. Permissions

`memory.write_shared` has existed in `family.yaml` since Phase 1, has been
resolved into `EveState` since Phase 1, and has never been read. This phase is
its first real use: `extract` may only write `scope_kind='household'` rows for
a member who holds it. Without it, facts the model judged shared are written to
that member's profile instead — degraded, not dropped.

Reads need no permission check beyond scoping: household rows are readable by
every member, and member-scoped rows are constrained by `scope_id = <sub>` in
the query itself, which is the same shape of enforcement Aegra applies to
threads.

This is enforcement at a write boundary inside the graph, which is narrower
than Phase 3's enforcement at the tool boundary and does not pre-empt it.

---

## 8. Embeddings — the pin resolves

ADR 0003 has carried one conditional since Phase 1: *if the REFLEX key is
Gemini, the embedding model becomes `gemini-embedding-001` truncated to 1536.*
The key is Gemini (§1), so it does.

**`gemini-embedding-001`, truncated to 1536 dimensions, and re-normalised to
unit length.** The re-normalisation is not optional and is the part most likely
to be skipped: the model emits 3072 dimensions trained with Matryoshka
representation learning, and a truncated MRL vector is no longer unit-norm.
Cosine similarity over non-normalised vectors silently returns wrong rankings —
no error, no crash, just quietly worse recall that nobody attributes to this.

Two things are unverified and are probed live before any schema is written:

1. Whether LiteLLM honours an `dimensions` parameter for Gemini embeddings, or
   whether truncation has to happen client-side.
2. Whether the returned vectors are already normalised at 1536, in which case
   the re-normalisation is a no-op that stays in the code as an assertion.

The Phase 1 tier table was written from documentation and four of its five
entries turned out to be wrong against the live proxy (ADR 0004). The same
discipline applies here: probe first, then build.

ADR 0003 is amended rather than replaced — the conditional was written to be
resolved exactly once, and this is that once.

---

## 9. Observability

Recall and extraction emit span attributes, because the two questions this
design will actually be judged on cannot be answered without them:

| Attribute | Question it answers |
|---|---|
| `eve.recall.vector_used` | How often does the 120 ms budget actually hold? |
| `eve.recall.latency_ms` | What did memory cost the latency contract? |
| `eve.recall.items`, `eve.recall.tokens` | Is the budget binding, or is it theatre? |
| `eve.extract.ops` (by type) | Is she learning, and is she superseding — or only accumulating? |

A memory system that only ever emits `add` is broken in the specific way §2
warns about, and that failure is invisible without this counter.

---

## 10. Testing

Three tiers, matching Phase 1's structure.

**Unit** — no network, no services. Scoring, reciprocal-rank fusion, decay
maths, budget truncation, the operation-application logic against a fake model,
and DDL idempotency. The fusion and decay functions are pure and are the place
a subtle ranking bug would hide.

**Integration** — real Postgres from `docker-compose.test.yml`, which already
runs the VectorChord image the cluster runs, so no change is needed there. The
round trip that matters: write a fact, recall it, contradict it, and assert the
superseded row is gone from every read path. Plus one test that recall returns
a complete bundle when the embedding call is made to fail — the degrade path is
load-bearing and untested degrade paths do not work.

**Live** — one test that `gemini-embedding-001` through LiteLLM returns 1536
dimensions and that the vector is unit-norm after truncation. This is the test
that would have caught the Phase 1 tier problem a week earlier.

---

## 11. Definition of done

| # | Criterion |
|---|---|
| 1 | A fact stated in one thread is used, unprompted, in a different thread. |
| 2 | A fact that contradicts a stored one supersedes it, and the old one stops appearing. |
| 3 | "Forget that" hard-deletes the row. |
| 4 | Recall adds < 150 ms at p50 to time-to-first-token, measured in Langfuse. |
| 5 | A forced embedding failure degrades to lexical recall with no user-visible effect. |
| 6 | Household facts are readable by both members; profile facts are not. |
| 7 | `eve.extract.ops` shows supersessions in production, not only adds. |
| 8 | Schema migration runs before `aegra serve` and fails the pod loudly if it cannot. |

---

## 12. Decision records

| ADR | Change |
|---|---|
| 0002 | **Amended.** A single bounded, cancellable embedding call may precede the first token; nothing generative may. The original prescription — recall runs concurrently and merges into a *later* turn — is withdrawn: concurrent recall cannot inform the answer it runs alongside, which makes episodic memory miss on the turn it is asked for. |
| 0003 | **Amended.** The Gemini conditional resolves. `gemini-embedding-001`, truncated to 1536, re-normalised. |
| 0005 | **New.** Memory storage: one table, supersession over deletion, read-time decay, no scheduled jobs. |

---

## 13. How Phase 3 attaches

Phase 3 wraps `eve` in a tools loop. Three things here are shaped for it and
should not be re-decided there:

- **Recall becomes available as a tool** without changing the query layer. The
  unconditional `recall` node stays for the always-on layers; a `search_memory`
  tool calls the same module with a model-authored query and no budget.
- **Specialists write memory through the same `extract` operations**, not
  through their own tables. The permission gate in §7 is already the shape the
  tool boundary needs.
- **`superseded_by` history is the audit trail** Phase 5's eval harness reads.
  It exists from the first row written, which is why §4.2 is a decision and not
  a convenience.
