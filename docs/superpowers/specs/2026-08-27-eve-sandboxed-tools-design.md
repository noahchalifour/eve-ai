# Eve Phase 5c — Gated Executable Tool Code — Design

**Date:** 2026-08-27
**Status:** Approved, not yet implemented.
**Scope of this document:** Phase 5c — Eve proposing executable tool code,
gated behind a human approval and executed in an isolated sandbox. This is the
last slice of Phase 5 and the last phase of the program. Program context lives
in [`2026-08-17-eve-core-design.md`](2026-08-17-eve-core-design.md); the tools
loop, `eve-tools`, and permission enforcement in
[`2026-08-21-eve-specialists-design.md`](2026-08-21-eve-specialists-design.md);
self-authored rules and procedures in
[`2026-08-27-eve-self-improvement-design.md`](2026-08-27-eve-self-improvement-design.md);
the eval harness in
[`2026-08-27-eve-eval-harness-design.md`](2026-08-27-eve-eval-harness-design.md).
All are assumed throughout and not repeated.

**Delivers:** the remainder of requirement R7 — "Eve can create tools for her
own improvement, within an approval boundary." When this phase ships, the
program is complete.

---

## 1. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Eve's migrations moved to Alembic. Phase 5b leaves `MIGRATIONS` at five entries, the threshold [`db.py:11`](../../../src/eve/memory/db.py) names; `eve_tool` is the sixth. **This is task one** (§8.2). | Not started |
| P2 | `eve-sandbox` deployed: Deployment, Service, `NetworkPolicy` with default-deny egress, no ServiceAccount token, no mounted secrets, read-only root filesystem. Belongs to the `infrastructure` repository. | Not started |
| P3 | `EVE_SANDBOX_API_KEY` generated and stored in Vault, delivered to both `eve` and `eve-sandbox`. | Not started |
| P4 | Phase 5b's gate green, so an approver has something better than a diff to judge by (§5.4). | Blocked on 5b |

P1 is pure refactoring and blocks the schema. P2 blocks live execution but not
implementation: the sandbox service is built and tested in-process against the
same `/invoke` contract. P4 is a process prerequisite, not a technical one.

---

## 2. What "Eve writes code" has to mean

Three things, in order of how badly they fail when missed:

1. **A human approves the exact bytes that run.** Not the idea, not an earlier
   version, not a description of it. §5.3 is the whole of this.
2. **Approving a bad tool costs almost nothing.** The interesting question is
   not "will the approver ever be wrong" — they will — but "what does a wrong
   approval get you." §6 is engineered so the honest answer is: a wasted CPU
   second. If the sandbox can be made worthless to compromise, the approval
   gate stops being load-bearing, and that is a much better place to be than
   trusting the gate.
3. **A tool that has stopped being right can be removed without a deploy.**
   §9.

### 2.1 Non-goals

- **No network access from tool code. At all.** Not an allowlist, not a proxy:
  none. §6.2 is the central decision of this phase and everything cheap about
  it follows from there.
- **No credentials in the sandbox.** A tool needing one is not a sandbox tool;
  it is an `eve-tools` handler, authored by a human, reviewed in a pull
  request. §6.1.
- **No package installation.** No `pip install` at propose, approve, or run
  time. The import surface is the allowlist in §6.3 and nothing else, ever.
- **No filesystem persistence.** A tool gets a tmpfs that exists for the
  duration of one call.
- **No Eve-initiated approval.** Eve cannot approve her own proposal, cannot
  retry a rejected one automatically, and cannot propose on behalf of a member
  who lacks the permission. §5.1.
- **No editing an approved tool.** Changing the source produces a new
  proposal needing a new approval. There is no "minor edit" path. §5.3.
- **No language but Python.** One runtime, one allowlist, one AST checker.
- **No long-running or stateful tools.** One call in, one result out, bounded
  by §6.4. No background work, no state between calls.
- **No eval dataset for sandbox tools.** 5b §14 offers a third dataset shape
  here — a pure function's inputs and outputs are exactly comparable, so it
  would be the cheapest scorer in the program — and this phase declines it. A
  dataset of two or three approved tools is not a dataset, and a pure function
  is already deterministic: if it was right when approved, it is right now.
  The scorer becomes worth building if `eve_tool` ever holds a dozen live rows,
  and §11's `invocations` is what would tell you.

