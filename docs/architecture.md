# Architecture

This document describes what exists in this repository today: Phase 1, "Eve
Core." For the program-level roadmap and the full Phase 1 design rationale,
see [`docs/superpowers/specs/2026-08-17-eve-core-design.md`](superpowers/specs/2026-08-17-eve-core-design.md)
(the "spec"). For the task-by-task build record, see
[`docs/superpowers/plans/2026-08-17-eve-core.md`](superpowers/plans/2026-08-17-eve-core.md).

## The graph

```
START -> load_context -> eve -> END
```

Two nodes, defined in `src/eve/graph.py`:

- **`load_context`** (`src/eve/context.py`) performs no model call. It reads
  the authenticated principal from
  `config["configurable"]["langgraph_auth_user"]`, resolves the matching
  entry in `family.yaml`, stamps the member's local time, and assembles the
  system prompt from `prompts/eve.md` plus member context. The prompt is
  rebuilt from scratch every turn and passed to the model rather than
  appended to `messages`, so edits to the persona or to a member's context
  take effect on existing threads instead of being frozen into history.
- **`eve`** invokes the `VOICE` tier model (`src/eve/models.py`) with the
  assembled messages and streams the response.

It is two nodes because of the latency contract in
[ADR 0002](adr/0002-no-llm-before-first-token.md): no model call may precede
the first streamed token. `load_context` is pure local computation in
single-digit milliseconds, so nothing sits in front of Eve's first token.
Later phases extend the graph without reshaping it — Phase 2 adds a `recall`
step that runs *concurrently* with the `eve` call, and Phase 3 wraps `eve` in
a tools loop, per [ADR 0001](adr/0001-agents-as-subgraph-tools.md).

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
```

The import graph is acyclic: `settings` and `family` depend on nothing
internal; `context` depends on `family`, `settings`, and `state`; `models`
depends on `settings`; `graph` depends on `context`, `models`, and `state`;
`auth` depends on `family` and `settings`.

`models.py` is a deliberate chokepoint: model identifiers appear nowhere else
in the codebase, so retiering — or falling back from the ChatGPT proxy to the
Claude proxy — is a one-file change. The tiers, all served through LiteLLM
(`settings.litellm_base_url`):

| Tier | Model | Purpose | First used |
|---|---|---|---|
| `VOICE` | `chatgpt/gpt-5.3-chat-latest` | Eve herself | Phase 1 |
| `DEEP` | `chatgpt/gpt-5.4` | Planning; hard reasoning | Phase 5 |
| `MECHANICAL` | `chatgpt/gpt-5.3-instant` | Structured, tool-heavy specialist work | Phase 3 |
| `CODE` | `chatgpt/gpt-5.3-codex` | Authoring skills and tool code | Phase 5 |
| `REFLEX` | unmapped — a metered API key, provisioned before Phase 2 | Ambient filtering; memory extraction | Phase 2 |

Only `VOICE` is exercised in Phase 1; `get_model(Tier.REFLEX)` raises
`NotImplementedError` until that key exists. The `chatgpt/*` models are
registered in LiteLLM with `mode: responses`, so the LangChain client is
constructed with `use_responses_api=True`.

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

**Store isolation is Aegra's, not ours, and this matters for Phase 2.**
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

The consequence for Phase 2: editing `scope_store_to_member` to carve out a
shared family namespace will have **no effect**. The lever is `aegra.json`'s
`store.scopes` map (`api/store.py:261-286`), currently unset here.

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

## Running locally

```bash
cp .env.example .env                      # dev auth mode, local ports
docker compose -f docker-compose.test.yml up -d   # Postgres (15432), Redis (16379)
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
cross-member access denial, and the run/resource-scoping behavior described
above. Live tests (`tests/test_live_models.py`) are the tier that verifies
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
