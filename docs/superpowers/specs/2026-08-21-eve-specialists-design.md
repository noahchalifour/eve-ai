# Eve Phase 3 — Specialists + Skills — Design

**Date:** 2026-08-21
**Status:** Approved, not yet implemented.
**Scope of this document:** the full design for Phase 3, "Specialists +
Skills." Program context, phase decomposition, and the Phase 1 design live in
[`2026-08-17-eve-core-design.md`](2026-08-17-eve-core-design.md); memory
mechanics live in
[`2026-08-18-eve-memory-design.md`](2026-08-18-eve-memory-design.md). Both are
assumed throughout and not repeated.

**Delivers:** requirement R2 — "specialized agents handle domain work" — and
R5 — "Eve and specialists accept skills from external sources." It is also the
phase where R8's permission strings, resolved into state since Phase 1 and
inert since then, start being enforced. When this phase ships, Eve does
things, not just remembers them.

---

## 1. Prerequisites

Two, both outside this repository, both owned by Noah directly rather than
"infrastructure" — these are personal-account credentials, not cluster
config.

| # | Prerequisite | State |
|---|---|---|
| P1 | Gmail OAuth credentials (client + refresh token for Noah's and Kendra's accounts) in Vault | Not started |
| P2 | Monarch Money session credentials in Vault | Not started |

Home Assistant needs no prerequisite: `kv/home-lab/home-assistant` already
holds a long-lived token, unrelated to this phase.

Neither blocks the start of implementation — the tools-loop mechanism, the
specialist-loop factory, and the skills layer can all be built and unit-tested
first. They block live verification of the Mail and Spend specialists
specifically (§12), so the implementation plan should sequence them early
enough not to stall at the end.

### 1.1 A risk this phase does not fix

ADR 0004 found that `ocp/*` (the Claude fallback) cannot call tools at all —
the proxy strips tool definitions before the model sees them — and that no
tool-capable fallback exists if the ChatGPT proxy degrades. Wiring the unused
Vault `anthropic_api_key` into LiteLLM was flagged as "should happen before
Phase 3." It has not happened, and this phase proceeds without it: if the
ChatGPT proxy's tool-calling degrades, specialists silently stop being able to
act while continuing to answer fluently — the failure mode ADR 0004 called
"the worst available." Accepted as a known risk, not a blocker; the fix
remains a one-file `models.py` change plus an infrastructure LiteLLM entry
whenever it's prioritized.

---

## 2. What "Eve does things" has to mean

R1 — one persona — does not relax here. Specialists never speak to the user;
they return a result to Eve, and Eve decides what to say and how. A
specialist that leaks its own voice into the reply (a different tone, a
"consulting the home assistant..." aside) is a bug, not a style choice.

### 2.1 Non-goals

- **No calendar specialist.** R2 and the Phase 3 line in the program
  decomposition both mention "the four v1 specialists," which included
  calendar. `family.yaml` grants no calendar permission today, and there is no
  calendar system chosen to integrate against. Scoped down to the three
  domains that already have a permission string and a real system behind them:
  Home, Mail, Spend. Calendar gets its own spec when there's a system to point
  it at.
- **No live MCP server.** The skills layer's MCP half (§5) is built and
  tested against a local mock server. Wiring a real one is deferred to
  whenever a concrete skill needs it.
- **No supervisor or router model in front of Eve.** Tool routing happens
  inside Eve's own turn via normal tool-calling, not a classifier that decides
  which specialist to invoke before Eve sees the message — that would be
  exactly the kind of pre-first-token model call ADR 0002 exists to prevent.
- **No Eve-authored skills.** Phase 5. This phase's skills are human-authored
  `SKILL.md` files and hand-written specialist tools.

---

## 3. Architecture overview

```
START -> load_context -> recall -> eve <-> tools -> extract -> END
```

`eve` gains a conditional edge: after each model call, if the response
contains tool calls, route to `tools` and back to `eve`; otherwise proceed to
`extract` as before. Bounded to a maximum of 6 iterations per turn — long
enough for a specialist call plus a follow-up, short enough that a confused
loop fails visibly instead of burning the latency and cost budget silently.

Three things are new:

1. **Specialists** (§4) — Home, Mail, Spend. Each a small subgraph with its
   own agentic loop, exposed to `eve`'s tool list as one opaque tool.