---

## 3. Architecture overview

```
  member asks for something Eve has no tool for
                 |
                 v
  Eve calls propose_tool(name, description, args_schema, source)
                 |
                 +--> AST check (§6.3) fails --> tool result explains why,
                 |                               Eve revises and retries
                 v
          interrupt()  (§5.2)   <-- Aegra checkpoints; the run pauses
                 |
     operator resumes with approved / rejected
                 |
         approved --> eve_tool row: source, sha256, approved_by, approved_at
                 |
                 v
  registry arm (5a's third source) --> search_skills finds it
                 |
                 v
  materialize() --> StructuredTool --> tools_client.invoke(target="sandbox")
                 |
                 v
  eve-sandbox /invoke: verify sha256, subprocess with limits, return result
```

Nothing here is a new mechanism. The proposal is a tool call; the gate is
LangGraph's `interrupt()`, which Aegra already persists and resumes; discovery
reuses the registry arm Phase 5a added; binding reuses
[`materialize.py`](../../../src/eve/skills/materialize.py) unchanged; dispatch
reuses [`tools_client.py`](../../../src/eve/tools_client.py) with one new
parameter. The only genuinely new component is the sandbox service.

### 3.1 Module layout

```
src/eve/tools_authoring/
  __init__.py
  types.py       # ToolProposal, ApprovalDecision — shapes only
  inspect.py     # the AST allowlist checker; pure, no I/O
  store.py       # every eve_tool SQL statement
  propose.py     # the propose_tool tool: check, interrupt, persist
  registry.py    # approved tools -> DynamicToolSpec, for search_skills
  cli.py         # eve-tool list | approve | reject | revoke

src/eve_sandbox/
  settings.py    # its own settings object, holding one API key and nothing else
  execute.py     # subprocess, rlimits, timeout
  app.py         # the /invoke and /healthz surface
```

`eve_sandbox` imports **nothing** from `eve` — not `eve.settings`, not
`eve.memory`. `eve_tools` reaches into `eve.settings` and `eve.memory.store`
for its own reasons; the sandbox must not, because every import is a line of
code that could be tricked into reading something. It gets its own settings
object for the same reason `eve_tools` has one
([`eve_tools/settings.py`](../../../src/eve_tools/settings.py)), only more so.

---

## 4. The proposal

`propose_tool(name, description, args_schema, source)`, bound in the
`eve <-> tools` cycle beside `search_skills` and `write_skill`.

`source` must define exactly one module-level function:

```python
def run(arguments: dict) -> dict:
    ...
```

One entry point, dict in, dict out, JSON-serialisable both ways. This is not
an aesthetic choice: it is what lets the sandbox execute the code without
importing it into a process that has anything worth stealing, and what lets
`args_schema` be the *only* contract between Eve's model call and the code.

`args_schema` is a JSON Schema, exactly the shape
[`materialize.py`](../../../src/eve/skills/materialize.py) already consumes for
MCP tools. Its documented limitation — only string/integer/number/boolean
property types are mapped, richer types fall back to `str` — applies here
unchanged, and `propose_tool` rejects a schema using an unmapped type rather
than inheriting a silent wrong-validation bug.

### 4.1 What a sandbox tool is actually for

Given §6.2, a sandbox tool is a **pure computation over data Eve already
has**: parse this iCal blob, compute the amortisation on these numbers, diff
these two lists, reformat this into a table, work out the school-run timing
from these three constraints. Eve fetches with `eve-tools` and computes with
`eve-sandbox`.

That is a narrower capability than "Eve writes her own tools" suggests, and
the spec says so plainly rather than letting the reader discover it. It is
also the capability with a real gap today: Eve currently does arithmetic and
parsing inside a language model, badly and unverifiably, and a deterministic
function she wrote once and reuses is a genuine improvement.

---

## 5. The gate

### 5.1 Only an approver may propose

`propose_tool` requires a new permission, `tools.author`, resolved through the
existing chain — `family.yaml` -> `build_member_context` ->
`state["member"]["permissions"]` -> `permission_denial`
([`permissions.py:11`](../../../src/eve/specialists/permissions.py)) — with no
new enforcement mechanism. Without it, the tool returns the standard denial
string and Eve explains that she would need Noah for that.

