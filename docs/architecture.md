# Architecture

This document describes what exists in this repository today: Phase 2,
"Memory." For the Phase 2 design rationale and definition of done, see
[`docs/superpowers/specs/2026-08-18-eve-memory-design.md`](superpowers/specs/2026-08-18-eve-memory-design.md).
For the task-by-task build record, see
[`docs/superpowers/plans/2026-08-18-eve-memory.md`](superpowers/plans/2026-08-18-eve-memory.md).

## The graph

```
START -> load_context -> recall -> eve -> extract -> END
```

Four nodes, wired in `src/eve/graph.py`:

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
  included, then invokes the `VOICE` tier model (`src/eve/models.py`) and
  streams the response. The prompt is never appended to `messages`, so persona,
  member-context, and memory edits affect existing threads instead of being
  frozen into history.
- **`extract`** (`src/eve/memory/extract.py`) runs after the answer has streamed.
  The `REFLEX` model produces structured add, reinforce, supersede, and forget
  operations; valid writes, digest refresh, embeddings, and cap eviction are
  applied best-effort so extraction failure cannot erase a completed answer.

The latency contract in [ADR 0002](adr/0002-no-llm-before-first-token.md)
forbids a *generative* model call before the first streamed token.
`load_context` is pure local computation; `recall` is the one concession: a
single bounded and cancellable embedding call that can degrade to lexical-only.
Phase 3 wraps `eve` in a tools loop, per
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
  memory/
    types.py        # Memory/MemoryBundle and extraction schemas
    ranking.py      # pure decay, reciprocal-rank fusion, token budgeting
    db.py           # pool and advisory-locked ordered-DDL migrations
    embed.py        # Gemini embedding client, truncation, re-normalisation
    store.py        # every eve_memory SQL read and write
    recall.py       # pre-answer hybrid retrieval node
    extract.py      # post-stream structured extraction and writes
```

The import graph is acyclic: `settings` and `family` depend on nothing
internal. Within `memory/`, dependency order is `types` -> `ranking`; `settings`
-> `db` and `embed`; `db`/`embed`/`types` -> `store`; and
`embed`/`ranking`/`store`/`types` -> `recall`, while `extract` depends on
`embed`, `store`, `types`, `models`, and `settings`. `context` depends on
`family`, `settings`, `state`, and memory types; `models` depends on `settings`;
`graph` depends on `context`, `memory`, `models`, and `state`; `auth` depends on
`family` and `settings`.

`models.py` is a deliberate chokepoint: model identifiers appear nowhere else
in the codebase, so retiering — or falling back from the ChatGPT proxy to the
Claude proxy — is a one-file change. The tiers, all served through LiteLLM
(`settings.litellm_base_url`):

| Tier | Model | Purpose | First used |
|---|---|---|---|
| `VOICE` | `chatgpt/gpt-5.6-terra` | Eve herself | Phase 1 |
| `DEEP` | `chatgpt/gpt-5.6-sol` | Planning; hard reasoning | Phase 5 |
| `MECHANICAL` | `chatgpt/gpt-5.6-luna` | Structured, tool-heavy specialist work | Phase 3 |
| `CODE` | `chatgpt/gpt-5.6-sol` | Authoring skills and tool code | Phase 5 |
| `REFLEX` | `gemini/gemini-flash-lite-latest` | Ambient filtering; memory extraction | Phase 2 |

The `chatgpt/*` models are registered in LiteLLM with `mode: responses`, so
the LangChain client sets `use_responses_api=True` for those tiers. `REFLEX`
uses the metered Gemini route and the Chat Completions-compatible API instead.

One deliberate exception to "model identifiers live only in `models.py`":
`tests/test_models.py::test_voice_tier_is_the_chatgpt_conversational_model`
asserts the `VOICE` model string directly. A test whose job is to pin the
tier-to-model mapping has to name the model, or it asserts nothing. Retiering
`VOICE` deliberately touches that test; this is not an oversight to "fix."

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
by pull request. Permissions are resolved into `EveState` in Phase 1 but not
acted on; Phase 3 enforces them at the tool boundary.

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

The schema is installed by the `eve-migrate` console script under a Postgres
advisory lock. The production Dockerfile runs exactly
`eve-migrate && exec aegra serve` in its `CMD`, so schema failure prevents
Aegra from starting. Local `aegra dev` does not execute the container command,
so run the migration explicitly after starting Postgres.

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

Three tiers, matching the pytest markers declared in `pyproject.toml`.
`addopts` deselects `integration` and `live` by default, so a bare `pytest`
is the unit tier; an explicit `-m` on the command line replaces that
expression rather than adding to it.

```bash
# Unit — no network, no services (the default; the -m is explicit for clarity)
uv run pytest -m "not integration and not live"

# Integration — real Postgres, Redis, and a live `aegra serve`
docker compose -f docker-compose.test.yml up -d
uv run pytest -m integration

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

## Decision records

- [ADR 0001 — Specialists are subgraph tools, not separate services](adr/0001-agents-as-subgraph-tools.md)
- [ADR 0002 — No model call may precede the first streamed token](adr/0002-no-llm-before-first-token.md)
- [ADR 0003 — The embedding model and dimension are pinned](adr/0003-embedding-model-pinned.md)
- [ADR 0004 — Model tier routing](adr/0004-model-tier-routing.md)
- [ADR 0005 — Memory storage: one table, supersession, read-time decay](adr/0005-memory-storage.md)
