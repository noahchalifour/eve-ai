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
START -> load_context -> recall -> eve <-> tools -> extract -> END
```

Five nodes, wired in `src/eve/graph.py`:

- **`load_context`** (`src/eve/context.py`) performs no model call. It reads
  the authenticated principal from
  `config["configurable"]["langgraph_auth_user"]`, resolves the matching
  entry in `family.yaml`, stamps the member's local time, and assembles the
  initial system prompt from `prompts/eve.md` plus member context. It performs
  no memory I/O and makes no model call.
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
  sources/      # calendar.py, mail.py, finances.py (polled); home.py (pushed via webhook)
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
that reaches three domain specialists and one extensible skills layer,
without any of them holding a third-party credential directly:

- **`src/eve/specialists/`** — `home.py`, `mail.py`, and `finances.py` each
  wrap a small tool-calling agent built by `base.py`'s `build_specialist`
  (running on `Tier.MECHANICAL`) as a single opaque tool for `eve`;
  `permissions.py` enforces `family.yaml` permissions once at that
  specialist boundary and again inside `mail.py`'s `send_email`, which needs
  `mail.send` on top of the coarser `mail.read`/`mail.send` check on
  `ask_mail` itself.
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
  for them.

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

Three revisions exist in `alembic/versions/`, not the two originally planned:

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

**Sources.** Four exist, registered in `sources/__init__.py`'s `SOURCES`
tuple: `calendar` and `mail` are polled once per family member holding the
source's permission; `finances` is polled once for the household. `home` is
deliberately absent from that tuple — it is pushed, not polled: Home
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