This collapses proposer and approver into one person **on purpose.** The
alternative is a queue: a kid asks for a tool, Eve writes it, it waits for
Noah, and something has to notify him, track pending proposals, and resume a
thread whose member is not the approver. That is a workflow system for a
household of five, and the case it serves — someone who cannot approve code
wanting code written — is not a case worth building it for. If you can't
approve, you can't propose.

The consequence is that the interrupt always surfaces in a thread owned by
someone entitled to answer it, which is what makes §5.2 a three-line change
instead of a subsystem.

### 5.2 The interrupt

`propose_tool` calls `langgraph.types.interrupt()` with the proposal. The run
pauses, Aegra checkpoints it, and the operator resumes with
`Command(resume={"approved": bool, "why": str})` — from the Agent Chat UI or
the SDK, either way with no code here.

Two existing facts make this work, and both are worth citing because both look
like they would break it:

- **The graph is compiled without a checkpointer**
  ([`graph.py`](../../../src/eve/graph.py), final line), and `interrupt()`
  requires one. Aegra attaches its own Postgres persistence to graphs it
  serves, which is exactly why the compile omits one. So the interrupt works
  in Aegra and **not** in a bare unit test — §12 compiles with a `MemorySaver`
  for the interrupt tests specifically.
- **`ToolNode`'s error handler would otherwise swallow it.**
  `_handle_tool_error` degrades every tool exception to a string, which is
  what keeps a LiteLLM outage from killing a run. `GraphBubbleUp` — the class
  interrupts raise through — is re-raised before reaching it, as
  [`graph.py`](../../../src/eve/graph.py)'s own comment on that function
  records. No change to the handler is needed, and §12 pins this with a test
  so a future refactor of `_handle_tool_error` cannot silently turn the
  approval gate into an auto-approval.

### 5.3 Approval binds to the bytes

`eve_tool` stores `source` and `source_sha256`. `approved_at`/`approved_by`
are set against that hash. The sandbox recomputes the hash of the source it is
about to run and refuses on a mismatch.

So: no editing an approved tool. A changed body is a new row, unapproved,
needing its own interrupt. Eve calling `propose_tool` with an existing
`name` supersedes nothing until the new version is approved, and the old
approved version keeps serving until it does.

This is the one place in the program where "supersede in place" — the pattern
Phases 2 and 5a use everywhere — is the wrong shape. An approval is a
statement about specific bytes at a specific time, and a store that lets those
bytes change underneath it is a store where the approval means nothing.

### 5.4 What the approver is actually looking at

The interrupt payload renders: the name and description, the args schema, the
full source, the AST checker's report of which allowlisted modules it imports,
and the requesting member and thread. Phase 5b's `eve-eval gate` status is not
included — it says nothing about a specific proposal — but §1's P4 asks for it
to be green as a matter of process, because approving code into a system you
cannot currently measure is the wrong order.

---

## 6. The sandbox

### 6.1 Why not `eve-tools`

`eve-tools` holds every third-party credential in the deployment (ADR 0006).
Running Eve-authored code inside it would hand attacker-influenceable code the
Gmail token, the Home Assistant token, and the Monarch session — the precise
blast radius that ADR made a whole service to contain.

So `eve-sandbox` is a second isolated service with the **opposite polarity**:
`eve-tools` holds all the credentials and runs only human-written code;
`eve-sandbox` runs machine-written code and holds nothing. Two services rather
than one because their invariants are exact opposites, and a single service
satisfying both is a service satisfying neither.

### 6.2 The isolation contract: pure functions

**A sandbox tool is a pure function.** No network, no filesystem beyond a
per-call tmpfs, no environment variables, no credentials, no cluster identity.

This is the decision the whole phase rests on, and it is chosen because it
*removes* the exfiltration class rather than mitigating it. With no egress
there is no data to steal *to anywhere*; with no credentials there is nothing
to steal; with no cluster identity there is nothing to escalate to. The
remaining hostile capability of a maximally-malicious approved tool is: burn
one CPU second, allocate some memory, and return a wrong answer to Eve. The
first two are bounded in §6.4. The third is what the approval gate is for, and
it is a correctness problem rather than a security one.

Enforced in three places, deliberately redundant:

| Layer | Mechanism |
|---|---|
| Cluster | `NetworkPolicy` default-deny egress; no ServiceAccount token; no secret mounts; read-only root filesystem; non-root UID |
| Process | Subprocess in isolated mode (`-I`) with an empty environment, cwd on a tmpfs, rlimits (§6.4) |
| Source | The AST allowlist (§6.3) |

