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
| 3 | Specialists + Skills | Supervisor topology, permission enforcement, skills registry, the v1 specialists. **Eve does things.** |
| 4 | Ambient | Signal ingestion, relevance filtering, proactive notifications. **Eve speaks first.** |
| 5 | Self-improvement | Eve authors skills and memory rules; gated tool authoring; eval harness. **Eve gets better.** |

This repository is Phase 2: a persistent, authenticated, observable family
chat assistant with four durable memory layers and hybrid recall. It still has
no tools, specialists, or proactive behaviour. See
[`docs/superpowers/specs/2026-08-18-eve-memory-design.md`](docs/superpowers/specs/2026-08-18-eve-memory-design.md)
for the Phase 2 design and definition of done.

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
