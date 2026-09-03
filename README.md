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
| **5a** | **Self-improvement** | Eve authors her own behavioural rules and multi-step procedures, stored as memory layers, revocable from a CLI. **Eve gets better.** |
| **5b** | **Eval harness** | Datasets built from Eve's own tables; an A/B measuring what the rule set is worth; a regression gate that never depends on Langfuse. **Now we can tell.** |
| **5c** | **Gated tool code** | Eve proposes executable Python tools behind a human approval; approved tools run in `eve-sandbox`, a separate credential-free service with no network access. **Eve computes, safely.** |

This repository was Phase 5c, completing the original five-phase program.
A sixth deploy, `eve-computer`, now sits beside it - see
[`docs/superpowers/specs/2026-08-28-eve-computer-design.md`](docs/superpowers/specs/2026-08-28-eve-computer-design.md)
and [ADR 0015](docs/adr/0015-granted-identity-vs-authored-capability.md).
Eve proposes a small Python tool through `propose_tool`; the run
pauses on LangGraph's `interrupt()` until a human with `tools.author` approves
or rejects the exact source bytes; an approved tool is discovered by
`search_skills` and dispatched to `eve-sandbox`, which runs it with no
network, no filesystem beyond a per-call tmpfs, no environment variables, and
no credentials of any kind. See [ADR 0010](docs/adr/0010-sandboxed-tools-are-pure-functions.md)
for why the pod, not the approval or the AST check, is the actual security
boundary, and
[`docs/superpowers/specs/2026-08-27-eve-sandboxed-tools-design.md`](docs/superpowers/specs/2026-08-27-eve-sandboxed-tools-design.md)
for the Phase 5c design and definition of done.

### Where the program ends

Four boundaries are permanent, not phases yet to come:

- **Eve does not approve her own code.** There is no path, no setting, and no
  "trusted tool" tier. The one human gate in the program stays.
- **Eve does not author credentialed capability.** A tool needing a secret is
  an `eve-tools` handler in a pull request, forever. `eve-computer` grants Eve
  her *own* identity - accounts a human provisions by hand over VNC, revocable
  with a checkbox, whose blast radius the pod's `NetworkPolicy` bounds to her
  own accounts and compute - which is a different thing from authoring
  capability over the family's credentials. See
  [ADR 0015](docs/adr/0015-granted-identity-vs-authored-capability.md).

  EVE-4 extends this once more, and the consequence is worth stating rather
  than discovering: **Eve can now open pull requests against this
  repository.** The README says a tool needing a secret is "an `eve-tools`
  handler in a pull request, forever." She can now write that pull request.

  This does not weaken the boundary; it routes through it. The gate was never
  "Eve cannot propose" - it was "a human merges." That gate is exactly where
  it was, and unlike the `propose_tool` interrupt, this one is a code review
  in GitHub with a diff, CI, and no 11pm approval prompt. It is a better
  instance of the same gate.
- **Eve does not rewrite her own persona.** `prompts/eve.md` is
  human-authored. Phase 5a lets her write rules *under* it; nothing lets her
  edit it.
- **Eve does not learn unsupervised.** Rules come from a specific turn with a
  specific member, hygiene never auto-resolves a contradiction, and code needs
  a human. The reflection loop this program deferred early on is deferred
  permanently, not pending.

## Quick start

```bash
cp .env.example .env
docker compose -f docker-compose.test.yml up -d   # Postgres + Redis
uv run eve-migrate
uv run aegra dev
```

Run the unit tests (no network, no services required):

```bash
uv run pytest -m "not integration and not live and not docker"
```

See [`docs/architecture.md`](docs/architecture.md) for the graph, the module
map, auth and thread scoping, the full test tier breakdown, and deployment.