### 6.3 The AST check is not the security boundary

The checker in `inspect.py` walks the parsed source and rejects: any import
outside the allowlist, any attribute access to a dunder name, and any
reference to `eval`, `exec`, `compile`, `open`, `__import__`, `globals`,
`locals`, or `vars`.

Allowlist: `json`, `re`, `math`, `decimal`, `statistics`, `datetime`,
`zoneinfo`, `itertools`, `functools`, `collections`, `textwrap`, `string`,
`dataclasses`, `typing`, `base64`, `hashlib`, `urllib.parse`, `uuid`.
`urllib.parse` is in and `urllib.request` is out — parsing a URL is
computation, fetching one is not.

**This check is an accident guard and a feedback mechanism, not a security
boundary.** A determined bypass of an AST allowlist exists; treating it as
containment is a well-travelled way to get owned. Its real jobs are to give
Eve a specific, actionable error so she can revise before bothering a human,
and to make the approver's read short. The containment is §6.2's first layer:
the pod. Every guarantee in this phase must hold with the AST checker assumed
defeated, and §12 tests it from that assumption.

> ponytail: process isolation via subprocess + rlimits + a no-egress pod. If
> the lab ever runs gVisor or Kata, add `runtimeClassName` to the sandbox pod
> and the remaining kernel-surface argument goes away too.

### 6.4 Execution limits

One subprocess per call, no reuse, no warm pool:

| Limit | Value | Setting |
|---|---|---|
| Wall clock | 5s, then SIGKILL | `EVE_SANDBOX_TIMEOUT_SECONDS` |
| CPU (`RLIMIT_CPU`) | 5s | — |
| Address space (`RLIMIT_AS`) | 256 MiB | `EVE_SANDBOX_MEMORY_MB` |
| Output | 64 KiB, truncated | `EVE_SANDBOX_MAX_OUTPUT_BYTES` |
| Concurrency | 4 in flight, then queue | `EVE_SANDBOX_MAX_CONCURRENCY` |

No warm pool because process startup is milliseconds against a `VOICE` model
call, and a reused interpreter is state shared between two tools — which is
the one thing §2.1 says a tool does not get.

Both a wall clock and `RLIMIT_CPU`: they catch different failures. A busy loop
burns CPU and both fire; a `time.sleep` burns none, so only the wall clock
does.

### 6.5 The `/invoke` contract

Identical in shape to `eve-tools` ([`eve_tools/app.py`](../../../src/eve_tools/app.py)) —
bearer auth, `{"tool": ..., "arguments": ...}`, `{"result": ...}` or
`{"error": ...}` — so [`tools_client.py`](../../../src/eve/tools_client.py)
works against it with one added parameter and no new failure handling. Its
existing behaviour is already right for this: every failure becomes an
`error:` string rather than an exception, so a dead sandbox produces an Eve
sentence and not a 500.

Eve's container sends `source` with each call rather than the sandbox reading
the database. The sandbox therefore needs no database credential — the last
credential it might otherwise have held — and the `source_sha256` it verifies
against travels with the request from a row only Eve's container can read.

---

## 7. Dispatch

An approved tool becomes a `DynamicToolSpec` with `server_id: "sandbox"`, and
from there the existing path carries it:
`registry.load_skills` surfaces it (the third source Phase 5a's registry arm
established), `search_skills` ranks it and appends the spec to state,
`materialize` builds a `StructuredTool` from the schema, and the callable posts
to `/invoke`.

`tools_client.invoke` gains `target: str = "tools"`, selecting base URL and API
key from settings. `materialize` passes `target="sandbox"` when
`spec["server_id"] == "sandbox"`. That is the entire integration: two small
edits, no new state field, no graph change.

`dynamic_tools_cap` (default 8) already bounds how many dynamically-bound
tools accumulate in state, and sandbox tools share that budget. No second cap.

---

## 8. Storage

### 8.1 The table

