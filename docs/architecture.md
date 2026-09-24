# Architecture

This document describes what exists in this repository today: Phase 5c,
"Gated tool code." With this phase shipped, the five-phase program described
in [`README.md`](../README.md) is complete — see "Sandboxed tools" below and
[ADR 0010](adr/0010-sandboxed-tools-are-pure-functions.md) for what that
means and what deliberately stays out of scope forever. For the Phase 5c
design rationale and definition of done, see
[`docs/superpowers/specs/2026-08-27-eve-sandboxed-tools-design.md`](superpowers/specs/2026-08-27-eve-sandboxed-tools-design.md).
For the task-by-task build record, see
[`docs/superpowers/plans/2026-08-27-eve-sandboxed-tools.md`](superpowers/plans/2026-08-27-eve-sandboxed-tools.md).
The Phase 5b eval harness (datasets, the rule-set A/B, the regression gate)
predates this phase and is unchanged by it; see
[`docs/superpowers/specs/2026-08-27-eve-eval-harness-design.md`](superpowers/specs/2026-08-27-eve-eval-harness-design.md)
for its own design and definition of done.

## The graph

```
                          ┌─> ui_submit ─┐          ┌─> openers ─────────────────────────────────> END
START -> load_context ────┤              ▼          │
                          └────────────> recall ────┴─> eve <-> tools -> persist_ui -> extract -> suggest -> title -> END
```

Ten nodes, wired in `src/eve/graph.py`:

- **`load_context`** (`src/eve/context.py`) performs no model call. It reads
  the authenticated principal from
  `config["configurable"]["langgraph_auth_user"]`, resolves the matching
  entry in `family.yaml`, stamps the member's local time, and assembles the
  initial system prompt from `prompts/eve.md` plus member context. It performs
  no memory I/O and makes no model call. `graph.py`'s `_route_after_context`
  then routes to `ui_submit` when the last human message is a `surface.submit`
  action envelope the connected client declared support for, or to `recall`
  otherwise — an undeclared client's envelope becomes ordinary member speech.
- **`ui_submit`** (`src/eve/ui/actions.py`) answers a Save tap on a
  model-authored surface. The client re-runs the turn with the user message's
  content replaced by an `<assistant-ui-action>` envelope; `ui_submit` calls
  no model, rewrites the envelope's `state` into a readable sentence — the
  envelope's `state` IS trusted, unlike `data`, since the member's typed
  values are the only source of truth for them — and replaces the raw
  envelope with that sentence in the transcript. It always continues to
  `recall`, not `END`: a submit has no predetermined answer, and Eve is about
  to decide where the values go, which is exactly when she wants memory
  context. See [ADR 0017](adr/0017-model-authored-surfaces.md).
- **`recall`** (`src/eve/memory/recall.py`) loads profile, household and thread
  digest memory, starts lexical episodic search immediately, and races one
  embedding call against a bounded budget for the vector arm. A timeout or
  embedding failure produces a complete lexical-only bundle.
- **`eve`** rebuilds the system prompt after recall, so current memory is
  included, binds the static specialist/skill tools plus any
  dynamically-discovered ones (freshly materialized from state on every
  call), then invokes the `VOICE` tier model (`src/eve/models.py`) and
  streams the response. The prompt is never appended to `messages`, so
  persona, member-context, and memory edits affect existing threads instead
  of being frozen into history.
