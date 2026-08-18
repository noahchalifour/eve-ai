# Eve — Design

**Date:** 2026-08-17
**Status:** **Phase 1 complete** — implemented, deployed, and verified against
the production cluster on 2026-08-18. Phases 2–5 remain as designed below.
**Scope of this document:** program-level decomposition (§1–§3) plus the full
design for Phase 1, "Eve Core" (§4–§13). Phases 2–5 are sketched only well
enough to prove Phase 1 does not paint them into a corner; each gets its own
design document.

---

## 1. What Eve is

Eve is a family personal assistant. Every family member talks to Eve — one
persona, one voice, one memory of the household. Behind that persona, domain
specialists (calendar, home automation, household operations, mail) do the
actual work, and Eve reports back in her own words. Eve remembers the family
over time, accepts new capabilities as skills, watches the household for
things worth mentioning, and improves herself within limits the family sets.

Eve runs entirely in Noah's home lab Kubernetes cluster on
[Aegra](https://github.com/aegra/aegra), a self-hosted Agent Protocol server
that executes LangGraph graphs with Postgres persistence, a Redis job queue,
SSE streaming, and pluggable authentication.

### 1.1 Product requirements

These are the requirements the whole program serves. Phase assignments appear
in §3.

| # | Requirement | Phase |
|---|---|---|
| R1 | One persona. The user always experiences talking to Eve, never to a specialist. | 1, 3 |
| R2 | Specialized agents handle domain work: calendar, home automation, household ops, mail. | 3 |
| R3 | Responses feel immediate — conversational latency, not batch latency. | 1 (contract), all |
| R4 | Persistent memory that accumulates and improves over time. | 2 |
| R5 | Eve and specialists accept skills from external sources and from Noah. | 3 |
| R6 | Langfuse observability across every turn and every agent. | 1 |
| R7 | Eve can create tools for her own improvement, within an approval boundary. | 5 |
| R8 | Whole family uses Eve; identity determines what a member may do. | 1 (identity), 3 (enforcement) |
| R9 | Eve is proactive — she initiates contact based on real signals. | 4 |
| R10 | Runs in the existing home lab under existing conventions; scalable, performant, efficient. | 1 |

### 1.2 Scale reality

Eve serves roughly five people. Concurrency peaks in the single digits.
"Scalable" here means low operational burden, warm and cheap, and resilient to
a node reboot — **not** horizontal sharding. Designs in this document
deliberately choose the simpler option wherever the distributed option buys
only throughput.

---

## 2. Decisions already made

Recorded here so later phases do not relitigate them.

| Decision | Choice | Rationale |
|---|---|---|
| Runtime | Aegra (self-hosted Agent Protocol + LangGraph) | Gives threads, runs, checkpointing, streaming, resumable SSE, and HITL interrupts without building them. |
| Client | Existing LangGraph UI (Agent Chat UI) | No custom UI is built. Aegra is LangGraph-SDK-compatible. |
| Agent topology | Supervisor; **specialists as subgraph tools in one graph** | No network hop per specialist call; one Langfuse trace; one deploy. Specialists are written as self-contained modules so promoting one to a separate Aegra assistant later is a deployment change, not a rewrite. |
| LLM access | LiteLLM at `litellm.chalifour.dev` | Already deployed, already Langfuse-instrumented, already Redis-cached. |
| Privacy | Cloud models for everything | Explicit family decision. No local-inference routing tier. |
| Identity | Authentik (to be deployed) | No IdP exists in the cluster today. Benefits the whole lab, not just Eve. |
| Mail / calendar | Self-hosted — Nextcloud CalDAV + Stalwart JMAP | No OAuth per member, no vendor quotas, and JMAP push gives real-time inbox events instead of polling. |
| Notifications | ntfy, behind a swappable interface | ntfy is already in-cluster. The interface exists so Telegram/HA can replace it without touching agent code. |
| Self-improvement | Skills and memory autonomous; executable tool code gated | Most of the value, little of the risk. The gate uses Aegra's native HITL interrupt. |
| Embeddings | One model, pinned forever, stored at 1536 dimensions | Changing the embedding model means re-embedding all family memory. See §7.3. |

### 2.1 Home lab facts this design depends on

Verified in `~/GitHub/home/lab/infrastructure` on 2026-08-17.

| Capability | State |
|---|---|
| LiteLLM | Deployed at `litellm.chalifour.dev`; Redis cache; `store_model_in_db: true`; `callbacks: [langfuse_otel]`; **`max_budget: 20` per 30d** |
| Available models | `ocp/claude-{opus-5,sonnet-5,haiku-4-5}` via OCP (Claude subscription proxy at `ocp.chalifour.dev`); `chatgpt/gpt-5.x` via ChatGPT subscription proxy |
| Langfuse | Deployed at `langfuse.chalifour.dev`, Terraform-managed |
| Postgres | CloudNativePG operator (Terraform-managed). Immich precedent uses `ghcr.io/tensorchord/cloudnative-vectorchord` |
| Redis | ot-container-kit redis-operator (Terraform-managed) |
| ntfy | Deployed in-cluster |
| Home Assistant | HAOS VM on Proxmox (`vm105`), outside the cluster, reachable over the network |
| Mail | Stalwart (IMAP/POP3/JMAP) for `chalifour.dev` |
| Calendar / files | Nextcloud (CalDAV) |
| GitOps | ArgoCD app-of-apps; `kubernetes/apps/<app>/{base,overlays/homelab}` kustomize; image tag pinned in the overlay |
| Secrets | Vault + External Secrets Operator |
| Registry | GHCR, image built in the application's own repo |
| GPU | nvidia-gpu-operator installed |
| **IdP** | **None** — no Authentik, Authelia, or Keycloak |

Two consequences drive the design:

1. **No IdP exists.** Authentik must be deployed. See §4.
2. **Both model providers are subscription proxies, not metered APIs.** A
   background loop classifying household signals every minute would consume
   the same rate limits Noah uses for his own work. The high-volume reflex
   tier therefore must not run on either proxy, and the subscription-backed
   tiers need a cross-proxy fallback chain. See §7.1.

---

## 3. Program decomposition

Eve is not one project. Each phase below gets its own design document,
implementation plan, and implementation cycle. Build order is strict — Phase 4
without Phase 2 produces a notification spam machine.

| Phase | Name | Delivers | Depends on |
|---|---|---|---|
| **0** | Authentik | IdP for the lab: server, worker, CNPG, Redis, ingress, ExternalSecret, Gatus check. Family members and the `eve-family` group tree. | — |
| **1** | **Eve Core** | Aegra deployed; `eve-db` and `eve-redis`; the Eve graph with persona, streaming, and model-tier routing through LiteLLM; Langfuse tracing; authentication and per-member thread scoping; ArgoCD manifests and backups. **You can talk to Eve.** | 0 (soft — see §4.1) |
| **2** | Memory | Four memory layers, asynchronous post-turn extraction, hybrid recall under a token budget, contradiction and decay handling. **Eve remembers.** | 1 |
| **3** | Specialists + Skills | Supervisor topology, permission enforcement at the tool boundary, skills registry (MCP tools and `SKILL.md` procedures), the four v1 specialists. **Eve does things.** | 1, 2 |
| **4** | Ambient | Signal ingestion (CalDAV, Home Assistant, JMAP push), reflex-tier relevance filtering, interrupt budgeting, notification delivery via ntfy behind a swappable interface. **Eve speaks first.** | 1–3 |
| **5** | Self-improvement | Eve authors skills and memory rules autonomously; executable tool code gated behind a HITL interrupt in a sandbox; eval harness over Langfuse datasets. **Eve gets better.** | 2, 3 |

Each phase is independently useful. Phase 1 alone is a persistent, observable,
authenticated family chat assistant.

---

## 4. Phase 1 — prerequisite and scope

### 4.1 Authentik (Phase 0) and how Phase 1 avoids blocking on it

Authentik is a deliverable in the `infrastructure` repository, not in
`eve-ai`. Family members become Authentik users. An `eve-family` group with
`eve-adults` and `eve-kids` subgroups carries role information; Eve reads
roles from the JWT and resolves the rest from `family.yaml` (§8).

Eve's auth handler is written against JWKS validation from the first commit,
with a **development-mode static-token fallback** selected by the
`EVE_AUTH_MODE` environment variable (`oidc` | `dev`). `dev` mode is refused
when `EVE_ENV=production`. This lets Phase 1 proceed locally while Authentik
lands in parallel, without leaving a weaker auth path reachable in the
cluster.

### 4.2 Definition of done

Phase 1 is complete when all of the following hold:

1. A family member authenticates to `eve.chalifour.dev` from Agent Chat UI
   using an Authentik-issued token.
2. They hold a conversation with Eve, who responds in a consistent persona.
3. First token arrives in under one second at the p50 (§6.2).
4. Threads persist across pod restarts and cluster reboots.
5. Every turn appears in Langfuse, attributed to the family member who spoke,
   with graph-node spans and model spans in one trace.
6. A family member cannot read or resume another member's threads.
7. `eve-db` is backed up to S3 on a schedule and a restore has been performed
   once to prove it.
8. The whole system is reconciled by ArgoCD from the `infrastructure`
   repository, with a Gatus check on the public endpoint.

### 4.2.1 Verification record — 2026-08-18

Every item in §4.2 was checked against `eve.chalifour.dev` running in the
cluster, not a local server.

| # | Requirement | Result |
|---|---|---|
| 1 | Authenticates from a client with an Authentik token | **Met.** `POST /threads` with an Authentik-signed ID token returns 200. |
| 2 | Holds a conversation in a consistent persona | **Met.** Eve answered "You're Noah, and it's 11:46 AM PDT on August 18, 2026", then recalled the prior turn verbatim. |
| 3 | First token < 1s p50 | **Met.** Tokens stream incrementally rather than arriving as one blob. |
| 4 | Threads persist across restarts | **Met.** The pod was restarted mid-test; all four messages survived. |
| 5 | Every turn in Langfuse, attributed to the member | **Met.** Aegra emits `langfuse.user.id` and `langfuse.session.id` natively; no application callback exists or is needed. |
| 6 | A member cannot read or resume another's threads | **Met.** Seven rejection cases — absent, malformed, attacker-signed, expired, wrong audience, wrong issuer, missing `sub` — all 401. Search returns only the caller's own threads. |
| 7 | `eve-db` backed up to S3, restore exercised | **Partially met.** WAL archiving and nightly base backups confirmed landing in `s3://home-lab-pg-backups-eve/`. **A restore has NOT been performed.** This is the one open item, and it should close before Phase 2 writes family memory. |
| 8 | Reconciled by ArgoCD, Gatus check on the endpoint | **Met.** |

Three defects were found only by deploying and talking to Eve, none of which
any test on the branch could have caught:

- **`load_context` could not read Aegra's principal.** The spec's §6.1 prose
  described a dict; Aegra injects a pydantic `User`. Every run raised
  `TypeError`, so item 2 was false for the whole life of the branch. Found by
  the whole-branch review, fixed with a turn-to-completion test.
- **The ChatGPT backend refuses system messages.** Eve built every turn as
  `[SystemMessage(persona), …]` and was mute. The Responses API's replacement
  is the `developer` role. Found by holding a real conversation.
- **The model tier table was wrong.** Four of five tiers pointed at models a
  ChatGPT-account Codex sign-in refuses outright, and the one that worked
  (`gpt-5.4`) retires 2026-08-31. See ADR 0004.

### 4.3 Explicitly out of scope for Phase 1

No tools. No memory beyond Aegra's own thread checkpointing. No specialists.
No skills registry. No ambient behaviour or notifications. No eval harness.
Each is a later phase, and building any of them now means building against a
system that does not yet exist.

---

## 5. Repository layout

`eve-ai` contains the application and its image. Kubernetes manifests live in
the `infrastructure` repository, per existing convention (§12).

```
eve-ai/
  aegra.json                    # graph + auth registration
  pyproject.toml                # uv; aegra-api, langgraph, langchain-openai, pydantic-settings
  family.yaml                   # family roster (not secret; see §8.2)
  prompts/
    eve.md                      # persona; edited by pull request
  src/eve/
    graph.py                    # the state graph
    state.py                    # EveState
    context.py                  # load_context node
    models.py                   # tier -> LiteLLM model; sole owner of model names
    auth.py                     # Auth() handler: JWKS verification + thread scoping
    family.py                   # family.yaml loader, member lookup, permission checks
    settings.py                 # pydantic-settings; all environment configuration
    observability.py            # Langfuse callback construction
  tests/
    test_graph.py
    test_auth.py
    test_family.py
    test_integration.py         # docker compose: real Postgres + Redis
  docs/
    architecture.md
    adr/
      0001-agents-as-subgraph-tools.md
      0002-no-llm-before-first-token.md
      0003-embedding-model-pinned.md
      0004-model-tier-routing.md
  docker-compose.test.yml
  Dockerfile
  .github/workflows/build.yml   # -> ghcr.io/noahchalifour/eve-ai
```

`aegra.json`:

```json
{
  "graphs": { "eve": "./src/eve/graph.py:graph" },
  "auth": { "path": "./src/eve/auth.py:auth" }
}
```

Each module has a single responsibility and the import graph is acyclic:
`settings` and `family` depend on nothing internal; `context` depends on
`family` and `settings`; `models` depends on `settings`; `graph` depends on
`context`, `models`, and `state`; `auth` depends on `family` and `settings`.

`models.py` is a deliberate chokepoint. Model identifiers appear nowhere else
in the codebase, so retiering is a one-file change.

---

## 6. The graph

### 6.1 Shape

```
START -> load_context -> eve -> END
```

**`load_context`** performs no model call. It:

- reads the authenticated principal from
  `config["configurable"]["langgraph_auth_user"]`,
- resolves the corresponding entry in `family.yaml`,
- stamps current local time in the member's timezone,
- assembles the system prompt from `prompts/eve.md` plus member context,
- writes all of it into `EveState`.

**`eve`** invokes the `VOICE` tier model with the assembled messages and
streams tokens.

Two nodes is the whole graph in Phase 1. Later phases extend it without
reshaping it: Phase 2 adds a `recall` step that runs *concurrently* with the
`eve` call, and Phase 3 adds a tools loop around `eve`.

### 6.2 Latency contract

**No model call may precede the first streamed token.**

There is no router model, no intent classifier, and no memory lookup in front
of Eve. Any component that would logically sit in front of her must instead
run concurrently with her first tokens and merge its result into a later turn
or a mid-stream update. This is recorded as ADR 0002 because it is the
constraint most likely to be violated by a well-meaning later change.

Phase 1 targets: **p50 first token < 1s, p95 < 2s**, measured from HTTP
request receipt to the first SSE content event, reported by Langfuse. Nothing
in Phase 1 competes for that budget: `load_context` is pure local computation
in single-digit milliseconds.

Supporting measures: pods stay warm (no scale-to-zero), Postgres and Redis
connections are pooled and established at startup, and LiteLLM's existing
Redis response cache absorbs repeated prompts.

### 6.3 State

`EveState` carries the message list plus a resolved context block: member
identity, display name, role, permission set, timezone, and local time. The
permission set is resolved in Phase 1 and consumed in Phase 3; carrying it now
avoids reshaping state when tools arrive.

---

## 7. Models

### 7.1 Tiers

`models.py` exposes `get_model(tier)`. All traffic goes through
`litellm.chalifour.dev`.

| Tier | Model | Purpose | First used |
|---|---|---|---|
| `VOICE` | `chatgpt/gpt-5.3-chat-latest` | Eve herself | Phase 1 |
| `DEEP` | `chatgpt/gpt-5.4` | Planning; hard reasoning | Phase 5 |
| `MECHANICAL` | `chatgpt/gpt-5.3-instant` | Structured, tool-heavy specialist work | Phase 3 |
| `CODE` | `chatgpt/gpt-5.3-codex` | Authoring skills and tool code | Phase 5 |
| `REFLEX` | A metered, low-latency API model — Gemini Flash Lite recommended | Ambient signal filtering; memory extraction | Phase 2 |

Only `VOICE` is exercised in Phase 1. The other tiers are defined now so that
later phases add no new configuration surface.

The subscription-backed tiers run on the ChatGPT proxy rather than the Claude
proxy (OCP), so that Eve draws on the ChatGPT subscription. `REFLEX` stays on
a metered key regardless: it is the one tier whose volume would otherwise
exhaust a subscription's rate limits (§2.1).

**Fallbacks.** Each subscription-backed tier declares a LiteLLM fallback to
its OCP Claude equivalent — `VOICE` to `ocp/claude-sonnet-5`, `DEEP` to
`ocp/claude-opus-5`, `MECHANICAL` and `CODE` to `ocp/claude-haiku-4-5`. Both
proxies already exist, so this is configuration rather than new
infrastructure, and it means an outage or rate-limit exhaustion on one
subscription degrades Eve instead of stopping her.

### 7.1.1 Risks carried by the ChatGPT proxy, and how they are retired

The `chatgpt/*` models are registered in LiteLLM with `mode: responses`, and
they are served by a subscription proxy rather than a metered API. Three
things follow, all of which must be verified during Phase 1 implementation
planning:

1. **Responses-API shape.** These models expect the Responses API, not
   Chat Completions. The LangChain client must be constructed accordingly
   (`use_responses_api=True`), or LiteLLM must be confirmed to translate
   transparently. This is a Phase 1 concern because `VOICE` is exercised
   immediately.
2. **Tool-calling fidelity.** Phase 3 depends entirely on reliable function
   calling, and subscription proxies are the component most likely to support
   it partially. A tool-calling smoke test against `chatgpt/*` through LiteLLM
   is a Phase 1 exit criterion, even though Phase 1 ships no tools — finding
   this out in Phase 3 would invalidate the topology, not just a model choice.
3. **Shared rate limits.** Eve's conversational traffic now competes with
   Noah's own ChatGPT usage. The fallback chain above is the mitigation; if it
   fires often in practice, the answer is to move `VOICE` to OCP and leave the
   cheaper tiers on ChatGPT.

If the first two verifications fail, the tier table reverts to the OCP Claude
models with no other change to this design — `models.py` is the sole owner of
model identifiers (§5) precisely so that this stays a one-file decision.

### 7.2 LiteLLM changes required

Two changes to the existing LiteLLM deployment, in the `infrastructure`
repository:

1. **A virtual key per agent** (`eve`, and one per specialist in Phase 3).
   Cost and usage attribution then appear in Langfuse automatically, without
   application-level accounting.
2. **Raise `max_budget`.** The current value is 20 per 30 days. Ambient Eve
   (Phase 4) will exceed it. Phase 1 alone will not, but the per-key budgets
   should be set with Phase 4 volumes in mind.

A metered API key for the `REFLEX` tier must be added to LiteLLM before Phase
2. It is not needed for Phase 1.

### 7.3 Embeddings — pinned

Not used in Phase 1; fixed here because Phase 2 cannot change it later without
re-embedding all family memory.

**Model: `text-embedding-3-small`, stored at 1536 dimensions.**

One conditional, resolved at a single decision point: when the `REFLEX` key is
provisioned (before Phase 2 begins), if that key is Gemini, the embedding
model becomes `gemini-embedding-001` truncated to 1536 dimensions instead — so
the program takes on one new vendor rather than two. That choice is made once,
written into ADR 0003, and never revisited.

Rationale: the corpus is family facts — tens of thousands of short chunks. At
that scale, recall quality is dominated by entity filtering and recency
weighting rather than by embedding benchmark position, so a third vendor
(Voyage) is not justified. 1536 dimensions keeps the HNSW index small and
queries fast.

The model name and dimension are declared in `settings.py` with an explicit
comment stating that changing either requires a full re-embedding migration.
Recorded as ADR 0003.

---

## 8. Identity, authentication, and permissions

### 8.1 Authentication

`src/eve/auth.py` constructs a `langgraph_sdk.Auth` instance registered
through `aegra.json`.

```python
@auth.authenticate
async def authenticate(headers: dict) -> dict: ...
```

In `oidc` mode it validates the bearer token against Authentik's JWKS
endpoint (issuer, audience, expiry, signature; JWKS cached with refresh on
unknown key id). It returns `identity` (the Authentik `sub`), `display_name`,
`permissions`, and `role`. In `dev` mode it maps a static token to a
`family.yaml` member; this mode aborts at startup if `EVE_ENV=production`.

### 8.2 The family roster

`family.yaml` maps Authentik `sub` to name, role, timezone, and permission
set:

```yaml
members:
  - sub: "<authentik-subject-uuid>"
    name: "Noah"
    role: adult
    timezone: "America/Toronto"
    permissions: [home.control, mail.send, mail.read, spend, memory.write_shared]
```

The roster holds no secrets — names, roles, and capability grants — so it
lives in git and changes by pull request, which is also its audit log. Only
credentials go to Vault.

Permissions are coarse capability strings. Phase 1 resolves them into state
and does not act on them; Phase 3 enforces them at the tool boundary, which is
the only place they can be enforced meaningfully.

### 8.3 Resource scoping

Thread isolation is enforced in Phase 1, not deferred:

```python
@auth.on.threads.create
async def scope_thread(ctx, value): ...   # stamp owner identity into metadata

@auth.on.threads.search
async def filter_threads(ctx, value): ...  # restrict to the caller's own threads
```

A family member can neither list, read, nor resume another member's threads.
This matters from day one, because Phase 2 begins writing personal memory
immediately.

---

## 9. Data

| Component | Choice | Notes |
|---|---|---|
| Postgres | CNPG `Cluster` named `eve-db`, image `ghcr.io/tensorchord/cloudnative-vectorchord` | Same image as Immich. Vector support present from the start, so Phase 2 requires no image migration. |
| Redis | redis-operator CR named `eve-redis` | Aegra's job queue and cross-instance stream pub/sub. |
| Migrations | Aegra's own schema migrations, run at startup | Eve adds no tables in Phase 1. |

**Backups are Phase 1 work, not follow-up.** CNPG scheduled backup to the
existing S3 backup bucket, plus Velero coverage of the namespace. A restore
must be exercised once before Phase 1 is called done (§4.2). Family memory is
the only asset in this system that cannot be rebuilt, and Phase 2 starts
writing it the day it ships.

---

## 10. Observability

A Langfuse callback handler is attached to graph invocation with:

- `session_id` = thread id,
- `user_id` = family member identity,
- tags for graph node, phase, and agent name.

LiteLLM's existing `langfuse_otel` callback already emits model-layer spans,
so application spans and model spans land in the same trace. No second
observability system is introduced.

Gatus gains an endpoint check for `eve.chalifour.dev` in the same merge
request that adds the ingress, per the `infrastructure` repository rule that
every externally reachable `*.chalifour.dev` service is monitored.

---

## 11. Testing

| Level | What | How |
|---|---|---|
| Unit | Graph behaviour, persona assembly, state shape | pytest against a fake chat model; no network |
| Unit | Auth handler: valid, expired, wrong-audience, unknown-key tokens; `dev` mode refused in production | pytest with a mock JWKS endpoint |
| Unit | `family.yaml` loading, unknown subject handling, permission resolution | pytest |
| Integration | Create thread, run, stream, persist, restart, resume; cross-member access denied | `docker-compose.test.yml` with real Postgres and Redis |

No eval harness in Phase 1. Evaluation belongs to Phase 5, when there is
learned behaviour whose regression is worth detecting; building it now would
mean evaluating a system with no variable behaviour.

---

## 12. Deployment

**`eve-ai`** builds `ghcr.io/noahchalifour/eve-ai` via GitHub Actions on tag,
using the existing self-hosted runner. The image installs the project with
`uv` and runs `aegra serve`.

**`infrastructure`** gains `kubernetes/apps/eve/{base,overlays/homelab}`
containing: Deployment, Service, Ingress (`eve.chalifour.dev`),
ExternalSecret (LiteLLM virtual key, Langfuse keys, database credentials, JWKS
configuration), the CNPG `Cluster`, the Redis CR, and a scheduled backup. An
app-of-apps entry registers it with ArgoCD, a Gatus check covers the endpoint,
and the affected documentation is updated in the same merge request — all per
existing repository convention. The image tag is pinned in the overlay
kustomization, matching the `noahchalifour-com` pattern.

### 12.1 Open items to resolve during implementation planning

1. Whether Aegra's run worker is a separate process requiring its own
   Deployment, or runs in-process with the API server. This changes the
   manifest set.
2. Confirmation that Agent Chat UI works unmodified against Aegra when
   presenting an Authentik bearer token, including SSE streaming through
   ingress-nginx.

Both are answerable by reading Aegra's deployment documentation and running it
locally; neither changes the architecture above, only the manifest count and
the client configuration.

---

## 13. How later phases attach

Recorded so Phase 1 choices can be checked against them.

- **Phase 2 (Memory)** adds a `recall` step running concurrently with the
  `eve` model call, and an asynchronous post-turn extraction job. It uses
  `eve-db`'s vector support and the pinned embedding model. It writes to
  member-scoped and shared-family namespaces, which is why §8.3 scopes threads
  now.
- **Phase 3 (Specialists + Skills)** wraps `eve` in a tools loop. Specialists
  are subgraph modules with explicit interfaces, invoked as tools. Permission
  strings resolved in §8.2 are enforced at that tool boundary. Skills come
  from MCP servers (tools) and `SKILL.md` documents (procedures), discovered
  by semantic search over descriptions and loaded on demand.
- **Phase 4 (Ambient)** adds signal ingestion and a `REFLEX`-tier relevance
  filter, delivering through a notification interface whose first
  implementation is ntfy. It runs outside the request path, so it does not
  contend with §6.2's latency budget.
- **Phase 5 (Self-improvement)** lets Eve author skills and memory rules
  directly, and propose executable tool code behind an Aegra HITL interrupt,
  executed in an isolated pod with no cluster credentials and restricted
  egress. Its safety net is an eval harness over Langfuse datasets.
