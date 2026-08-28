# Eve

Eve is a family personal assistant for the Chalifours. One family member
talks to one persona — one voice, one memory of the household — while domain
specialists do the actual work behind her, out of view. Eve runs on
[Aegra](https://github.com/aegra/aegra), a self-hosted Agent Protocol server
that executes LangGraph graphs with Postgres persistence, a Redis job queue,
SSE streaming, and pluggable authentication, in Noah's home lab Kubernetes
cluster.

This repository is the current phase of a five-phase program. Each phase is
independently useful and gets its own design document:

| Phase | Name | Delivers |
|---|---|---|
| 0 | Authentik | Identity provider for the lab. |
| **1** | **Eve Core** | Aegra deployed; the Eve graph with persona, streaming, and model-tier routing; Langfuse tracing; per-member authentication and thread scoping. **You can talk to Eve.** |
| **2** | **Memory** | Four memory layers, post-stream extraction, hybrid recall. **Eve remembers.** |
| **3** | **Specialists + Skills** | Supervisor topology, permission enforcement, skills registry, the v1 specialists. **Eve does things.** |
| **4** | **Ambient** | Signal ingestion, relevance filtering, proactive notifications. **Eve speaks first.** |
| 5a | Self-improvement | Eve authors her own behavioural rules and multi-step procedures, stored as memory layers, revocable from a CLI. **Eve gets better.** |
| **5b** | **Eval harness** | Datasets built from Eve's own tables; an A/B measuring what the rule set is worth; a regression gate that never depends on Langfuse. **Now we can tell.** |
| 5c | Gated tool code | Eve proposes executable tool code behind a human approval, run in a sandbox with no network and no credentials. |

This repository is Phase 5b: an `eve-eval` command builds evaluation datasets
from Eve's own Postgres tables, replays them through the real filter and the
real graph, measures the effect of Phase 5a's self-authored rules with an
A/B, and gates on a regression — all without depending on Langfuse being
reachable. See
[`docs/superpowers/specs/2026-08-27-eve-eval-harness-design.md`](docs/superpowers/specs/2026-08-27-eve-eval-harness-design.md)
for the Phase 5b design and definition of done.

## Quick start

```bash
cp .env.example .env
docker compose -f docker-compose.test.yml up -d   # Postgres + Redis
uv run eve-migrate
uv run aegra dev
```

Run the unit tests (no network, no services required):

```bash
uv run pytest -m "not integration and not live"
```

See [`docs/architecture.md`](docs/architecture.md) for the graph, the module
map, auth and thread scoping, the full test tier breakdown, and deployment.