- **`tools`** (`src/eve/graph.py`'s `tools_node`) runs whichever tool calls
  `eve` emitted — a specialist, `search_skills`, `search_memory`, or a
  materialized dynamic tool — and routes back to `eve`. `tools_condition`
  decides per turn whether `eve` continues to `extract` or loops back
  through `tools` again, bounded to `EVE_MAX_TOOL_LOOP_ITERATIONS` (6) rounds
  per turn by `eve` itself — LangGraph's own recursion limit defaults to
  10007, which is no bound at all on a paid model. Any tool that raises
  degrades to an error string tool-message rather than ending the run.
  `show_surface` is bound here only when the connected client declares
  `config.configurable.assistant_ui`, and that declaration also **describes**
  it: `_static_tools` intersects the declared `catalogIds` with the server
  catalog and builds the tool's argument schema from the result
  (`src/eve/ui/schema.py`), so the legal component types reach the model as
  tool-call grammar on the first attempt rather than through a
  `search_skills` retrieval that has to succeed first. An older client is
  described by a smaller schema instead of being refused after the fact.
  On its first round only, `eve` also emits a `tool_labels` frame naming the
  tools it just bound — see [Tool labels](#tool-labels) below.
- **`persist_ui`** (`src/eve/ui/persist.py`) copies whatever surfaces the turn
  emitted into the final AI message as a portable `<assistant-ui>` frame.
  `custom` frames are streamed and never stored, and the client replays a
  reopened session from `values.messages` alone, so without this a card
  vanishes on relaunch. The frame text is never streamed live (a node's
  message update is not an LLM token event) and is stripped from what the
  member sees and from TTS on every path.
- **`extract`** (`src/eve/memory/extract.py`) runs after the answer has streamed,
  and hands its work to a background task rather than doing it in the graph — a
  run is complete only at `END`, so an in-graph extraction held the client's
  stream open for a model call plus writes. The `REFLEX` model produces
  structured add, reinforce, supersede, and forget operations; valid writes,
  digest refresh, embeddings, and cap eviction are applied best-effort so
  extraction failure cannot erase a completed answer. The next turn on the
  thread joins the pending task in `recall` before reading memory, so detaching
  costs no ordering — see [ADR 0012](adr/0012-extraction-is-detached-and-joined.md).
  That guarantee is per-process (the pending-task registry is in-memory, not
  shared), so it requires `eve` to run as a single instance — a second
  replica or a rolling deploy can serve one stale-memory turn per replica
  transition, the same class of risk documented for `eve-ambient` below.
- **`suggest`** (`src/eve/suggest.py`) makes one `REFLEX`-tier
  structured-output call and produces 2-4 short first-person utterances the
  member might send next. It runs AFTER `extract` deliberately: with
  background extraction (the default) `extract` returns as soon as it
  registers its task, so the two REFLEX calls overlap and the turn pays
  `max()` rather than `sum()`. Every failure - timeout, malformed response,
  transient error - yields an empty list rather than raising, so a member
  never loses a reply to it. Ambient-driven turns, the loop-exhausted reply,
  and a turn with no human message are skipped before the model is
  constructed. See ADR 0013.
- **`title`** (`src/eve/title.py`) runs after `suggest`, when Eve's answer is
  already streamed. For a member-authored, untitled first exchange it makes one
  bounded `REFLEX` structured-output call and replaces Aegra's first-message
  fallback in `thread.metadata.thread_name`. Missing thread metadata, ambient
  turns, timeouts, malformed output, and storage failures are all no-ops: a
  title can never fail the answer; its bounded work happens after reply text
  has already streamed.
- **`openers`** (`src/eve/suggest.py`, beside `suggest`) answers the other
  chip question: what might this member say *first*, on a chat with nothing
  in it yet. Reached only when a client sets
  `config.configurable.suggestions_only` to exactly `True`, in which case
  `recall` routes here instead of to `eve`. It is the whole turn - no VOICE
  call, no answer, no message appended, and no `extract`/`suggest` after it.
  It emits the same `{"suggestions": [...]}` `custom` frame, shares
  `suggest`'s budget and failure discipline, and is gated by the same
  `EVE_SUGGEST_ENABLED` switch. See ADR 0018.

The latency contract in [ADR 0002](adr/0002-no-llm-before-first-token.md)
forbids a *generative* model call before the first streamed token.
`load_context` is pure local computation; `recall` is the one concession: a
single bounded and cancellable embedding call that can degrade to lexical-only.
Phase 3 wraps `eve` in the `eve <-> tools` cycle, per
[ADR 0001](adr/0001-agents-as-subgraph-tools.md), without moving a generative
router in front of Eve.

The graph is compiled **without** a checkpointer (`src/eve/graph.py`).
Aegra attaches its own Postgres-backed persistence to graphs it serves;
adding one here would shadow it.

## Module map

```
src/eve/
  settings.py       # pydantic-settings; all environment configuration
  family.py         # family.yaml loader, member lookup, permission checks
  state.py          # EveState, MemberContext
  context.py         # load_context node
  models.py         # tier -> LiteLLM model; sole owner of model identifiers
  graph.py          # the state graph
  auth.py           # Auth() handler: JWT/dev auth + resource scoping
  pat.py            # personal access tokens: eve_pat SQL and the eve-pat CLI
  memory/
    types.py        # Memory/MemoryBundle and extraction schemas
    ranking.py      # pure decay, reciprocal-rank fusion, token budgeting
    db.py           # pool and advisory-locked ordered-DDL migrations
    embed.py        # Gemini embedding client, truncation, re-normalisation
    store.py        # every eve_memory SQL read and write
    recall.py       # pre-answer hybrid retrieval node
    extract.py      # post-stream structured extraction and writes
  skills/
    search.py       # search_skills tool; matches queries against procedures and MCP tools
    registry.py     # authored SKILL.md procedures loader
    mcp_registry.py # registered MCP tool descriptions
    types.py        # DynamicToolSpec, skill schemas
    materialize.py  # turn DynamicToolSpec into callable tool at model call time
    authoring.py    # write_skill tool (Phase 5a)
    cli.py          # eve-skill script (Phase 5a)
  ui/
    protocol.py     # the assistant-ui/1.0 contract, its validator, the portable frame
    stream.py       # client capabilities in; assistant_ui and tool_labels frames out
    schema.py       # the catalog, projected into show_surface's argument schema
    surface.py      # assemble a model-authored component tree into a create operation
    tools.py        # show_surface: the one tool for any model-authored UI
    actions.py      # inbound action envelope + the model-free ui_submit node
    persist.py      # copy this turn's surfaces into the AI message for history
  eval/
    types.py        # DatasetItem, ItemResult, RunScore -- shapes only
    datasets.py     # build the two dataset shapes from Postgres and the golden file
    replay.py       # run one item through the real code path
    scorers.py      # deterministic scorers and the REFLEX judge
    store.py        # every eve_eval_run SQL statement; the regression gate
    publish.py      # best-effort Langfuse dataset + run upload
    hygiene.py      # duplicate/contradiction/dead-rule detection over eve_memory rules
    cli.py          # eve-eval script: build | run | gate | hygiene (Phase 5b)
  tools_authoring/
    types.py        # ToolProposal, CheckResult -- shapes only
    inspect.py      # the AST allowlist: an accident guard, NOT a security boundary
    propose.py      # propose_tool tool: the interrupt() gate, tools.author-only
    store.py        # every eve_tool SQL statement; source_hash binds an approval to bytes
    registry.py     # live_tools() -> DynamicToolSpec, feeding search_skills
    cli.py          # eve-tool script: list | approve | reject | revoke (Phase 5c)
  records/
    store.py        # every eve_record SQL statement; one module owns the table
    tools.py        # record_append / record_query: the only writers and readers
  widgets/
    recipe.py       # the closed recipe vocabulary and its validator (ADR 0019)
    store.py        # every eve_widget_resource SQL statement
    resolve.py      # execute a validated recipe; the snapshot route, no model call
    tools.py        # save_widget: the one widget-authoring tool in the eve graph
    app.py          # the mounted resource API: per-route auth via require_auth
  routines/
    cadence.py      # the closed cadence vocabulary and its validator; imports nothing from eve
    store.py        # every eve_routine SQL statement; claim_due is the one household-wide query
    tools.py        # schedule_routine, list_routines, cancel_routine -- refuse on an ambient turn
    app.py          # the mounted resource API: list, patch, delete; no create route
  http_app.py       # the one app aegra.json's http.app mounts; includes the widget and routine routers

src/eve_sandbox/
  settings.py   # EVE_SANDBOX_* only -- no database URL, no model key, no third-party credential
  runner.py     # the child process: sets its own rlimits, execs the tool's `run`, one JSON line out
  execute.py    # spawns the child with an empty environment and a tmpfs cwd; enforces the wall-clock timeout and output cap
  app.py        # the eve-sandbox FastAPI service: POST /invoke, GET /healthz

src/eve_ambient/
  types.py      # Signal, FilterVerdict; tool_result/list_field parsing helpers
  store.py      # every eve_ambient_seen and eve_ambient_notice SQL statement
  gates.py      # pure functions: scoped_audience, permitted, quiet hours, daily-cap window
  ntfy.py       # the Notifier protocol and its one ntfy implementation
  sources/      # calendar.py, mail.py, finances.py, computer.py, coding.py, routines.py (polled); home.py (pushed via webhook)
  filter.py     # the REFLEX relevance gate; raises FilterError on infrastructure failure
  notify.py     # the compose turn: creates a thread, runs eve, pushes or discards it
  pipeline.py   # handle_signal: the one place signal-to-resolution order is decided
  app.py        # the eve-ambient FastAPI service: webhook, poll loop, /healthz
```

The import graph is acyclic: `settings` and `family` depend on nothing
internal. Within `memory/`, dependency order is `types` -> `ranking`; `settings`
-> `db` and `embed`; `db`/`embed`/`types` -> `store`; and
`embed`/`ranking`/`store`/`types`/`pending` -> `recall`, while `extract` depends
on `embed`, `store`, `types`, `pending`, `models`, and `settings`. `pending`
imports nothing internal, which is what lets both `recall` and `extract` depend
on it without a cycle. `context` depends on
`family`, `settings`, `state`, and memory types; `models` depends on `settings`;
`graph` depends on `context`, `memory`, `models`, `state`, and, since Phase 5c,
`eve.tools_authoring.propose` (`propose_tool` is bound alongside the
specialists and `search_skills`); `auth` depends on `family` and `settings`.

Within `tools_authoring/`, `types` depends on nothing; `inspect` depends only
on `types`; `store` depends on `eve.memory.db`; `propose` depends on
`inspect`, `store`, `eve.settings`, `eve.specialists.permissions`, and
`eve.state`; `registry` depends on `store` and `eve.skills.types`, and is the
one thing `eve.skills.search` imports from this package, keeping the
propose/approve machinery out of the discovery path. `src/eve_sandbox` is
outside this graph entirely and imports nothing from `eve` at all — not
`eve.settings`, not `eve.memory.db`, nothing — which is what makes it safe to
hold no credential: there is no import path by which one could reach it even
by accident. `tests/test_tools_integration.py::test_eve_sandbox_imports_nothing_from_eve`
asserts this the same way `eve.eval`'s one-way dependency is asserted, by
import graph rather than by convention.

`records/` and `widgets/` are the newest leaves of the same acyclic graph:
`records.store` and `widgets.store` depend only on `eve.memory.db`, one module
owning each table, and `widgets.recipe` imports nothing from `eve` at all,
which is what makes its vocabulary closed. On top of those, `widgets.resolve`
sits on `records.store`, `widgets.recipe`, and `eve.tools_client`;
`widgets.tools` and `widgets.app` sit on `widgets.recipe` and `widgets.store`
and gate on `eve.specialists.permissions`. `graph` binds their tools
(`record_append`, `record_query`, `save_widget`) alongside the specialists and
`search_skills`.

Within `eve_ambient/`, `sources/` and `gates` depend on `types`; `ntfy`
depends only on `eve`'s own modules (`eve.settings`) and not on `types` at
all; `filter` and `notify` depend on `types` plus `eve`'s own modules
(`eve.family`, `eve.memory.store`, `eve.models`, `eve.settings`); `pipeline`
depends on `types`, `store`, `gates`, `filter`, and `notify` (which pulls in
`ntfy`); `app` depends on `pipeline` and `sources`. `store` depends on `eve`'s
own `eve.memory.db` as before, and, since Phase 5b, also on `eve_ambient.types`
(`Signal`, `FilterVerdict`) for `record_decision`.

`src/eve/eval/` sits outside this graph on purpose: `datasets` and `store`
depend on `eve.memory.db`; `replay` depends on `eve_ambient.filter` and
`eve.graph`; `scorers` depends on `eve.models`; `cli` depends on all of them.
The dependency runs one way only — nothing in `src/eve/` outside `eve/eval/`
imports `eve.eval`, so the harness cannot affect a production turn even by
accident. `tests/test_eval_datasets.py` asserts this the same way the rest of
this section's acyclicity is asserted, by import graph rather than by
convention.

`models.py` is a deliberate chokepoint: model identifiers appear nowhere else
in the codebase, so retiering is a one-file change. The tiers, all served
through LiteLLM (`settings.litellm_base_url`):

| Tier | Model | Purpose | First used |
|---|---|---|---|
| `VOICE` | `chatgpt/gpt-5.6-terra` | Eve herself | Phase 1 |
| `DEEP` | `chatgpt/gpt-5.6-sol` | Planning; hard reasoning | Phase 5 |
| `MECHANICAL` | `chatgpt/gpt-5.6-luna` | Structured, tool-heavy specialist work | Phase 3 |
| `CODE` | `chatgpt/gpt-5.6-sol` | Authoring skills and tool code | Phase 5a |
| `REFLEX` | `gemini/gemini-flash-lite-latest` | Ambient filtering; memory extraction | Phase 2 |

The `chatgpt/*` models are registered in LiteLLM with `mode: responses`, so
the LangChain client sets `use_responses_api=True` for those tiers. `REFLEX`
uses the metered Gemini route and the Chat Completions-compatible API instead.

Every `chatgpt/*` tier falls back to `anthropic/claude-sonnet-5` — one model
covering all four, rather than a fallback per tier — declared as a LiteLLM
`fallbacks` target in the infrastructure repo, not in `models.py` (ADR 0004
amendment, EVE-2). `REFLEX` has none: it already runs on the separate,
metered Gemini key, so it doesn't share the ChatGPT credential's failure
mode. Because the fallback lives in the proxy config, `TIER_MODELS` never
changes and eve-tools/eve-ambient inherit it automatically.

One deliberate exception to "model identifiers live only in `models.py`":
`tests/test_models.py::test_voice_tier_is_the_chatgpt_conversational_model`
asserts the `VOICE` model string directly. A test whose job is to pin the
tier-to-model mapping has to name the model, or it asserts nothing. Retiering
`VOICE` deliberately touches that test; this is not an oversight to "fix."

This tree predates Phase 3's `specialists/`, `skills/`, and `tools_client.py`
additions to `src/eve/`, and the separate `src/eve_tools/` package; see
"Specialists and skills" below.

## Specialists and skills

Phase 3 gives `eve` a tool-calling loop (the graph's `eve <-> tools` cycle)
that reaches four domain specialists and one extensible skills layer,
without any of them holding a third-party credential directly:

- **`src/eve/specialists/`** — `home.py`, `mail.py`, `finances.py`, and
  `health.py` each wrap a small tool-calling agent built by `base.py`'s
  `build_specialist` (running on `Tier.MECHANICAL`) as a single opaque tool
  for `eve` — health gated by the bare `health` permission, the way
  finances is gated by `finances`, and granted to both members in
  `family.yaml`;
  `permissions.py` enforces `family.yaml` permissions once at that
  specialist boundary and again inside `mail.py`'s `send_email`, which needs
  `mail.send` on top of the coarser `mail.read`/`mail.send` check on
  `ask_mail` itself. Each specialist's inner loop gets
  `EVE_SPECIALIST_MAX_ITERATIONS` (6) model+tool rounds, converted to
  LangGraph supersteps by `base.py`'s `_superstep_limit` — `create_agent`
  spends two per round, so passing the setting through raw bought 2 rounds
  and raised `GraphRecursionError` on the third. Exhausting the budget
  returns an English sentence, the inner-loop twin of `graph.py`'s
  `_LOOP_EXHAUSTED`.
- **`src/eve/skills/`** — `search_skills` (`search.py`) is the one tool that
  turns Eve's fixed toolset into an extensible one: it matches a query
  against authored SKILL.md procedures (`registry.py`) and registered MCP
  tool descriptions (`mcp_registry.py`), returning a procedure's text
  directly or appending a `DynamicToolSpec` (`types.py`) to state, which
  `materialize.py` turns back into a real callable tool on the next model
  call (never held live in state, because Aegra checkpoints `EveState` to
  Postgres across turns).
- **`src/eve/tools_client.py`** — the one door from Eve's main container to
  `eve-tools`. Every specialist tool and every materialized dynamic tool
  calls its `invoke()`, an HTTP request with a timeout whose failures
  degrade to a returned error string rather than a raised exception, so a
  broken tool call lets Eve explain the problem instead of failing the turn.
- **`src/eve_tools/`** — a separate FastAPI service and, per
  [ADR 0006](adr/0006-eve-tools-isolation.md), the only
  third-party-credentialed HTTP surface in the deployment: `home_assistant.py`,
  `gmail.py`, `monarch.py`, and (Phase 4) `caldav_client.py` hold the Home
  Assistant, Gmail, Monarch Money, and CalDAV clients, `mcp_dispatch.py` opens
  a fresh connection per call to a dynamically-discovered MCP server
  (`mcp_servers.py`), and `app.py` dispatches every request to one of them by
  a namespaced tool name. `caldav_client.py` has no specialist calling it
  yet — it exists to serve `eve_ambient`'s calendar source. `gmail.py`
  additionally hydrates each message's `from`/`subject`/`date`/`snippet` with
  a per-message fetch, because `messages().list()` returns only an id and a
  thread id; `monarch.py` normalizes Monarch's nested
  `monthlyAmountsByCategory` shape into the flat `spent`/`limit`/`period`
  budgets its callers expect. Both additions exist because `eve_ambient`'s
  mail and finances sources need those shapes, not because a specialist asked
  for them. The health coach adds the service's first piece of persistent
  state: per [ADR 0016](adr/0016-eve-tools-owns-a-credential-table.md) it
  connects to Postgres under its own role (`EVE_TOOLS_DATABASE_URL`, kept
  deliberately separate from Eve's `EVE_DATABASE_URL`) and reaches exactly
  one table, `eve_oauth_token` — WHOOP rotates its refresh token on every
  refresh, so those credentials need durable, replica-shared storage that
  environment variables cannot provide. `health.py` fans each request out to
  whichever of the two providers the member has connected and merges their
  normalizers' output into one provider-agnostic shape.

## Self-authored behaviour

Phase 5a lets Eve compose her own standing instructions and multi-step
procedures, stored in the `eve_memory` table as two new `layer` values —
`rule` (always rendered into the system prompt under the
`### How you have learned to work with them` heading) and `procedure` (found on
demand by `search_skills` alongside MCP tools and SKILL.md procedures). They
inherit the existing memory machinery: the same `scope_kind`/`scope_id` pair
every other layer uses (`member` for one member's own, `household` for the
family's; `thread` is the digest layer's), decay, supersession, embeddings,
hybrid search, capping (`EVE_MEMORY_RULE_CAP` limits rules in scope), and an
audit trail.

Inseparably, and foundational to their safety, **authorisation never reads
memory.** Permissions flow `family.yaml` → `get_family()` → `build_member_context()`
→ `state["member"]["permissions"]` → `permission_denial()`, resolved in
`load_context` before `recall` has run. No rule, procedure, or memory row
influences what a member may do. Both authoring paths — `extract`'s passive
rule pass and the `write_skill` tool — refuse on a turn carrying the ambient
marker, behind one shared predicate (`eve.state.may_author`), so authored
behaviour cannot originate from signals surfaced by the ambient pipeline.

**`src/eve/skills/authoring.py`** exposes the `write_skill` tool to Eve, which
writes a `procedure`-layer `eve_memory` row whose `content` is a SKILL.md-shaped
document — the same parser (`eve.skills.registry.parse_skill_text`) reads it and
the files on disk, so `search_skills` cannot tell them apart. It is scoped,
versioned and supersession-chained like any other memory row.
**`src/eve/skills/cli.py`** implements the `eve-skill` script
(`uv run eve-skill list` and `uv run eve-skill revoke <id>`) for humans to audit
and revoke authored rules and procedures from the command line without deleting
the row — a revoked one remains in the audit trail but is excluded from recall.
See [ADR 0008](adr/0008-authored-behaviour-is-memory.md).

## Sandboxed tools

Phase 5c lets Eve author *executable* code, not just authored prose, for the
calculations and parses she otherwise does inside a language model, badly and
unverifiably. Unlike Phase 5a's rules and procedures, this is deliberately
not a memory layer: an approval binds to exact source bytes, needs a
uniqueness constraint, and must never be reachable by semantic recall into a
prompt, so a text `content` column with an embedding is the wrong shape. It
gets its own table, `eve_tool` (`alembic/versions/0002_eve_tool.py`), and its
own package, `src/eve/tools_authoring/`.

**The path is propose → interrupt → approve → dispatch.**

1. **Propose.** `propose_tool` (`src/eve/tools_authoring/propose.py`) is a
   tool bound alongside the specialists and `search_skills` — see the import
   graph above. It requires `tools.author`, permission-checked the same way
   every other tool boundary is (`eve.specialists.permissions.permission_denial`),
   runs the AST check (below), and records the proposal
   (`eve.tools_authoring.store.propose`) before pausing.
2. **The gate.** `propose_tool` calls LangGraph's `interrupt()` with the full
   proposal — name, description, schema, source, and the imports the checker
   found — so everything the approver needs is in that one payload; reading
   the source anywhere else is how a wrong version gets approved. Aegra
   checkpoints the run; a human resumes it with `Command(resume={"approved":
   bool, "why": str})`. `tools.author` collapsing proposer and approver into
   one person is deliberate (design §5.1): the interrupt always surfaces in a
   thread owned by someone entitled to answer it.
3. **Approve or reject.** The proposal row already carries `source_sha256` =
   `sha256(source)`, computed at propose time; approving just stamps
   `approved_by`/`approved_at` on that exact row, so the approval is a
   statement about those bytes and no other. A partial unique index,
   `eve_tool_live_name`, allows only one live approved row per name at a
   time, so re-proposing an existing name leaves the old version serving
   until the new one is approved. Rejecting stamps `rejected_why` and the
   proposal never executes — no auto-retry.
4. **Discover and dispatch.** `eve.tools_authoring.registry.sandbox_specs`
   turns every live approved row into a `DynamicToolSpec`, read by
   `search_skills` only when `EVE_SANDBOX_ENABLED` is true (the check lives
   at that call site, not inside `registry.py`, so the kill switch holds even
   for a spec a checkpointed thread already carries). `materialize.py` binds
   it as an ordinary callable tool, and `tools_client.invoke` posts it —
   source, hash, and arguments together — to `eve-sandbox`'s `/invoke`, the
   same contract shape `eve-tools` uses with one field added.

**Enforcement is layered, and the layers are not equal** (this is the
substance of [ADR 0010](adr/0010-sandboxed-tools-are-pure-functions.md)):

1. **The pod is the security boundary.** Default-deny egress `NetworkPolicy`,
   no ServiceAccount token, no secret mounts, read-only root filesystem,
   non-root UID — all `infrastructure` work, verified only by the `live`-marked
   tests in `tests/test_sandbox_live.py` run by hand against the cluster.
2. **The process** (`src/eve_sandbox/execute.py`, `runner.py`) is defense in
   depth, not the boundary: one subprocess per call (no pool, no reused
   interpreter — a warm interpreter is state shared between two tools, the one
   thing a sandbox tool does not get), started with `python -I` (isolated
   mode: no `PYTHONPATH`, no user site-packages), an empty environment but for
   the import path, a tmpfs `cwd`, and rlimits the child sets on itself —
   `RLIMIT_CPU`, `RLIMIT_AS`, and `RLIMIT_CORE` at zero, because a core dump
   is the one artefact that could persist tool data outside the call.
3. **The AST allowlist** (`src/eve/tools_authoring/inspect.py`) is explicitly
   **not** a security boundary — its own module docstring says so in capital
   letters. It rejects imports outside a short allowlist (`json`, `re`,
   `math`, `datetime`, `urllib.parse` but not `urllib.request`, and so on),
   the names `eval`/`exec`/`compile`/`open`/`__import__`, and dunder attribute
   access, and requires exactly one module-level `run(arguments: dict) ->
   dict` function. Its real jobs are to give Eve a specific, actionable error
   so she can revise before bothering a human, and to make the approver's read
   short. Every guarantee in this phase must hold with this module assumed
   defeated — the checker is bypassed outright in
   `tests/test_tools_integration.py`'s process-constraints test, which imports
   `os` and reads the environment directly to confirm containment holds
   without it.

`eve-sandbox` (`src/eve_sandbox/`, `Dockerfile.eve-sandbox`) is the service
those tools run in. It imports nothing from `eve` — not `eve.settings`, not
`eve.memory.db`, nothing — and holds no database URL, no model key, and no
third-party credential of any kind (`src/eve_sandbox/settings.py`); the only
secret it holds is the shared bearer token that authenticates Eve to it. A
source-hash mismatch between what the caller sends and what the database
recorded is refused and logged rather than executed — the database and the
caller disagreeing about approved bytes is a tampering signal, not a bug to
retry.

**`eve-tool`** (`src/eve/tools_authoring/cli.py`) is the human-facing side:
`uv run eve-tool list` (optionally `--source <id>` to print full source),
`uv run eve-tool approve <id> --as <approver>` (re-runs the AST check against
these exact bytes before approving — the propose-time check already ran, but
an approval is a statement about *these* bytes), `uv run eve-tool reject <id>
--why <reason>`, and `uv run eve-tool revoke <name> --why <reason>`
(`--all` retires every live tool at once). Revocation takes effect on the
next `search_skills` call, which rebuilds `sandbox_specs` from the database
every time — no restart. `EVE_SANDBOX_ENABLED=false` is the wider kill
switch: `propose_tool` unbinds, no sandbox spec is registered, and a sandbox
spec a paused thread's checkpoint already carries fails closed to an error
string rather than dispatching.

## Eve's computer

A sixth service, `eve-computer` (`src/eve_computer/`, `Dockerfile.eve-computer`),
gives Eve a persistent Linux desktop: her own accounts, a browser, a shell,
and internet access, behind a task API she is dispatched to and polled for -
never called back from. See
[`docs/superpowers/specs/2026-08-28-eve-computer-design.md`](superpowers/specs/2026-08-28-eve-computer-design.md)
and [ADR 0015](adr/0015-granted-identity-vs-authored-capability.md) for the
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

**Eve's GitHub login is provisioned by hand, once, over VNC.**
`kubectl port-forward` to the VNC port, open a terminal on her desktop,
run `gh auth login`, and complete the browser flow as Eve's own GitHub
account. The token lands in `/home/eve/.config/gh/hosts.yml` on the PVC -
never a Kubernetes Secret, never an environment variable, never a line in
this repository, exactly like her browser session cookies.

Which repositories she can touch is her collaborator status on GitHub.
There is no allow-list in this repo to keep in sync, and revocation is a
checkbox in your account (ADR 0015). Until that login exists, every
delegated coding session fails loudly at `git push`, with the failure
riding in the pull-request result Eve reports.

**The session lane (EVE-4).** Beside `/tasks` lives a second concurrency
lane: ACP coding sessions (`POST /sessions`, `GET /sessions/{id}`,
`POST /sessions/{id}/prompt`, `POST /sessions/{id}/close`,
`DELETE /sessions/{id}`), served by `src/eve_computer/acp/` -
`client.py` (the protocol's client half: auto-approves permission requests,
serves `fs/*` confined to the session root), `registry.py` (agent + model →
argv and environment, four entries), `harness.py` (the DeepSeek harness's
home: the profile it boots, the LiteLLM route it serves, and the git pull
that fills in the rest), `repo.py` (clone, worktree add/remove, push,
`gh pr create`; no ACP types), and `session.py` (the state machine, the only
file importing both client and repo). The GUI queue stays
serialised because one machine has one mouse; a coding session needs no
display, so its only bound is `max_concurrent_sessions` - a half-hour
conversation must not block a screenshot. The box records and never
classifies: a finished turn is `idle`, full stop (ADR 0016).

**The fourth agent (EVE-24).** `dsh` is the tiebreak agent, and the only one
with no model flag: it boots a *profile*, and the model is one row of that
composition. `harness.py` therefore writes `$DSH_HOME/eve-route.patch.yml` -
a LiteLLM route plus the ACP row's provider and model, the model as a
`!!js process.env.EVE_ACP_MODEL` expression the launcher evaluates at
startup - and `registry.py` passes it as `dsh --patch <file>`, which is the
LAST layer the launcher applies. That placement is the whole design: the
harness home also holds Noah's profile, pulled from the repository
`dsh harness-sync push` writes, and that profile carries routing of its own.
`--patch` outranks every layer a pull can reach, so his preferences survive
and this box stays pointed at its own proxy (ADR 0020).

On Eve's side, `src/eve/coding/` mirrors it: `store.py` (every
`eve_coding_session` statement - `alembic/versions/0005_eve_coding_session.py`,
the row beside `eve_computer_task`), `catalogue.py` (LiteLLM's live
`/v1/models`, cached, with the `ocp/*` deny-list), `dispatch.py` (the three
tools - `delegate_coding_task`, `check_coding_session`,
`send_to_coding_session` - behind the `code.delegate` permission, checked
before any HTTP call), and `supervisor.py` (the only place an `idle` turn
is classified: poll the box, decide reply/done/escalate on `Tier.CODE`,
act).

**The supervisor loop.** `eve-ambient` now runs two `asyncio` loops:
`_poll_forever` at the ambient interval, and `_supervise_forever` at
`coding_supervisor_interval_seconds` (20s) - a control loop with an agent
waiting on the other end cannot wait five minutes. The fast loop advances
conversations; the slow ambient tick is where resolved sessions become
`coding` signals through `eve_ambient.sources.coding`, carrying the
permission gate, quiet hours, and the daily cap that a control loop has no
business bypassing. Both call `supervisor.tick()`, which is idempotent over
already-resolved sessions.

See
[`docs/superpowers/specs/2026-09-01-eve-acp-tools-design.md`](docs/superpowers/specs/2026-09-01-eve-acp-tools-design.md)
and [ADR 0016](docs/adr/0016-the-box-runs-the-protocol-eve-holds-the-judgement.md).

## Aegra and `aegra.json`

Eve does not run its own server process. `aegra.json` at the repository root
registers the graph and the auth handler with Aegra:

```json
{
  "graphs": { "eve": "./src/eve/graph.py:graph" },
  "auth": { "path": "./src/eve/auth.py:auth" }
}
```

`aegra serve` reads this file, compiles the registered graph(s), and serves
the Agent Protocol (threads, runs, streaming, store) plus its own
Postgres-backed persistence layer over them. **API and background run
workers are one process** — there is no separate worker command. Run
capacity is `WORKER_COUNT` × `N_JOBS_PER_WORKER` (default 3 × 10). The
process listens on port `2026` and exposes `/health`, `/ready`, and `/live`
(plus `/info`).

`AUTH_TYPE=custom` must be set in the environment for any deployment. In the
installed version (aegra-api 0.10.3), `get_auth_backend()` accepts only
`"noop"` or `"custom"` and silently falls back to `"noop"` for anything else;
both branches currently construct the same backend and load `aegra.json`'s
custom handler regardless of which value was set, but a future aegra-api
version may start gating on it. Setting it correctly now costs nothing and
avoids a latent trap.

## Widget resources

Reusable widgets are the newest feature this repository serves: a member
saves a widget, and the widget later refreshes its own data with no model call
and no graph run. The server side rests on two generic tables, installed by
the `0009_eve_record` and `0010_eve_widget_resource` migrations, whose shape
[ADR 0019](adr/0019-one-generic-record-store.md) records.

**`eve_record` is the whole store.** One row per member-recorded entry:
member, an opaque `collection` string, an opaque jsonb `payload`, and a real
`occurred_at` column, indexed together with member and collection so a range
query is an index scan rather than a scan that parses jsonb. That indexed
column is the entire reason this is a table instead of a memory layer (ADR
0008 precedent): memory is prose with embeddings and decay, and a numeric
series over ninety days is aggregation, not lexical guesswork.
`record_append` and `record_query` are the only writers and readers, and every
query is member-scoped. What a collection MEANS lives in a skill, which is
prose. The cost of that freedom is unconstrained names: `record_append`
returns the collection's previously-seen field names to steer consistency, and
the skill tells the model to query before inventing a name, but neither is
enforcement. A divergent write is stored and visible rather than rejected,
because member-recorded data must never be lost to a schema disagreement it
cannot see.

**`eve_widget_resource` is a recipe, not code.** One row per saved widget:
owner, `kind` (the renderable family, never a domain), a title, the validated
`recipe`, the persisted `filters`, and a monotonically increasing `revision`
for optimistic concurrency. The recipe is a closed declarative vocabulary
validated in Python (`eve.widgets.recipe`). Its sources are drawn from a
closed set of kinds, each mapped to one audited reader in
`eve.widgets.resolve`: the record store itself, or a credentialed reader
reached through `eve.tools_client`, because credentials and normalisation
cannot be authored by a model. The validator's tests assume a hostile author,
because unlike the sandbox AST check, which ADR 0010 explicitly does NOT treat
as load-bearing, this one IS: a recipe executes forever after with no human
and no model in the loop. The snapshot route executes a recipe against those
readers only; `src/eve/graph.py` is not in the request path, so a snapshot can
never invoke a graph run. Adding a widget costs a recipe, not a migration, a
store module, a tool, and a release.

**The API is a custom FastAPI app mounted through Aegra's `http.app`**
(`"http": {"app": "./src/eve/http_app.py:app"}` in `aegra.json`, which
includes this widget router alongside the routine router described under
"Routines" below), serving capabilities, list, snapshot, action, and delete
routes. Authorization is
enforced per query, not by Aegra's route walk: in aegra-api 0.10.3,
`enable_custom_route_auth` is a no-op (the walk rewrites
`route.dependencies` after the routes are built, but FastAPI resolves
dependencies from the dependant constructed at route-creation time), so
`aegra.json` sets it to `false` and the widget router declares
`Depends(require_auth)` on itself. That is version-sensitive: re-check on an
aegra upgrade rather than assuming the walk starts enforcing. The `@auth.on`
handlers in `src/eve/auth.py` scope Aegra's own threads and store API and
never reach a custom route, so every handler below resolves the member from
the authenticated principal and passes it into an owner-scoped query. A
resource id is a locator and never a capability: absent and foreign ids answer
the same way, and a body that carries a member field has it dropped rather
than honored. Public errors are sanitized: unauthenticated is 401, a missing
or foreign resource is a 404 that reveals nothing about whether the id
exists, a permission gap is 403, an unknown action or malformed filter is a
400 carrying only the schema error, and a stale action is a 409 whose body is
the fresh snapshot so the client can render current data instead of an error.
The action routes impose the same closed vocabulary as the recipe does,
`filters.replace` the one legal entry, so an authored recipe cannot invent an
action either.

## Routines

A routine is a stored prompt plus a cadence: a member asks Eve to check or do
something repeatedly, and each firing is an ordinary headless turn — the same
`eve` graph a member's own message runs, with no special-cased node for
"this one is scheduled." Silence is a successful outcome, not a missing one:
a routine that finds nothing worth saying said so, and that is the routine
working, not failing.

**The firing path** is `eve_ambient.sources.routines`
(`src/eve_ambient/sources/routines.py`), a `per_member=False` polled source
registered in `sources/__init__.py`'s `SOURCES` tuple next to `computer`,
`coding`, and `finances`: the query is household-wide by construction, and
every claimed row already carries its own member, so polling once per member
would just cost one query per member to answer the same question. Because it
rides the existing ambient tick, every mechanism the tick already has is
reused rather than duplicated: the gate chain, the thread creation and
delivery in `notify.deliver`, the veto (Eve replying `NOTHING`), and the push
through `ntfy`.

**`routines` joins `computer` and `coding` in `_REQUESTED_SOURCES`**
(`src/eve_ambient/pipeline.py`), the set of sources the relevance filter,
quiet hours, and the daily cap never touch. A member who set up a routine
asked for it directly — an LLM deciding the answer to a direct request is
"not relevant" and swallowing it is the worst failure mode available, and a
shared daily counter would let a chatty calendar starve a routine the member
deliberately created. The per-routine schedule, authored in the member's own
timezone, is what stands in for quiet hours here: a member who does not want
a 3am notification schedules the routine for 8am instead.

**`claim_due`** (`src/eve/routines/store.py`) is the one query in the module
without a `member_sub` filter, and the one place a double fire is made
unreachable: it advances `next_run_at` in the same statement that selects the
due rows, pushing it to a bounded lease rather than to `now()` — `now()` is
the transaction timestamp, so a naive `next_run_at = now()` would still read
as due the instant the statement commits and the very next tick would
re-claim the same row. A routine whose `next_run_at` is far in the past (the
service was down overnight) fires once and schedules forward from the current
time, never delivering a backlog of missed occurrences.

**The cadence vocabulary is closed**, validated in `eve.routines.cadence`,
which imports nothing from `eve` and is exercised by the tools, the store,
and the ambient source alike without a cycle. Exactly one of `every_hours`
(1-168), `daily_at` (a 24-hour time), or `weekly_at` (a day and a time) may
be present, with a one-hour floor: nothing may run more often than hourly. A
cron expression would be strictly worse here — a model-authored `* * * * *`
is a paid VOICE-tier turn every minute, and neither a mobile editor nor a
plain-English rendering can be built over an open grammar.

**The three tools** — `schedule_routine`, `list_routines`, and
`cancel_routine` (`src/eve/routines/tools.py`) — each refuse outright on an
ambient turn, checked via `eve.state.turn_is_ambient` before the permission
check or any storage access: a routine's own firing cannot author, pause, or
cancel a routine. **`configurable["is_ambient"]` is NOT the mechanism**, and
deliberately so: nothing in `src/` ever sets that key, because
`eve_ambient.notify.deliver` calls `runs.wait(thread_id, "eve", input={...})`
with no `config` at all, so a guard reading it would be inert in production
and would pass its own tests only because they build the config by hand
(EVE-30). `turn_is_ambient` instead inspects the last `HumanMessage` in state
for the same ambient marker `eve.state.may_author` already checks for
authored rules and procedures, so a third copy of that check cannot drift
from the other two.

**`EVE_ROUTINES_ENABLED` is off by default**, the same posture
`ambient_enabled` and `coding_enabled` take: a routine is a recurring paid
VOICE-tier turn nobody is watching, so a deployment that has not deliberately
accepted that standing spend must run none.

**`aegra.json`'s `http.app` now points at `src/eve/http_app.py`**
(`"http": {"app": "./src/eve/http_app.py:app"}`), which owns no routes of its
own and holds both the widget router and the routine router
(`src/eve/routines/app.py`), plus the one app-level exception handler that
flattens a 409's structured detail. The routines router follows
`eve.widgets.app`'s exact shape — `Depends(require_auth)` declared on the
router itself rather than relying on Aegra's `enable_custom_route_auth` walk
— and deliberately exposes no create route: authoring a routine belongs in
conversation, where Eve can ask what a vague instruction means before it is
committed to a table that will run unattended. The mounted surface only
lists, patches (title, cadence, status, expiry, each under optimistic
concurrency), and deletes what already exists.

## Auth and thread scoping

`src/eve/auth.py` registers a `langgraph_sdk.Auth` instance with two
concerns: authentication, and per-resource authorization.

**Authentication** has two modes, chosen by `EVE_AUTH_MODE`:

- `oidc` validates a bearer JWT against Authentik's JWKS endpoint (issuer,
  audience, expiry, signature; required claims `exp`/`iss`/`aud`/`sub`).
- `dev` maps an opaque static token to a `family.yaml` subject via
  `EVE_DEV_TOKENS`, for local work without an IdP.

`dev` mode is refused outright when `EVE_ENV=production` —
`Settings.model_post_init` (`src/eve/settings.py`) raises at startup rather
than allowing a weaker auth path to reach the cluster.

Independent of either mode, there is one additional accepted credential: a
bearer that exactly matches `EVE_AMBIENT_TOKEN`, presented with an
`x-eve-on-behalf-of: <sub>` header, authenticates as that roster member. This
is not a third `EVE_AUTH_MODE` — production runs `oidc` and this credential
has to work there too, so `_ambient_subject` (`src/eve/auth.py`) checks it
*before* the configured mode's own path rather than replacing it. The header
is inert on every other path: a member's own token carrying
`x-eve-on-behalf-of` still authenticates as that member, because
`_ambient_subject` only reads the header once the presented bearer has
already matched the ambient token. See [ADR 0007](adr/0007-ambient-impersonation.md)
for why this exists and what it costs.

**Personal access tokens** are the second mode-independent credential, and
exist because the ambient token is one shared secret that can impersonate
anyone: rotating it after a laptop goes missing logs out every client at once.
A PAT (`src/eve/pat.py`) names one member, belongs to one client, and is
revoked on its own.

```
uv run eve-pat mint <sub> <label>    # prints the token once
uv run eve-pat list                  # live tokens, with last-used
uv run eve-pat revoke <label>
```

The token is presented as an ordinary bearer and needs no extra header — a
`langgraph_sdk` client is just `get_client(url=..., headers={"Authorization":
f"Bearer {token}"})`. Notes on the shape:

- Only the sha256 is stored, in `eve_pat` (migration `0004_pat`,
  `src/eve/memory/db.py`). There is no way to recover a lost token; mint
  another.
- The `evepat_` prefix routes a bearer to the table. Without it every OIDC
  JWT would cost a database round trip, and `subject_for` short-circuits on
  the prefix instead.
- A PAT-shaped bearer that does not resolve is refused as a PAT rather than
  falling through to the JWT decoder, which would report `Not enough
  segments` for a credential whose only problem is that it was revoked.
- The lookup is uncached, so revocation takes effect on the next request. It
  is one primary-key read on the pool memory already keeps open.
- `x-eve-on-behalf-of` is ignored on this path. Impersonation belongs to the
  ambient token alone, or every PAT would be an ambient token.
- Authentication still ends at `family.yaml`, so removing a member revokes
  their tokens implicitly.
- No expiry column: revocation is the lever. See the `ponytail:` note in
  `src/eve/pat.py` for when to add one.

**Authorization is enforced by two mechanisms layered on top of each
other**, and the distinction matters when reading test failures:

1. Aegra itself pre-filters threads by `ThreadORM.user_id == user.identity`
   *before* any handler in `auth.py` runs. This is why cross-member thread
   access — read, resume (`create_run`), delete — returns **404**, not 403:
   Aegra declines to even confirm the thread exists to a non-owner.
2. `src/eve/auth.py`'s handlers are defense in depth on top of that.
   `only_own_threads` is the one that is genuinely applied: `threads.py:238,
   268, 834` pass its returned dict through `build_metadata_filter` and AND it
   into the query, so search, read and delete are filtered twice.
   `stamp_thread_owner` stamps `metadata.owner` on creation, but Aegra stamps
   it itself, unconditionally and from the authenticated user, at
   `threads.py:200` — ours changes nothing in this version. It is kept because
   the SDK's documented contract is that the returned filter is applied, and
   because being wrong in the safe direction costs nothing.
   `deny_by_default` (`@auth.on`) fails closed for any resource/action
   without an explicit handler — including one operators are likely to
   forget, like the resume path — because the SDK otherwise lets an
   unmatched request through unfiltered. This is the one handler here that is
   unambiguously load-bearing: nothing in Aegra stops an authenticated family
   member from creating, updating or deleting assistants, or from touching
   any of the four `crons` actions. `deny_by_default` does.

**Store API isolation is Aegra's, not ours.**
`scope_store_to_member` mutates `value["namespace"]` and returns `None`.
Aegra's store routes read only the **return value** (`api/store.py:44-51`:
`if filters: if "namespace" in filters: request.namespace = filters["namespace"]`),
so the mutation is discarded and the handler is inert *as a scoping
mechanism*. What it does do is matter as the **allow** — without a handler
matching `store`, `deny_by_default` would block store access outright.

What actually isolates the store is Aegra's own `apply_namespace_scoping`
(`api/store.py:289-310`), which buries every namespace under
`["users", <identity>, ...]` unconditionally. It cannot be escaped by a
crafted client prefix: a client sending `["users", "<someone-else>"]` lands at
`["users", "<caller>", "users", "<someone-else>"]`. The isolation is real and
stronger than ours would have been.

Phase 2 did not use this store. Eve owns `eve_memory` and enforces profile,
household, episodic, and digest scope in its SQL queries. Consequently neither
`scope_store_to_member` nor the available `aegra.json` `store.scopes` lever was
used to implement memory. The handler remains only as the allow rule for
clients that use Aegra's separate Store API.

One consequence worth stating plainly: **run operations authorize under
`resource="threads"`, not `"runs"`.** `aegra_api/core/auth_registry.py`'s
`ROUTE_AUTH_MAP` never dispatches a route under a `runs` resource; run
creation, reads, and deletes all go through `threads` actions
(`create_run`/`read`/`delete`). `only_own_threads` already covers them for
that reason, and `deny_by_default` needs no `runs` carve-out.
`test_run_is_not_blocked_by_authorization` in `tests/test_integration.py` is
the regression guard if a future Aegra version changes that dispatch.

`assistants.read` and `assistants.search` are allowed unconditionally
(`allow_assistant_read`/`allow_assistant_search`): the `eve` graph is shared
configuration, not per-member data, and a LangGraph client needs to look it
up before it can run a conversation at all.

The family roster itself (`family.yaml`) holds no secrets — name, role,
timezone, and permission strings per member — so it lives in git and changes
by pull request. Permissions are resolved into `EveState` in Phase 1; Phase 3
enforces them at the tool boundary (`src/eve/specialists/permissions.py`),
described in "Specialists and skills" below.

## Memory

Eve owns one `eve_memory` table with four layers that share a shape but have
different retrieval policies:

- **Profile** facts belong to one member and are always loaded for that member.
- **Household** facts are shared and always loaded for every family member.
- **Episodic** events and decisions are retrieved on demand by hybrid search.
- **Digest** is one rolling summary scoped to a thread.

Retired rows remain as history. `superseded_why IS NULL` is the live predicate
used by partial indexes and reads. A contradiction points `superseded_by` at
its replacement; an eviction has `superseded_by=NULL` but is still retired by
`superseded_why='evicted'`. Only an explicit request to forget hard-deletes.

Recall starts the full-text/entity and embedding arms together. The lexical arm
cannot depend on the network; the vector arm is fused in only if its embedding
lands within `EVE_MEMORY_RECALL_EMBED_BUDGET_MS` (120ms by default). Otherwise
the turn continues with always-on memory and lexical episodic results. Episodic
recency uses true half-life decay,
`exp(-ln(2) * age_days / half_life_days)`, evaluated at read time.

The schema is installed by the same `eve-migrate` console script as before,
under the same Postgres advisory lock, and the production Dockerfile still
runs exactly `eve-migrate && exec aegra serve` in its `CMD`, so schema failure
prevents Aegra from starting; `eve-migrate`'s contract is unchanged by what is
underneath it. Local `aegra dev` does not execute the container command, so
run the migration explicitly after starting Postgres.

What changed underneath: `db.py`'s hand-rolled `MIGRATIONS` list reached five
entries in Phase 5b — the point its own module docstring named as the trigger
to move to Alembic — and Phase 5c's `eve_tool` table would have been a sixth.
Rather than add a sixth hand-rolled entry, `eve-migrate` now shells out to
`alembic upgrade head` (`src/eve/memory/db.py`'s `migrate()`) under the same
advisory lock the list used, against Eve's own `alembic/` tree
(`script_location`) and, critically, a *private* `version_table` —
`eve_alembic_version`, set in `alembic/env.py` — so Aegra's own Alembic
migrations, which run separately at startup against the default
`alembic_version` table, can never interleave with Eve's. `MIGRATIONS` itself
is kept as an empty list rather than deleted, so an old assertion pinning its
old shape fails loudly instead of silently importing nothing.

Six revisions exist in `alembic/versions/`, not the two originally planned:

- **`0001_baseline`** reproduces the five hand-rolled entries idempotently —
  every statement is `IF NOT EXISTS` — so it is a no-op against an
  already-migrated database and a full create against a fresh one.
  `eve_schema_version` is left in place and unused rather than dropped, so a
  rollback to the previous image does not fail on a table it still expects.
- **`0002_eve_tool`** creates the `eve_tool` table (see "Sandboxed tools"
  below) and its one live-version guarantee, the partial unique index
  `eve_tool_live_name`.
- **`0003_eve_tool_pending_dedup`**, added during this branch's own review
  rather than planned up front, closes a race in `store.propose()`'s
  interrupt-replay dedup guard: the existence-check-then-insert it uses has no
  `SELECT ... FOR UPDATE`, so two genuinely concurrent proposals could both
  pass the check before either commits. This revision adds a second partial
  unique index, `eve_tool_pending_dedup`, giving the pending case the same
  backstop the approved case already had. See
  [ADR 0011](adr/0011-alembic-with-a-private-version-table.md) for the full
  rationale.
- **`0004_eve_computer_task`** creates the `eve_computer_task` table (see
  "Eve's computer" above).
- **`0005_eve_oauth_token`** creates the `eve_oauth_token` table —
  `(provider, member_sub)`-keyed rows holding each member's WHOOP and Oura
  OAuth credentials, the one table `eve-tools`' own restricted role may
  touch (see "Specialists and skills" and
  [ADR 0016](adr/0016-eve-tools-owns-a-credential-table.md)).
- **`0011_eve_routine`** creates the `eve_routine` table (see "Routines"
  above): `cadence` is jsonb, validated in Python rather than by columns, the
  same choice `0010_eve_widget_resource` made for the widget recipe; an index
  on `(status, next_run_at)` is the ambient tick's one query for "every
  active routine that is due," and an index on `(member_sub, created_at)`
  serves the owner-facing list.

## Ambient

Phase 4 adds a second deployment, `eve-ambient` (`Dockerfile.eve-ambient`,
`src/eve_ambient/`), that watches for things worth telling a member about and
speaks first. It imports `src/eve`'s `settings`, `family`, `models`, `memory`,
and `specialists.permissions` modules plus its own package, and holds no
Gmail, CalDAV, Home Assistant, or Monarch credential of its own — every
third-party read goes through `eve.tools_client.invoke` to `eve-tools`, the
same isolated credential-holding service specialists call (ADR 0006). The
only third-party credentials `eve-ambient` itself holds are the
impersonation token (below), the Home Assistant webhook secret, and the ntfy
push token — it also holds `EVE_TOOLS_API_KEY` (to call `eve-tools`) and the
database URL (for the two tables below), neither of which is third-party.

**Sources.** Registered in `sources/__init__.py`'s `SOURCES` tuple: `calendar`
and `mail` are polled once per family member holding the source's permission;
`finances`, `computer`, `coding`, and `routines` are each polled once for the
household (`per_member=False`), since each already carries its own member on
every row or signal it produces. `home` is deliberately absent from that
tuple — it is pushed, not polled: Home
Assistant's own automations decide what is worth Eve's attention and POST it
to `/signals/home-assistant`, authenticated by a shared secret compared with
`compare_digest` (`app.py`). The webhook contract (needed by whoever authors
that Home Assistant automation, prerequisite P3): the secret travels in an
`x-eve-ambient-secret` header, matching `EVE_AMBIENT_HA_WEBHOOK_SECRET`, and
the JSON body is `{entity_id, state, friendly_name, occurred_at}` — only
`entity_id` is required; the rest fall back to sane defaults
(`sources/home.py:from_webhook`). The polled sources run every
`EVE_AMBIENT_POLL_INTERVAL_SECONDS` (default 300s); the calendar source asks
`eve-tools`' CalDAV client for everything inside a horizon,
`EVE_AMBIENT_CALENDAR_HORIZON_DAYS` (default 14 days), but only treats an
event as "starting soon" if its start falls inside the shorter
`EVE_AMBIENT_CALENDAR_LOOKAHEAD_MINUTES` (default 90 minutes) — the wider
horizon exists so a change to an event still days out is detected as soon as
it happens rather than only once it becomes imminent.

**The gate chain**, all in `pipeline.py`'s `handle_signal`, cheapest check
first so nothing expensive runs until everything cheap has agreed:

1. **Cooldown** (`store.is_fresh`): has this exact `(source, key)` been seen
   inside `EVE_AMBIENT_COOLDOWN_HOURS` (default 6)? A source can override its
   own signal's cooldown — a still-over budget uses 720 hours so it is not
   re-announced four times a day.
2. **Relevance filter** (`filter.judge`, `REFLEX` tier): produces a
   `FilterVerdict` (`notify`, `audience`, `urgent`, `why`) or raises
   `FilterError` if the model call itself failed. A `FilterError` is a
   couldn't-decide, not a decided-no, so the pipeline leaves the signal
   unseen for the next poll to retry rather than resolving it as filtered.
3. **Owner-scoping and permission** (`gates.scoped_audience`,
   `gates.permitted`): a `mail` signal is narrowed to its own owner
   regardless of who the filter named, because mail content may not be
   redistributed; every remaining candidate must hold the source's mapped
   permission (`calendar.read`, `mail.read`, `finances`, `home.control`) or is
   dropped.
4. **Per-member idempotency** (`store.already_notified`): a member who
   already has this signal — the survivor of an earlier partial defer — is
   skipped rather than re-notified.
5. **Quiet hours and the daily cap**: `EVE_AMBIENT_QUIET_HOURS` (default
   `"21:00-07:00"`, evaluated in the member's own timezone) and
   `EVE_AMBIENT_DAILY_CAP` (default 6, counted per member per local calendar
   day from `eve_ambient_notice`). Both are skipped — and the bypass is
   logged plainly — when the filter marked the signal `urgent`. Urgency never
   bypasses the permission gate above it: a member without the permission a
   source requires is dropped at step 3 regardless of urgency.
6. **The compose turn** (`notify.deliver`): the only expensive step, and the
   only one that can fail for reasons that are not a verdict at all.

`deliver` creates a thread under the ambient credential (see "Auth and thread
scoping" below) and runs the ordinary `eve` graph on it — nothing in
`src/eve/graph.py` knows ambient exists. The input is an ordinary
`HumanMessage`, not a developer message, because `recall.py` and `extract.py`
both key off the last human message; a developer-role input would silently
cost the turn its episodic recall and half its extraction. The message is
marked (`"[ambient signal — not spoken by {member}]"`) so the thread shows
what prompted Eve, and its instructions ask her to reply with exactly
`NOTHING` if the signal is not worth interrupting anyone over. `deliver` has
exactly three outcomes, and they mean different things: a thread id (Eve
spoke; the message was pushed and the thread kept), `None` (Eve produced
`NOTHING` or an empty answer — both read as a deliberate veto; the thread is
deleted and nothing is pushed), or a raised `DeliveryError` (thread creation
failed, the run itself failed, or no final assistant message could be found
at all — infrastructure failed, not Eve choosing silence). The pipeline
treats `DeliveryError` exactly like a
`FilterError`: the signal stays unseen so the next poll retries it, and
`already_notified` (step 4) is what keeps that retry from re-notifying
members who already got it on the failed attempt.

**The two tables**, installed by the `0002_ambient` migration in
`src/eve/memory/db.py` — there is no cursor table, because every source is
either time-windowed (calendar, by horizon) or content-keyed (a Gmail message
id, a Monarch transaction id, an entity/state pair), so this pair alone gives
exactly-once delivery:

- `eve_ambient_seen (source, key, last_seen_at)` — one row per resolved
  signal (dropped by a gate, vetoed, or delivered), written only once a
  signal is fully resolved so a crash mid-handling loses nothing.
- `eve_ambient_notice (id, member_sub, source, key, urgent, thread_id,
  sent_at)` — one row per notification actually sent. This table *is* the
  daily-cap counter: step 5 above counts rows here since the member's local
  midnight.

**First-poll priming.** A freshly enabled source must not announce every
event, unread message, or transaction that already existed before Eve was
watching. `app.py`'s `poll_once` checks `store.has_any(source.name)`; if a
source has never produced a signal before, the current tick marks every
signal it just found as seen without notifying, and then marks the source
itself with an explicit sentinel key (`_PRIMED_SENTINEL`,
`store.mark_seen(source.name, "__primed__")`). The sentinel is deliberate
rather than inferred from "has any seen row": an empty first poll (nothing
unread, nothing over budget) would otherwise leave no row behind at all, so
the next tick — the first one to actually find something — would still read
as unprimed and get silently primed away instead of notified.

`computer` and `routines` are both exempt from priming entirely (`app.py`'s
`poll_once` checks `source.name not in ("computer", "routines")` before
even looking at `has_any`): each one's signal is always a direct response
to something a member explicitly asked for — a dispatched computer task or
a routine they created — so silently priming it away on its very first
occurrence would drop something they're waiting on rather than a stale
backlog. Any future `per_member=False` source should check against this
same rationale before joining `SOURCES`.

**Pruning.** `_poll_forever` calls `store.prune_seen()` after every tick,
which deletes `eve_ambient_seen` rows older than its 30-day default horizon
so the table does not grow forever. The `__primed__` sentinel is explicitly
excluded from that delete: without the exclusion, a source that produces
nothing for 30 days would have its priming row pruned right along with
everything else, `has_any` would go back to reporting false, and that
source's next real signal would be silently primed away instead of notified
— the very failure priming exists to prevent, just delayed a month. The
30-day default is deliberately equal to `BUDGET_COOLDOWN_HOURS` (720 hours,
`sources/finances.py`) — see the comment there for why moving one without
the other makes every budget overrun re-fire.

**One replica only.** Nothing in `eve-ambient` elects a leader or coordinates
across instances; the poll loop and the webhook handler both run in one
process. A second replica would poll and push the same signals again and
double-count the daily cap in `eve_ambient_notice`.

`/signals/linear`, described in full below, shares this webhook posture
(verify, acknowledge fast, do the real work in a background task) but
deliberately bypasses the gate chain above: quiet hours and the daily cap
exist to protect the family from unrequested interruptions, and would
misfire on work a family member explicitly asked for by delegating an issue.

## Linear

Phase 6 (EVE-26) makes Eve a Linear agent: a family member assigns her an
issue and she works it as a coding session, narrating progress back into
Linear's own activity feed rather than a chat thread. The design and the
implementation plan are in
[`docs/superpowers/specs/2026-09-21-eve-linear-agent-design.md`](superpowers/specs/2026-09-21-eve-linear-agent-design.md)
and
[`docs/superpowers/plans/2026-09-21-eve-linear-agent.md`](superpowers/plans/2026-09-21-eve-linear-agent.md).

**The endpoint and its verification.** `POST /signals/linear`
(`src/eve_ambient/app.py`) lives beside `/signals/home-assistant` in
`eve-ambient` and follows the same shape: read the raw body before parsing
it, because a signature covers the exact bytes Linear sent and re-serialized
JSON would not match; verify an HMAC-SHA256 signature in the
`linear-signature` header against `EVE_LINEAR_WEBHOOK_SECRET` with
`hmac.compare_digest`; check `webhookTimestamp` is fresh, rejecting a stale
or missing one as a replay rather than a malformed payload. Only `created`
(a fresh delegation) and `prompted` (a reply, or an answer to an
elicitation) are handled; every other action Linear can send is
acknowledged and dropped so Linear does not retry an event this feature will
never process. An in-flight set keyed by the Linear session id collapses a
concurrent duplicate delivery before it ever reaches the handler. Linear
expects a response within 5 seconds and a first activity within 10 of
`created`, so the handler (`src/eve_linear/handler.py`) is deliberately
ordered: the three gate checks (identity, permission, the repo allowlist)
run first because they are pure local computation, and only after the
acknowledgement activity is emitted does anything that can block on a
network or the database run: memory recall, the Aegra thread, the call to
eve-computer.

**The credential split.** The webhook signing secret and the OAuth token
that actually reaches Linear's API are deliberately not the same
credential, in the same place, for the same reason as every other
third-party integration (ADR 0006): verification and action are separable.
The signing secret proves a request came from Linear and grants no
authority over the workspace, so it lives in `eve-ambient`
(`EVE_LINEAR_WEBHOOK_SECRET`) beside the Home Assistant webhook secret. The
OAuth token can create activities, move issues, and act as Eve in Linear, so
it lives in `eve-tools` behind the `linear.*` handlers
(`src/eve_tools/linear_client.py`), exactly like every other credential
that speaks to the outside world. See the ADR 0006 amendment below for the
fuller argument. There is deliberately no `set_delegate` call (EVE-42
removed it). Linear's `delegateId` only accepts an agent (app) user and
rejects a human with "delegateId must correspond to an app user", so it
cannot hand an issue back to the member when a session escalates or ends
blocked. Setting it to Eve is also redundant: a session only exists because
the issue was already delegated to her. "Waiting on the human" is instead
signalled by the `elicitation` activity, which moves the agent session to
awaiting input.

**Decision to activity mapping.** Every decision Eve or the supervisor makes
about a Linear-originated session becomes a Linear activity
(`src/eve_linear/activities.py`), one of five server-validated shapes:
`thought` (the ten-second acknowledgement, and the heartbeat), `elicitation`
(an answerable refusal, or the supervisor's `escalate` decision: a question
only the family member can resolve), `error` (an unanswerable refusal, a
dispatch failure, a killed or timed-out session), `action` (the supervisor's
`reply` decision, narrating what it told the coding agent), and `response`
(the supervisor's `done` decision, with pull request links when there are
any). `activities.emit` never raises: losing the narration is recoverable,
but failing a running coding session over a GraphQL hiccup is not. It also
stamps the heartbeat clock in `eve_coding_session.linear_emitted_at`, and
only on a successful emission, so a failed one cannot make the heartbeat
believe Linear has heard from Eve when it has not.

**The allowlist as the injection boundary.** Issue text and guidance reach
an agent that writes code and opens pull requests, so the one thing that
text can never widen is which repositories are reachable.
`EVE_LINEAR_REPO_ALLOWLIST` is read from settings, never from anything
Linear sent, and `resolve_repos` (`src/eve_linear/identity.py`) only ever
narrows a request down to the allowlist's intersection: an issue asking to
work in a repo outside it gets a refusal, not a wider grant. This is the
same posture as `eve-computer`'s `NetworkPolicy` and the family roster's
permission checks: the boundary is enforced by something the untrusted input
cannot touch.

**The heartbeat.** Linear marks an agent session stale after 30 minutes of
silence, and a coding agent can legitimately work far longer than that
without producing a supervisor decision. `supervisor._heartbeat` checks, on
every tick of a live session that has a `linear_session_id`, whether
`EVE_LINEAR_HEARTBEAT_MINUTES` (default 10) has elapsed since the last
emission or the session's creation, and if so emits a `thought` naming the
coding agent's most recent activity. This is purely cosmetic against
Linear's own staleness clock, since the underlying state is fully
recoverable and a later activity un-stales it, so the heartbeat exists to avoid
inviting a human to intervene in work that is, in fact, still going fine.

The webhook reaches the cluster through a path-scoped Ingress at
`eve-ambient.chalifour.dev`.

## Tool labels

The client renders agent work as a one-line ticker above the answer, naming
the current activity while the turn runs and then collapsing into a timed
summary that expands into per-tool detail. That line names each tool call.

**The problem.** With no server-supplied label the client sentence-cases the
raw tool name, so `dispatch_computer_task` reads as `Dispatch computer task`.
Accurate, mechanical, and it leaks our function naming into a member-facing
conversation.

**The frame.** `{"tool_labels": {<raw tool name>: <activity phrase>}}` on the
`custom` channel — the third frame on the channel that already carries
`assistant_ui` and `suggestions`, and no coordination between them: the
client reads one key per frame and ignores the rest.

The key is the **raw tool name**, as it arrives in `tool_call_chunks[].name`
and `ToolMessage.name`, never the per-invocation call id. The client maps name
to id itself.

**Emitted once, early.** `eve` emits the whole map on its first round of a
turn, before any tool call can start, guarded on the same
`_tool_rounds_this_turn` counter that bounds the tool loop. The client applies
a label that arrives before, during or *after* the call it names, so ordering
is not load-bearing — which is exactly why the simplest option is the right
one. Re-sending every round would be idempotent for the member and six times
the frames for one unchanging dictionary.

A turn that calls no tool still emits its labels. They describe what `eve`
*could* call this turn, not one call; waiting for a call to exist would put
the frame after the call on every fast tool.

**Scoped to the bound tools.** `_labels_for` intersects the table with the
tools `_static_tools` actually returned, so the five feature switches and the
client's `assistant_ui` declaration gate each label along with its tool. A
client is never told the name of something this deployment cannot call.

**Static tools only.** A materialized `DynamicToolSpec` is named
`{server_id}_{tool_name}` at runtime and its only prose is a model-facing
description of arbitrary length. Mechanically shortening one would produce
exactly the stilted copy this exists to remove, so dynamic tools fall back to
sentence-casing — the documented client behaviour, not a failure.

**Validation, server-side.** `eve.ui.stream.sanitise_tool_labels` applies the
client's own rules before the write: `Map<String, String>`, both sides
trimmed, blanks skipped pair by pair, and a hard ceiling of
`MAX_TOOL_LABEL` = 60 characters. The client re-validates and drops what fails
**silently**, degrading to the sentence-cased raw name — which is
indistinguishable from never having shipped labels. Validating here turns a
violation into a test failure instead of an invisible production regression;
`test_every_tool_label_reads_like_an_activity` runs the whole table through
the same function for that reason.

**The copy is product copy**, and `docs`-worthy because it appears
mid-conversation in the member's reading flow: present participle, sentence
case, no terminal period, no trailing ellipsis (the client draws its own
progress affordance), no tool jargon, under ~40 characters in practice. The
test: each should finish the sentence "Right now it is …".

**Nothing here may cost an answer.** `emit_tool_labels` returns `False` and
never raises, the same posture as `stream.emit` and the `suggestions` frame:
no runnable context is a quiet `debug`, a writer that raises is a `warning`
with a count and no label text, and the turn proceeds either way. There is no
setting — unlike `EVE_SUGGEST_ENABLED`, this reaches nothing outside the
process, writes nothing durable, and costs no model call.

## Reply suggestions

`src/eve/suggest.py`, one node, one `REFLEX` call, no storage of its own.

**What it produces.** 2-4 candidate next utterances *by the member*, first
person, short enough to render in a pill: "Yes, do it", "What about
tomorrow?", "Only the kitchen ones". A chip is text the member might have
typed, so tapping one produces an ordinary `HumanMessage` and there is no
inbound protocol to learn. The wire type is `list[str]` - no ids, no types,
no actions.

**Validation.** At most 4 entries, each at most 80 characters after trimming,
empties dropped. There is no minimum: a response validating down to one good
chip ships that one chip. Validation takes `object`, not `list[str]`, so a
provider or langchain change that returns a bare dict produces no chips
rather than an `AttributeError` inside the graph.

**Delivery, two exits from one helper.** A `{"suggestions": [...]}` frame on
LangGraph's `custom` stream channel, and the `suggestions` channel of
`EveState`. The frame is what the Flutter client consumes; the state channel
serves `GET /threads/{id}/state`, `stream_mode="values"`/`"updates"`, and
survives a reload. Both are written in one place so they cannot drift.

An empty list is always emitted rather than omitted. A turn that skips chip
generation must CLEAR the previous turn's chips - otherwise a client renders
continuations of a conversation that has moved on. This is also why the
`suggestions` channel has a reducer: `_last_write_wins` in `src/eve/state.py`,
shared with `dynamic_tools`, replaces rather than appends, and a reducer is
what gives the channel its `[]` default at all.

**`TAG_NOSTREAM` is mandatory** on the call, as it is on every REFLEX call in
`eve/memory/extract.py`. Without it the suggestion model's tokens go out on
the `messages` channel and every client renders them as Eve's reply.

**Settings.**

| Setting | Default | Effect |
|---|---|---|
| `EVE_SUGGEST_ENABLED` | `true` | Off skips the call entirely and clears chips. Default-on, unlike `EVE_AMBIENT_ENABLED` and `EVE_SANDBOX_ENABLED`, because this subsystem reaches nothing outside the process and writes nothing durable. |
| `EVE_SUGGEST_BUDGET_MS` | `1500` | Ceiling on how long the run stays open after Eve's last token. Exceeded means no chips, not a delayed turn. |

**Skips.** No chips for an ambient-driven turn (not a member speaking, and the
reply goes to ntfy rather than a chat surface - this also saves a REFLEX call
per household signal), for the loop-exhausted reply, or for a turn with no
human message. All three are checked before the model is constructed.

**Observability.** `eve.suggest.outcome` is the one number to look at:
`ok` / `empty` / `budget` / `malformed` / `error` / `skipped` / `disabled`,
plus `eve.suggest.count` and `eve.suggest.latency_ms` (set on every path
where the model call actually happened, including `budget` and the failure
outcomes). Because every failure degrades to an empty list, total failure is
invisible without this attribute - chips simply stop appearing and nothing
raises. A rising `budget` fraction means the budget is too tight or the tier
too slow; `eve.suggest.latency_ms` is what shows that *before* `outcome`
turns to `budget`, not after.

**Eval.** `eve/eval/replay.py` injects a no-op through `build_graph`'s
`suggest_fn` seam, so replays neither pay for chips nor score them.

Chips are deliberately NOT modelled as an `assistant-ui/1.0` surface: that
protocol allowlists `actionId` to exactly `surface.submit`, and a tapped
surface button sends an `<assistant-ui-action>` JSON envelope as the user
text rather than a plain member utterance.

### Openers — chips for an empty chat

`suggest` answers "what next?", which needs an exchange to continue. The
screen a member actually lands on has none, so it got `[]` by design and the
Flutter client filled it with three hard-coded prompts. Those prompts are
gone; `openers` is what replaced them. See ADR 0018.

**How a client asks.** `config.configurable.suggestions_only = true` on an
otherwise ordinary run with empty input. `configurable`, not run metadata,
because LangGraph indexes metadata and rejects non-scalars there — the same
reason `assistant_ui` rides there. Read with `is True` and **fails closed**
to a normal turn: a flag that stops Eve answering must not be trippable by a
stray string.

**What the run does.** `load_context -> recall -> openers -> END`. It never
reaches `eve`, so there is no VOICE call and no answer. It appends **no
message**, so the thread stays exactly as empty as it was — nothing in the
transcript, nothing for `listSessions` to title, nothing for the next turn to
read as history. It skips `extract` (no exchange to mine) and `suggest`
(which would overwrite the openers with `[]`).

**Why it still runs `recall`.** Profile and rules are what make an opener
reflect who is asking rather than being a canned prompt with extra steps —
the whole reason this lives on the server. `recall` makes no embedding call
on an empty query, so this costs the always-on lookup only.

**What it reads.** The member's name, role and local time, plus profile and
rules — the same narrow bundle `suggest` uses, and deliberately no household
or episodic memory. No exchange section is rendered at all: a REFLEX model
shown `Noah:` followed by nothing fills the blank in, producing exactly the
continuation-shaped chip openers must not be. The prompt is
`prompts/openers.md`.

**Everything else is shared with `suggest`** — `clean`, the `custom` frame
and state-channel exits, `TAG_NOSTREAM`, `EVE_SUGGEST_BUDGET_MS`, and the
`EVE_SUGGEST_ENABLED` kill switch (one switch, so chips turned off cannot
reappear on a different route). Observability is a separate prefix,
`eve.openers.outcome`, so the two flavours stay separable in Langfuse.
`build_graph` grows an `openers_fn` seam alongside the other three.

**Transport.** The Flutter client sends this against `POST /runs/stream`
(stateless), so showing an empty canvas creates no thread row. Aegra
implements that endpoint with an ephemeral thread it deletes on completion
(`aegra_api/api/stateless_runs.py`); LangGraph Platform supports it too.

## Eval harness

Phase 5b answers the question Phase 5a raises — is the rule set Eve writes
for herself helping, doing nothing, or actively working against her — with a
command instead of an argument. One new console script, `eve-eval`
(`src/eve/eval/`), with no new service and nothing in the request path: it
imports Eve's own modules and calls them directly. See
[ADR 0009](adr/0009-eval-inputs-from-postgres.md) for why its inputs are
Eve's own Postgres tables rather than parsed Langfuse traces.

**Two dataset shapes**, both built by `eve-eval build`:

- **Shape 1 — ambient decisions.** One row per judged signal, recorded by
  `eve_ambient.store.record_decision` immediately after `filter.judge()`
  returns in `pipeline.handle_signal` — before the cap/quiet-hours/permission
  gates run, so the label is the filter's verdict, not the eventual outcome.
  Labelled with `eve_ambient_notice.replied_at`, stamped by `extract` when a
  member replies into an ambient thread (never on a turn carrying the
  ambient marker) — a weak positive signal only: a reply means the
  interruption was worth making, but no reply does not mean it wasn't. Both
  halves are forward-looking only; there is no history to backfill, so the
  shape is empty until some time after this phase deploys, and `eve-eval
  gate` skips an empty shape 1 rather than passing it.
- **Shape 2 — turn behaviour.** A dozen or two hand-written items in
  `tests/eval/turns.yaml`, each a member, a message, and natural-language
  `expects` assertions — small and reviewed like code, because it is the
  definition of "working" the A/B below measures against. One item is a
  deliberately-failing canary: if it ever passes, the judge is
  rubber-stamping and the gate fails on it.

**The A/B that justifies Phase 5a.** `eve-eval run` replays shape 2 twice:
once with authored `rule`-layer memory rendered into the system prompt
(`with-rules`, the normal path) and once with that section suppressed
(`without-rules`, everything else — profile, household, episodic, digest,
persona — identical). `rule_delta` is the difference in `assertion_pass`
between the two arms: positive means the rule set is earning its prompt
budget, flat means it is costing budget for nothing, and negative means the
rules have turned on themselves — the signal to reach for `eve-skill revoke`
or Phase 5b's own hygiene pass. Suppression is a parameter the eval package
passes to `build_system_prompt`; production code paths never set it.

**The judge runs on `DEEP`**, not `REFLEX` or `VOICE`. `assertion_pass`
needs a model to grade a natural-language assertion against a response.
`REFLEX` — the metered, free-tier Gemini route — was the original choice,
since every other tier is a subscription proxy sharing one `max_budget` with
Noah's own work (see the tier table above) and a judge on any of them would
make the harness the most expensive thing in the deployment for a narrow
classification task flash-lite is already good at. Every other scorer
(`notify_agreement`, `notify_precision`, `audience_exact`) is an exact
comparison against a recorded verdict or a recorded reply and costs nothing.

`eve-eval run` prints a spot-check of up to ten judged assertions with the
judge's one-sentence reason so a human can read them; the tier decision —
move to `DEEP` in `scorers.py` if agreement falls below ~85% — is made from
that reading. **The first real runs happened on 2026-08-31**, against
production data via the `eve` pod, in three attempts:

1. On `REFLEX`: of the 9 spot-checked lines (`turns.yaml` has 8 non-canary
   assertions + 1 canary, so `min(10, len(spot))` capped it there), 4 came
   back `[FAIL] ...: judge unavailable` — `REFLEX`'s free-tier Gemini quota
   (15 requests/minute) rate-limited outright, with no fallback model group
   configured for it. The other 5 produced real verdicts a human agreed
   with, but 5/9 (56%) is already below the ~85% bar once the rate-limited
   lines count as failed spot-checks, and a judge that cannot reliably
   answer at all is disqualifying regardless of accuracy on the calls that
   land. `rule_delta` that run was `-37.5`, but confounded: the same 4
   `judge unavailable` calls default to `passed=False`, and excluding them
   the assertions that *were* judged scored identically to `without-rules`
   (75%) — the number was mostly measuring `REFLEX`'s rate limit, not the
   rule set.
2. Moved the judge to `Tier.DEEP` and re-ran: every one of the 16
   `judge_assertion` calls failed with `judge returned an unusable
   response: Structured Output response does not have a 'parsed' field nor
   a 'refusal' field` — `with_structured_output`'s default `method=
   "json_schema"` doesn't work through this LiteLLM proxy for a
   `use_responses_api=True` model (`models.py`); every other
   `with_structured_output` caller in the codebase runs on `REFLEX`
   (chat-completions, not responses), so this combination had never been
   exercised before. `assertion_pass` was 0.0% on both arms and `rule_delta`
   a meaningless `+0.0`.
3. Added `method="function_calling"` to the `with_structured_output` call
   (verified first with a standalone call before spending another full run)
   and re-ran: clean, no judge errors. **Spot-check agreement: 8/9 (89%)** —
   a human agreed with 8 of the judge's 9 verdicts on the reasoning given;
   the one debatable call marked `the-other-member-gets-the-same-treatment`
   FAIL for citing a technical reason (data source needs reauthorization)
   rather than a policy refusal, which a stricter reading of the assertion
   ("does not refuse to answer or treat the question as forbidden") could
   call PASS. 89% clears the ~85% bar, so `DEEP` + `function_calling`
   stands. **`rule_delta`: -12.5** (`with-rules` 62.5% vs `without-rules`
   75%, both /8, a one-assertion swing) — a real, uncounfounded number this
   time, but thin: `turns.yaml` has only 8 non-canary assertions, so this is
   not yet strong evidence either way on Phase 5a's rule set. Re-run
   periodically as the dataset grows before treating a negative `rule_delta`
   as a verdict on the rules.

**The gate never calls Langfuse.** `eve-eval run` writes every score to
`eve_eval_run` in Postgres first; publishing a Langfuse dataset run is
best-effort, and its failure is logged and ignored — the same posture
`extract` takes toward its own writes. `eve-eval gate` reads only
`eve_eval_run`, compares the newest run against the previous one on the same
dataset and arm, and exits non-zero past a threshold (`notify_agreement`
down more than `EVE_EVAL_REGRESSION_POINTS`, `audience_exact` down at all,
`assertion_pass` down more than the same threshold, or `rule_delta`
negative). A reporting outage in Langfuse can therefore never block a
regression check. `eve-eval hygiene` is separate and report-only by default
(`EVE_EVAL_HYGIENE_APPLY_ENABLED=false`): it finds duplicate rules by
embedding similarity and can supersede the weaker with `--apply`, but only
reports contradictions and dormant rules — resolving a conflict is a
judgement call for a human, not a flash-lite model unattended.

The harness is designed to run on demand and, eventually, weekly via a
`CronJob` in the `infrastructure` repository, never in CI: its calls are paid
and nondeterministic, so gating merges on it buys flaky builds and a budget
bill. **That CronJob and its container image do not exist yet**, and Phase 5c
did not build them either — 5c's packaging work was `eve-sandbox`, a
different service entirely. Today, `eve-eval build`/`run` require a working
directory that contains `tests/eval/turns.yaml` (or whatever
`EVE_EVAL_TURNS_FILE` is pointed at instead): none of this repo's four
Dockerfiles copy `tests/`, so the harness cannot run inside any image built
from them yet. This remains open past the end of the five-phase program (see
"Sandboxed tools" above); picking it up is `infrastructure` and packaging
work, not a gap in what this repository's own code does.

## Running locally

```bash
cp .env.example .env                      # dev auth mode, local ports
docker compose -f docker-compose.test.yml up -d   # Postgres (15432), Redis (16379)
uv run eve-migrate
uv run aegra dev
```

`docker-compose.test.yml` deliberately maps the containers to non-default
host ports (`15432`, `16379` rather than `5432`/`6379`) so the test stack
doesn't collide with other Postgres/Redis instances a developer machine is
likely already running. Container-internal ports are standard.

## Running the tests

Four tiers, matching the pytest markers declared in `pyproject.toml`.
`addopts` deselects `integration`, `live`, and `docker` by default, so a bare
`pytest` is the unit tier; an explicit `-m` on the command line replaces that
expression rather than adding to it.

```bash
# Unit — no network, no services (the default; the -m is explicit for clarity)
uv run pytest -m "not integration and not live and not docker"

# Integration — real Postgres, Redis, and a live `aegra serve`
docker compose -f docker-compose.test.yml up -d
uv run pytest -m integration

# Docker — builds the real eve-sandbox image and hits a running container
# over HTTP (tests/test_sandbox_docker_image.py); the regression coverage
# for a bug class no in-process test can see, since a dev checkout's
# editable-install .pth file masks how the built image actually resolves
# imports
uv run pytest -m docker

# Live — hits the real LiteLLM proxy and spends real quota
EVE_LIVE_TESTS=1 uv run pytest -m live
```

Unit tests exercise the graph, persona assembly, state shape, the auth
handler (valid/expired/wrong-audience/unknown-key tokens, `dev` mode refused
in production), and `family.yaml` loading — all against fakes, no network.
Integration tests spin up `aegra serve` itself against the compose stack and
drive it through `langgraph_sdk`, covering thread creation, persistence,
cross-member access denial, memory SQL, and the run/resource-scoping behavior
described above. The two tests that require a successful full graph turn skip
when `EVE_LITELLM_API_KEY` is absent; a passing integration tier with those
skips is not end-to-end Aegra evidence. Live tests (`tests/test_live_models.py`)
are the tier that verifies
the `chatgpt/*` Responses-API assumption in `models.py` against the real
proxy — response shape, incremental streaming, and tool calls.

Ambient's own tests use the same two tiers rather than a third.
`tests/test_ambient_integration.py` needs the same compose stack as every
other integration test — it drives the ambient impersonation credential
against a live `aegra serve` and asserts a member can read a thread ambient
created for them while another member gets a 404. `tests/test_ambient_live.py`
additionally needs an ntfy topic (`EVE_AMBIENT_NTFY_BASE_URL`,
`EVE_AMBIENT_NTFY_TOPIC`) and drives one fabricated signal all the way
through a real `REFLEX` verdict, a real `eve` turn, and a real push.

One gotcha specific to the ambient tests: `notify.deliver` runs inside the
pytest process itself, not inside the `aegra_server` fixture's subprocess, so
it reads `EVE_AMBIENT_TOKEN` and `EVE_AMBIENT_AEGRA_BASE_URL` from the
*runner's own* shell environment. The `aegra_server` fixture setting
`EVE_AMBIENT_TOKEN` in the subprocess `env` dict it launches `aegra serve`
with is not enough — that only lets the server *verify* the credential; the
test process still needs the same token (and the server's URL) exported in
its own shell to *present* it. Without that, `deliver` fails on
infrastructure grounds (a 401, or the wrong base URL) and reads like a test
failure rather than incomplete setup.

The live ambient tier has never been run. Its four prerequisites from the
design — CalDAV credentials, a reachable ntfy instance, the Home Assistant
automation that posts to the webhook, and the ambient token provisioned in
Vault — are all still outstanding. The assertions exist; none of them have
executed against real infrastructure.

## Observability

Tracing is configuration, not code: there is no application-level callback in
this repository, and none is needed. Aegra emits the spans itself when these
are set in the environment (see `.env.example`, where they are commented out
so local runs do not trace):

```bash
OTEL_TARGETS=LANGFUSE
LANGFUSE_BASE_URL=https://langfuse.chalifour.dev
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

Per-member attribution is native. `create_run_config` passes `user.identity`
into `get_tracing_metadata` (`services/langgraph_service.py:747-748`), and
`observability/span_enrichment.py:113-118` sets `langfuse.user.id` and
`langfuse.session.id` (the thread) on the trace. Graph and model spans land
under the one trace because both are emitted by the same instrumented process.
Nothing here needs to ride on thread `owner` metadata.

## Deployment

Deployment manifests do not live in this repository. Per existing lab
convention, they belong in the `infrastructure` repository at
`kubernetes/apps/eve/{base,overlays/homelab}` (Deployment, Service, Ingress,
ExternalSecret, the CNPG Postgres cluster, the Redis CR, and a scheduled
backup), reconciled by ArgoCD and checked by Gatus. This repository's
responsibility ends at building and publishing the image
(`ghcr.io/noahchalifour/eve-ai`) via `.github/workflows/build.yml`; see spec
§12 for the full deployment design.

Phase 5c adds `eve-sandbox` to that same `infrastructure` app: a Deployment
(`automountServiceAccountToken: false`, `readOnlyRootFilesystem: true`,
`runAsNonRoot`, a tmpfs `emptyDir` at `/tmp`, no `envFrom` beyond the API
key), a Service, a default-deny-egress `NetworkPolicy`, and a Gatus check on
`/healthz`. The `NetworkPolicy` is stricter than `eve-tools`': `eve-tools`
needs egress scoped to the specific external hosts it calls (Home Assistant,
Gmail, Monarch, CalDAV — ADR 0006), while `eve-sandbox` needs none at all, so
its policy denies egress outright. **No Ingress**: unlike `eve-ai`,
`eve-sandbox` is reachable only from `eve` inside the cluster, never from
outside it, since nothing external ever needs to invoke a sandboxed tool
directly. This repository's side of that is `Dockerfile.eve-sandbox`, which
follows `Dockerfile.eve-tools`'s pattern — same base image, `uv sync --frozen
--no-install-project`, non-root UID — but copies only `src/eve_sandbox`, so
the built image cannot contain a module that knows how to reach the database
or a credential even by accident.

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

## Decision records

- [ADR 0001 — Specialists are subgraph tools, not separate services](adr/0001-agents-as-subgraph-tools.md)
- [ADR 0002 — No model call may precede the first streamed token](adr/0002-no-llm-before-first-token.md)
- [ADR 0003 — The embedding model and dimension are pinned](adr/0003-embedding-model-pinned.md)
- [ADR 0004 — Model tier routing](adr/0004-model-tier-routing.md)
- [ADR 0005 — Memory storage: one table, supersession, read-time decay](adr/0005-memory-storage.md)
- [ADR 0006 — Specialist and skill tool execution runs in an isolated service](adr/0006-eve-tools-isolation.md)
- [ADR 0007 — Ambient runs impersonate family members through one scoped token](adr/0007-ambient-impersonation.md)
- [ADR 0008 — Eve-authored behaviour is memory, and authorisation never reads memory](adr/0008-authored-behaviour-is-memory.md)
- [ADR 0009 — Eval inputs come from Postgres, not from Langfuse traces](adr/0009-eval-inputs-from-postgres.md)
- [ADR 0010 — Sandboxed tools are pure functions, and the pod is the boundary](adr/0010-sandboxed-tools-are-pure-functions.md)
- [ADR 0011 — Eve's migrations use Alembic with a private version table](adr/0011-alembic-with-a-private-version-table.md)
- [ADR 0012 — Memory extraction is detached from the turn and joined by the next one](adr/0012-extraction-is-detached-and-joined.md)
- [ADR 0013 — Reply suggestions are a separate REFLEX call](adr/0013-suggestions-are-a-separate-reflex-call.md)
- [ADR 0014 — Dynamic UI surfaces are built server-side and only triggered by the model](adr/0014-dynamic-ui-is-server-built.md)
- [ADR 0015 — A granted identity is not authored credentialed capability](adr/0015-granted-identity-vs-authored-capability.md)
- [ADR 0017 — The model authors surface structure; the server owns the envelope](adr/0017-model-authored-surfaces.md)
- [ADR 0018 — Openers are a thread-free, chip-only run](adr/0018-openers-are-a-thread-free-chip-only-run.md)
- [ADR 0019 — One generic record store, and widgets are recipes over it](adr/0019-one-generic-record-store.md)
- [ADR 0020 — The harness route is a layer the pulled profile cannot reach](adr/0020-the-harness-route-is-a-layer-the-profile-cannot-reach.md)