```sql
CREATE TABLE eve_tool (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name           text        NOT NULL,
  description    text        NOT NULL,
  args_schema    jsonb       NOT NULL,
  source         text        NOT NULL,
  source_sha256  text        NOT NULL,
  proposed_by    text        NOT NULL,
  proposed_at    timestamptz NOT NULL DEFAULT now(),
  source_thread  text,
  source_run     text,
  approved_by    text,
  approved_at    timestamptz,
  rejected_why   text,
  revoked_at     timestamptz,
  revoked_why    text,
  invocations    bigint      NOT NULL DEFAULT 0,
  last_used_at   timestamptz
);

CREATE UNIQUE INDEX eve_tool_live_name ON eve_tool (name)
  WHERE approved_at IS NOT NULL AND revoked_at IS NULL;
```

The partial unique index is the §5.3 invariant in the schema: one live
approved version per name, while unapproved proposals and revoked history
accumulate freely. A revoked name can be reused by a replacement, the same
pattern `eve_pat_active_label` uses for tokens.

A real table rather than a memory layer — the opposite of Phase 5a's call —
because none of what made that lazy choice right applies. This row is
executable, has an approval binding to a hash, needs a uniqueness constraint,
and must never be reachable by semantic recall into a prompt. A `text`
`content` column with an embedding is the wrong shape in every one of those
respects.

### 8.2 Alembic

Task one, before `eve_tool` exists. Phase 5b leaves `MIGRATIONS` at five
entries and [`db.py:11`](../../../src/eve/memory/db.py) names ~5 as the point
to switch; `eve_tool` is the sixth, so the switch happens here.

The constraint that made hand-rolled migrations right in Phase 2 has not gone
away: Aegra runs its own Alembic migrations at startup and Eve's must not
interleave. So Eve's Alembic gets its own `script_location` and, critically,
`version_table="eve_alembic_version"` — sharing Aegra's `alembic_version`
table is how two independent migration histories corrupt each other.

Revision one reproduces the five existing entries and is a no-op against an
already-migrated database (`CREATE TABLE IF NOT EXISTS` throughout, plus a
stamp path for deployments already carrying `eve_schema_version`). Revision two
adds `eve_tool`. The `eve-migrate` console script keeps its name and its
contract — run before `aegra serve`, fail the pod loudly on a schema problem —
so nothing outside `db.py` changes.

---

## 9. Revocation, failure, and the kill switch

| Situation | Response |
|---|---|
| A tool is wrong or unwanted | `eve-tool revoke <name> --why …`. Sets `revoked_at`; the row and its source stay for the audit trail. Takes effect on the next `load_skills`, which is rebuilt per call ([`registry.py`](../../../src/eve/skills/registry.py)) — no restart. |
| Something is badly wrong | `EVE_SANDBOX_ENABLED=false`. `propose_tool` unbinds, no sandbox spec is registered, and already-bound specs in checkpointed state fail closed to an error string rather than dispatching. |
| Everything must go | `eve-tool revoke --all`, one statement. |
| The sandbox is unreachable | `tools_client` returns `error: eve-sandbox unavailable (…)`; the turn continues and Eve explains. |
| The tool raises, times out, or exceeds a limit | `{"error": …}`, becoming a tool-result string. Recorded against the row so a habitually-failing tool is visible in `eve-tool list`. |
| The source hash mismatches | Refuse, log loudly, and increment nothing. This means the database and the caller disagree about approved bytes, which is a tampering signal, not a bug to retry. |

`EVE_SANDBOX_ENABLED` defaults to `false`, matching `ambient_enabled` and
`self_authoring_enabled`. Fail-closed on checkpointed specs matters: a thread
paused mid-conversation can carry a sandbox spec in `dynamic_tools` from before
the flag flipped, and a kill switch that a stale checkpoint can route around is
not a kill switch.

---

## 10. Settings

| Setting | Default | Purpose |
|---|---|---|
| `EVE_SANDBOX_ENABLED` | `false` | Master gate (§9). |
| `EVE_SANDBOX_BASE_URL` | `http://eve-sandbox:8091` | Dispatch target. |
| `EVE_SANDBOX_API_KEY` | `""` | Shared bearer token, like `EVE_TOOLS_API_KEY`. |
| `EVE_SANDBOX_TIMEOUT_SECONDS` | `5` | Wall clock (§6.4). |
| `EVE_SANDBOX_MEMORY_MB` | `256` | `RLIMIT_AS`. |
| `EVE_SANDBOX_MAX_OUTPUT_BYTES` | `65536` | Result truncation. |
| `EVE_SANDBOX_MAX_CONCURRENCY` | `4` | In-flight subprocesses. |