2. **Skills** (§5) — `search_skills`, backed by `SKILL.md` procedures and a
   generic MCP dispatcher, with newly discovered MCP tools dynamically bound
   for the rest of the thread.
3. **`eve-tools`** (§7) — an isolated service holding every third-party
   credential and API client, so a compromised or misbehaving tool call
   cannot reach anything else Eve's main process can reach.

ADR 0001 (specialists as subgraph tools, not separate services) stands: one
graph, one Aegra assistant, specialist *reasoning* stays in-process. What
changes is narrower than it sounds — specialists' leaf tool calls (the actual
HTTP request to Home Assistant, Gmail, or Monarch) are relayed through
`eve-tools` rather than made directly. The network hop ADR 0001 rejected for
the *reasoning loop* was rejected because it would land on the latency budget
and fragment tracing; a hop for a *leaf tool call* lands after the first
token has already streamed and inside a single Langfuse trace either way, so
neither objection applies to it. §14 records this as a refinement, not a
reversal.

---

## 4. Specialists

`src/eve/specialists/base.py` exposes one factory:

```python
def build_specialist(
    name: str,
    tools: list[BaseTool],
    system_prompt: str,
    permission: str | list[str],
) -> BaseTool: ...
```

`home.py`, `mail.py`, and `spend.py` each call it once. The factory builds a
small ReAct-style subgraph on `Tier.MECHANICAL`
(`chatgpt/gpt-5.6-luna` today), wraps it as a single async tool that takes a
natural-language request and returns a final string, and applies the
permission check (§8) before the subgraph runs at all. One factory rather than
three hand-rolled loops, because the loop shape — call model, run tools,
check for a final answer, repeat, bounded — does not vary between domains;
only the tool list, prompt, and permission string do.

Each specialist's own tool list is a thin HTTP client against `eve-tools`
(§7), not a direct SDK call:

- **Home** (`home.control`): list devices, read state, call a service (e.g.
  turn a light on, set a thermostat setpoint) — the shape Home Assistant's own
  REST API already exposes.
- **Mail** (`mail.read`, `mail.send`): list/search messages, read a thread,
  draft and send. `send_email` additionally requires `mail.send` — a member
  with only `mail.read` can ask Eve to summarize their inbox but not send on
  their behalf.
- **Spend** (`spend`): read transactions, read budgets and cash flow from
  Monarch Money. Whether any write action belongs here (categorizing a
  transaction, flagging one for review) is a question for implementation
  planning once the Monarch Money API's actual write surface is known — read
  access alone already satisfies "Eve can answer a real spend question."

Exact tool signatures are an implementation-planning detail, not a design
decision — same treatment Phase 1 gave its own open deployment questions.

---

## 5. Skills

`search_skills(query)` embeds the query with the existing Gemini client
(`memory/embed.py` — no new embedding model, no new credential) and matches it
against a small precomputed index built from two sources:

- **`SKILL.md` documents**, one per file under a `skills/` directory in this
  repo, git-tracked and human-authored — the same shape as this project's own
  Claude Code skills, which is not a coincidence: it is a format Noah already
  reads and writes fluently. A match returns the procedure body directly, as
  the tool's output — Eve reads it and acts using tools she already has. This
  is knowledge, not a new capability, so nothing about the bound-tool list
  changes.
- **Registered MCP servers**, each exposing a name, description, and tool
  schemas. A match here *is* a new capability, and is handled differently
  (below).

### 5.1 Dynamic tool rebinding (your chosen approach)

An MCP match is materialized as a real tool for the rest of the thread, not
just described in text. Mechanically:

1. `search_skills` is implemented as a tool that returns a `Command` updating
   `EveState.dynamic_tools` with a **serializable spec** — server id, tool
   name, JSON schema. Not a live callable, and this matters: Aegra checkpoints
   `EveState` to Postgres across every turn in a thread, so anything closing
   over a live connection or session object would either fail to serialize or
   silently break on the next turn's rehydration.
2. At the top of every model call in `eve`'s loop and in each specialist's
   loop, the bound-tools list is rebuilt as the static set plus a
   `StructuredTool` freshly materialized from each spec in
   `EveState.dynamic_tools`. The materialized tool's implementation is a thin
   call into `eve-tools`' MCP dispatcher, same pattern as §4's specialist
   tools.
3. Capped at 8 retained specs. Past the cap, the lowest-relevance spec is
   dropped when a new one is added — long-tail capabilities discovered once
   do not get to grow the tool list without bound for the rest of a long
   conversation.