`model_post_init` refuses to start when `EVE_SANDBOX_ENABLED=true` without an
API key, and requires at least 32 characters when set — the same validation
`ambient_token` already carries, for the same reason: a guessable shared secret
on a service that executes code fails open.

### 10.1 Deployment and documentation

`infrastructure` gains `eve-sandbox` in the existing `eve` app: Deployment
(`automountServiceAccountToken: false`, `readOnlyRootFilesystem: true`,
`runAsNonRoot`, tmpfs `emptyDir` at `/tmp`, no `envFrom` beyond the API key),
Service, a default-deny-egress `NetworkPolicy`, and a Gatus check on
`/healthz`. No Ingress — it is reachable only from `eve`.

`Dockerfile.eve-sandbox` follows `Dockerfile.eve-tools`: same base, same
`uv sync --frozen --no-install-project`, same non-root UID pattern, copying
only `src/eve_sandbox`.

In this repository: `README.md`'s phase table (Phase 5 complete, program
complete), `docs/architecture.md` (the second isolated service, the authoring
path, the Alembic change), `.env.example`, and
`docs/adr/0010-sandboxed-tools-are-pure-functions.md` (§13).

---

## 11. Observability

| Attribute / field | Answers |
|---|---|
| `eve.sandbox.proposed` | Does Eve ever propose a tool, or is `propose_tool` dead weight? |
| `eve.sandbox.ast_rejected` | How often the checker catches something — and whether Eve learns to avoid it across revisions. |
| `eve.sandbox.approved` / `rejected` | The approver's actual behaviour. A 100% approval rate means the gate is a rubber stamp, which is worth knowing. |
| `eve.sandbox.duration_ms`, `eve.sandbox.timeouts` | Are the §6.4 limits right, or is 5s throttling legitimate work? |
| `eve_tool.invocations`, `last_used_at` | Is an approved tool used more than once? A tool used once was a wasted approval; Eve should have just done the arithmetic. |
| `eve.sandbox.hash_mismatch` | Should be zero forever. Non-zero is an incident. |

The most likely failure of this phase is **nobody ever proposes a tool** —
`propose_tool` exists, Eve never reaches for it, and a sandbox service runs
forever executing nothing. `proposed` staying at zero for a month is the
signal, and the honest response is to delete the phase rather than to prompt
harder. `invocations` is the second-order version: tools proposed, approved,
used once, never again.

---

## 12. Testing

| Level | What | How |
|---|---|---|
| Unit | AST checker accepts each allowlisted import and rejects each denied name, dunder access, and non-allowlisted import | pytest, table-driven |
| Unit | AST checker rejects `urllib.request` while accepting `urllib.parse` | pytest |
| Unit | `propose_tool` rejects a schema with an unmapped JSON Schema type rather than silently degrading to `str` (§4) | pytest |
| Unit | `propose_tool` without `tools.author` returns the denial string and never interrupts | pytest |
| Unit | **`interrupt()` from inside a tool reaches the caller and is not swallowed by `_handle_tool_error`** (§5.2) | pytest, graph compiled with `MemorySaver` |
| Unit | Resuming with `approved: false` writes `rejected_why` and no approval; with `true` writes `approved_by`/`approved_at` | pytest |
| Unit | Re-proposing an existing name leaves the live approved version serving until the new one is approved (§5.3) | pytest |
| Unit | The partial unique index rejects a second live approved row for one name | integration, real Postgres |
| Unit | The sandbox refuses a source whose sha256 does not match the request | pytest |
| Unit | **Assuming the AST checker defeated**: source doing `import os` and reading a file, or opening a socket, is executed and fails on the pod's own constraints rather than succeeding | integration, marked, with the checker bypassed explicitly |
| Unit | Timeout, memory, and output limits each fire and return an error rather than hanging or crashing the service | integration |
| Unit | `EVE_SANDBOX_ENABLED=false`: `propose_tool` unbound, and a checkpointed sandbox spec in `dynamic_tools` fails closed (§9) | pytest |
| Unit | Revoke removes the tool from the next `load_skills` with no restart | pytest |
| Unit | `eve_sandbox` imports nothing from `eve` | an import-graph assertion, like Phase 5b's |
| Integration | Alembic revision one is a no-op against a database already carrying all five `eve_schema_version` entries, and does not touch Aegra's `alembic_version` | `docker-compose.test.yml` |
| Integration | Propose, interrupt, approve, discover through `search_skills`, invoke, get a correct result — end to end | `docker-compose.test.yml` |
| Live | The deployed sandbox pod has no ServiceAccount token, cannot resolve or reach any external host, and cannot write outside `/tmp` | marked `live`, run by hand against the cluster |

The two tests that matter most are the ones easiest to leave out: the
interrupt-not-swallowed test, because without it a refactor of
`_handle_tool_error` turns the approval gate into an auto-approver silently;
and the checker-defeated test, because it is the only thing that verifies
§6.3's claim that the AST check is not what is holding the line.

---

## 13. Definition of done

| # | Criterion |
|---|---|
| 1 | Eve proposes a tool; the run pauses; the operator sees name, description, schema, full source, and imports; resuming approves or rejects. |
| 2 | A rejected proposal is recorded and does not execute. Eve does not auto-retry it. |
| 3 | An approved tool is found by `search_skills`, bound by `materialize`, invoked through `/invoke`, and returns a correct result. |
| 4 | Editing an approved tool's source requires a fresh approval; the old version serves until the new one is approved. |
| 5 | A source-hash mismatch is refused and logged. |
| 6 | A member without `tools.author` cannot propose. |
| 7 | Source that imports `os`, opens a socket, or reads a file fails — with the AST checker bypassed, on the pod's constraints alone. |
| 8 | Timeout, memory, and output limits each produce an error string and a live service. |
| 9 | `eve-tool revoke` removes a tool with no restart; `EVE_SANDBOX_ENABLED=false` fails closed even for a checkpointed spec. |
| 10 | The deployed sandbox has no ServiceAccount token, no secret mounts, a read-only root filesystem, and no reachable egress. |
| 11 | Alembic is in place, is a no-op against an already-migrated database, and does not share Aegra's version table. |
| 12 | `eve_sandbox` imports nothing from `eve`. |

---

## 14. Decision records

| ADR | Change |
|---|---|
| 0006 (eve-tools isolation) | **Extended by symmetry.** A second isolated service with the opposite invariant: `eve-tools` holds every credential and runs only human-written code; `eve-sandbox` runs machine-written code and holds nothing. The ADR's reasoning — bound the blast radius of the thing most likely to go wrong — is what produces two services rather than one (§6.1). |
| 0001 (Agents as subgraph tools) | **Upheld.** A sandbox call is a leaf tool call, landing after the first streamed token and inside the same Langfuse trace, exactly as the ADR permits for `eve-tools`. |
| 0002 (No LLM before first token) | **Untouched.** Everything here is inside the tools loop. |
| 0005 (Memory storage) | **Bounded, deliberately.** Phase 5a put authored prose in `eve_memory`; executable code does not go there (§8.1). The ADR's storage model is right for text and wrong for code, and this is the phase that says where the line is. |
| — | **New (ADR 0010).** Sandboxed tools are pure functions with no network, no credentials, and no filesystem; the pod is the security boundary and the AST allowlist explicitly is not (§6.2, §6.3). |
| — | **New (ADR 0011).** Eve's schema migrations move to Alembic with a private version table, kept separate from Aegra's own migration history (§8.2). |

---

## 15. Where the program ends

With 5c shipped, every requirement in the core design is delivered, and it is
worth recording what the program deliberately never does:

- **Eve does not approve her own code.** There is no path, no setting, and no
  "trusted tool" tier. The one human gate in the program stays.
- **Eve does not author credentialed capability.** A tool needing a secret is
  an `eve-tools` handler in a pull request, forever. The self-improvement
  boundary is drawn at "computation Eve can verify," not "actions Eve can
  take."
- **Eve does not rewrite her own persona.** `prompts/eve.md` is human-authored.
  Phase 5a lets her write rules *under* it; nothing lets her edit it.
- **Eve does not learn unsupervised.** Rules come from a specific turn with a
  specific member (5a §4), hygiene never auto-resolves a contradiction
  (5b §8), and code needs a human (this phase). The reflection loop deferred in
  5a §1.1 is deferred permanently, not pending.

Those four lines are the actual answer to "how far does self-improvement go,"
and they are here rather than in a future document because the end of the
program is the right place to be explicit that it has one.