`search_skills` is available in both `eve`'s loop and every specialist's loop
— it is just another bindable tool, and restricting it to one level would be
an arbitrary asymmetry with no benefit.

---

## 6. Recall becomes a tool

Per the memory design's own §13 commitment, honored here rather than
re-decided: `search_memory` wraps `memory/recall.py`'s existing query layer
with a model-authored query and no time budget (unlike the unconditional
`recall` node, which must ship a complete turn inside 120ms). Specialists
write memory through the same `extract` operations, not their own tables —
Mail noting "Kendra prefers replies kept under 100 words" writes through the
identical `add`/`supersede` path a household fact does, gated by the same
`memory.write_shared` check.

---

## 7. Isolation: the `eve-tools` service

A separate, long-running service — its own container image, its own
Deployment — holding every third-party credential (Home Assistant token,
Gmail OAuth, Monarch Money session) and the API client code that uses them. It
holds **no family or permission data** and needs none: by the time a call
reaches it, `eve`'s main container has already decided the caller is allowed
to make it (§8). Keeping it Kubernetes-credential-free and stateless is what
makes "blast radius" a meaningful claim rather than a slogan — there is
nothing sensitive in it to steal beyond the three external-service
credentials it exists to hold, and no cluster access to pivot from.

Interface: `POST /invoke {tool_name, arguments} -> {result} | {error}`. One
route, not one per tool — new specialist tools and new MCP dispatch calls both
reuse it without changing `eve-tools` itself.

Network: `NetworkPolicy` restricts egress to exactly the Home Assistant host,
the Gmail API, and the Monarch Money API (and later, whatever MCP servers get
registered). No ingress from outside the cluster — only `eve`'s main container
calls in.

Failure model: every specialist and dynamically-bound tool call is an HTTP
call with a timeout. A down or slow `eve-tools` surfaces as a tool-error
string to the model — the same graceful-degradation shape as any other
external API failure, not a new failure class requiring new handling.

### 7.1 Open items for implementation planning

Matching how Phase 1 deferred its own infra specifics rather than
re-litigating them here:

1. **Repo layout for `eve-tools`.** Leaning toward a second package
   (`src/eve_tools/`) and a second `Dockerfile` in *this* repo, built as a
   second image tag alongside `eve-ai` — a new repository is unwarranted
   isolation-of-code for a service whose isolation requirement is about the
   running container, not the source tree. Confirm during planning.
2. **Inter-service auth** between `eve`'s main container and `eve-tools` —
   shared bearer token in a Secret vs. relying on `NetworkPolicy`-only trust.
3. **Local dev/test**: `eve-tools` needs to run locally alongside the existing
   `docker-compose.test.yml` Postgres/Redis pair for integration tests to
   exercise the real HTTP boundary rather than mocking it away entirely.

---

## 8. Permissions — the tool boundary, for real this time

`family.yaml`'s own comment has said "permissions are enforced at the tool
boundary in Phase 3" since Phase 1. This is that boundary.

Two checks, both inside `eve`'s main container, both before any call reaches
`eve-tools`:

- **Coarse**, at the `eve` → specialist edge: calling `ask_mail` at all
  requires `mail.read` or `mail.send`; calling `ask_home` requires
  `home.control`; calling `ask_spend` requires `spend`.
- **Fine**, inside a specialist's own loop: Mail's `send_email` tool
  additionally requires `mail.send`, checked against the same
  `MemberContext.permissions` list, threaded into specialist subgraph state
  when it's invoked.

A denied call returns a clear string ("Kendra doesn't have mail.send
permission") as the tool's result, not an exception — the turn continues, and
Eve explains the boundary in her own words instead of the graph erroring out.

---

## 9. State changes

`EveState` gains one field:

```python
class EveState(TypedDict):
    ...
    # Specs only — never a live callable. See §5.1 for why: Aegra
    # checkpoints EveState across every turn in a thread.
    dynamic_tools: list[DynamicToolSpec]
```

`MemberContext.permissions` needs no change — it has carried the right shape
since Phase 1 specifically so this phase would not have to reshape state to
add tools, per that design's own §6.3.

---

## 10. Observability

| Attribute | Question it answers |
|---|---|
| `eve.specialist.called` (name) | Which specialists actually get used, vs. designed for nothing? |
| `eve.specialist.latency_ms` | What does a specialist call cost the turn, now that it's a network hop to `eve-tools`? |
| `eve.specialist.permission_denied` | Is the permission boundary being hit in practice, or is it decorative? |
| `eve.skills.search_used` | Is `search_skills` ever called, or is the whole mechanism unused? |
| `eve.skills.mcp_bound` | How many dynamically-bound tools accumulate in a real thread — is the cap in §5.1 ever reached? |

Same discipline as Phase 2's `eve.extract.ops`: a mechanism nobody measures is
a mechanism nobody can tell is working.

---

## 11. Deployment

`infrastructure` gains `kubernetes/apps/eve-tools/{base,overlays/homelab}`:
Deployment, a ClusterIP-only Service (no Ingress — §7 established there is no
legitimate caller outside the cluster), ExternalSecret for the three
credentials, and the `NetworkPolicy` restricting egress. `eve-ai`'s own
Deployment gains an env var pointing at `eve-tools`' in-cluster DNS name.

---

## 12. Testing

| Level | What | How |
|---|---|---|
| Unit | Permission gating (coarse + fine), skills ranking, specialist loop iteration bound | pytest against a fake chat model and a fake `eve-tools` client; no network |
| Unit | `DynamicToolSpec` materialization into a callable `StructuredTool` | pytest |
| Integration | Specialist loop against a stub `eve-tools` HTTP server returning canned responses; permission denial path | `docker-compose.test.yml`, extended with an `eve-tools` service |
| Integration | Dynamic tool rebinding against a local mock MCP server | same compose file |
| Live | Re-probe `gpt-5.6-luna` tool-calling through the live proxy — ADR 0004 flagged this configuration as unconfirmed after the model-name rollout | `tests/test_live_models.py`, extended |
| Live | Home Assistant: read a real device's state, then change it, through `eve-tools` | requires network access to `vm105` |
| Live | Gmail: read and send, gated correctly by `mail.read`/`mail.send` | requires P1 |
| Live | Monarch Money: read a real transaction or budget | requires P2 |

---

## 13. Definition of done

| # | Criterion |
|---|---|
| 1 | Eve turns a real Home Assistant device on or off within a single conversational turn. |
| 2 | Eve reads and sends real Gmail messages, correctly gated by `mail.read` vs. `mail.send`. |
| 3 | Eve answers a real spend or budget question from Monarch Money. |
| 4 | A member lacking a permission gets a graceful in-conversation explanation, never a graph error. |
| 5 | `eve-tools` holds no family or permission data, has no cluster credentials, and has no Ingress. |
| 6 | `search_skills` finds an authored `SKILL.md` procedure and Eve visibly follows it. |
| 7 | A dynamically-discovered MCP tool (mock server) is callable within the turn it was discovered in, and still callable on a later turn in the same thread. |
| 8 | `gpt-5.6-luna`'s tool-calling is reconfirmed live, closing the open item ADR 0004 left. |

---

## 14. Decision records

| ADR | Change |
|---|---|
| 0001 | **Refined, not reversed.** "One deploy" becomes two: `eve-ai` and `eve-tools`. The rejection of a network hop applies to the specialist *reasoning loop*, which stays in-process; it does not extend to a leaf tool call's HTTP hop to `eve-tools`, which lands after the first streamed token and inside the same Langfuse trace either way. |
| 0004 | **Risk carried forward, not resolved.** The missing tool-capable fallback (§1.1) remains open. This phase does not fix it. |
| — | **New (candidate ADR 0006).** Specialist and skill tool execution runs in an isolated, credential-holding, cluster-credential-free service (`eve-tools`) rather than in Eve's main process, so a misbehaving or compromised tool call cannot reach anything beyond the three external credentials it needs. To be written up as its own ADR file when implementation lands, matching how Phase 2 finalized its ADR amendments post-implementation rather than at design time. |

---

## 15. How later phases attach

- **Phase 4 (Ambient)** will want to call specialists from outside a
  conversational turn (e.g., a signal-triggered check on a Home Assistant
  sensor). The specialist-as-tool shape here is invocable from any graph node,
  not only from `eve`'s loop, so Phase 4 gains a caller rather than needing a
  new interface.
- **Phase 5 (Self-improvement)** adds Eve-authored skills and executable tool
  code behind a HITL interrupt, in its own isolated pod with no cluster
  credentials and restricted egress. `eve-tools` is the existing precedent for
  that isolation shape, not a new pattern invented for Phase 5.
