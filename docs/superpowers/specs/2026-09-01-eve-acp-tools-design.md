# ACP tools — design

**Issue:** [EVE-4 Add ACP tools](https://linear.app/chalifour-development/issue/EVE-4/add-acp-tools)
**Status:** Design approved, not yet implemented
**Date:** 2026-09-01

## What this is

Eve delegates coding work to harnesses built for coding. A family member asks
for a change; Eve hands the goal to Claude Code, Codex, or OpenCode over the
[Agent Client Protocol](https://agentclientprotocol.com), holds a real
multi-turn conversation with it while it works, and comes back with pull
requests.

She is not a conduit. She sends the goal, reads the agent's turns, answers its
questions herself out of household context, redirects it when it goes wrong,
and only returns to the family with a result or a genuine blocker. The family
talks to Eve; Eve talks to the coding agent.

This adds no sixth deploy. It extends `eve-computer`, which already has the
persistent home, the shell, the internet, Eve's own accounts, and a proven
LiteLLM-routed harness.

## Why ACP, and not `codex exec`

The box has a shell, so `codex exec "..."` is already available and costs
nothing to reach. That is the lazy answer and it does not hold, for one
reason: it is one-shot. Everything EVE-4 asks for past "prompt" — monitor,
converse, redirect — needs a session that stays alive between turns, streams
what it is doing, and can be replied to. `codex exec` has no such surface, and
three CLIs have three different not-quite-surfaces.

ACP is what makes the second and third harness free. It normalises session
lifecycle, streaming updates, permission requests, and filesystem access
across all three, so adding OpenCode is a dict entry rather than an
integration. This is the same reason the repo speaks MCP rather than three
vendor tool APIs.

## Where it runs

`eve-computer`, not a new service.

The box already is what this needs: a PVC-backed home for repo clones, a
shell, unrestricted egress to GitHub and package registries, Eve's own GitHub
identity, a LiteLLM key with its own spend cap, VNC oversight, and a
NetworkPolicy that already denies every route back into the cluster. A sixth
deploy would duplicate all of it and add a second identity to provision by
hand over a VNC session it does not have.

Running the agents inside Eve's own container was rejected outright. A coding
agent with a shell and write access, in the container that holds Postgres, the
family roster, and every credential, inverts ADR 0006 and ADR 0010 at once.

**Two lanes, one process.** `/tasks` and its single worker are untouched — GUI
tasks stay serialised behind the one X display and the one mouse. Coding
sessions are independent `asyncio` tasks under a semaphore, because they need
no display and a long conversation must not block the desktop.

## The session model

A **session** is the unit, not a task: one ACP subprocess, one or more git
worktrees, and one row in Eve's Postgres, living across many of Eve's turns.

The state machine follows ACP's own grain rather than inventing one.
`session/prompt` returns a `stopReason` when the agent's turn ends; the
protocol has no separate "I am asking you a question" signal.

```
queued → running → idle → running → … → finished | failed | killed | stale
                    ↑                        ↑
             agent's turn ended        Eve's judgement
```

`idle` is the load-bearing state. The agent's turn ended and its last message
is sitting there. That message might be "done, opened PR #42", might be "which
auth library do you want?", might be a stall.

**The box does not classify it.** It has no family context and no business
guessing. Eve reads the turn and decides: reply, close it out, or escalate.
That decision is the entire content of "Eve converses autonomously."

### Monitoring, at two granularities

"Monitor" means two different things and needs two different mechanisms:

- **Turn-granular**, for conversing. The box keeps an append-only list of turn
  records. Eve polls with a cursor (`?since=n`) and receives only what is new.
- **Live**, for "how's it going?". A rolling `activity` field holding the last
  handful of `session/update` notifications — current tool call, file being
  edited. Overwritten, not accumulated. A member asking mid-turn gets a real
  answer without the box streaming anything to Eve.

### What crosses the boundary

Session id, agent name, repo names, prompt text. Nothing else. No member
subject, no names, no roster, no permissions.

Eve's supervisor reasoning happens in *her* container with full context, and
only the resulting prompt text goes over the wire. `eve-computer`'s standing
invariant — *the box learns nothing about the family* — survives this work
intact, and that is why the supervisor lives where it does.

## Inside the box

New module `src/eve_computer/acp/`:

| File | Responsibility |
|---|---|
| `registry.py` | The three agents, as a dict: command, args, env. |
| `client.py` | The ACP client half: auto-approve `session/request_permission`; serve `fs/read_text_file` and `fs/write_text_file`, confined to the session directory. |
| `session.py` | One live session: subprocess lifecycle, turn log, rolling `activity`, pending-message list, bounds enforcement. |
| `repo.py` | Clone, worktree add and remove, push, PR. |

### The agent registry

Three entries, one shape: a model name in, a command and environment out. No
plugin system, no abstract base class.

```python
AGENTS = {
    "claude":   lambda m: (["claude-code-acp"], {"ANTHROPIC_BASE_URL": ..., "ANTHROPIC_MODEL": m, ...}),
    "codex":    lambda m: (["codex-acp", "--model", m], {"LITELLM_API_KEY": ...}),
    "opencode": lambda m: (["opencode", "acp", "--model", m], {"LITELLM_API_KEY": ...}),
}
```

**The box owns neither the agent choice nor the model list.** It takes an agent
name and a model name and spawns the subprocess. Both are chosen and validated
in Eve's container, for the reason in the next section.

Claude Code and Codex reach ACP through the official adapters
(`agentclientprotocol/claude-agent-acp`, `agentclientprotocol/codex-acp`).
OpenCode speaks it natively via `opencode acp`.

### LiteLLM routing is three mechanisms

EVE-4 is explicit that every harness runs on the lab's LiteLLM-hosted models.
The three do it three different ways, and flattening that into one abstraction
would be a lie:

- **Claude** — `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` in the subprocess
  environment. Already proven in `src/eve_computer/harness.py`.
- **Codex** — `~/.codex/config.toml` with a `[model_providers.litellm]` block
  (`base_url`, `env_key`, `wire_api = "responses"`) and `model_provider =
  "litellm"`. LiteLLM needs `drop_params: true` for this path.
- **OpenCode** — an OpenAI-compatible provider block in
  `~/.config/opencode/opencode.json`.

`$HOME` *is* the PVC, so all three config files are written idempotently by the
existing `bootstrap.sh` from templates baked into the image — the same
self-healing-across-reschedule philosophy as `packages.txt`. A wiped PVC
recovers model routing with no human involved.

**Two things about LiteLLM routing that are easy to get backwards.**

*Codex through LiteLLM still rides the subscription.* LiteLLM fronts the
ChatGPT/Codex sign-in itself (ADR 0004: it fronts two subscription proxies and
no metered general-purpose API), so pointing `codex-acp` at LiteLLM keeps
`eve-computer`'s *"zero metered spend"* property rather than abandoning it.
The same is true of OpenCode pointed at a `chatgpt/*` model. The one agent
with real metered spend is **Claude Code**, whose `ANTHROPIC_BASE_URL` path
resolves to `anthropic/claude-sonnet-5` on the metered `anthropic_api_key`.

*Some `chatgpt/*` names in the catalogue are refused by the backend.* ADR
0004's live probe found `gpt-5.3-codex`, `-codex-spark`, `-instant`,
`-chat-latest`, and `gpt-5.4-pro` all rejected — *"The '<model>' model is not
supported when using Codex with a ChatGPT account."* LiteLLM still lists them,
so Eve can pick one and it will fail.

**That is fine, and it is why the deny-list is one prefix rather than a table.**
The line is not "which models work" but **how they fail**:

- `ocp/*` fails *silently* — tool definitions are stripped, the agent answers
  fluently and changes nothing. Undetectable at runtime, so it is denied.
- A refused model name fails *loudly, at the first prompt*, with the backend
  saying exactly why. Eve reports it, retries with another, and remembers.

Enumerating the refused set here would be a second list to keep in sync with
OpenAI's generation renames — precisely the staleness this design avoided by
not putting a `CODING_MODELS` table in `models.py`. Loud failures do not need
a registry; silent ones do.

**The cost that actually needs watching is rate limits, not dollars.**
`models.py` is explicit that `REFLEX` was moved off the ChatGPT credential so
it would not *"consume the rate limits Noah uses for his own work"*, and
`tests/test_models.py` carries the same worry in a comment. A coding agent
running for thirty minutes is a far heavier consumer of that subscription than
any chat turn Eve takes. The session bounds below are the throttle, and the
choice of default agent (next section) is the other half of it.

### Agent and model are both chosen per task

Neither is pinned. Eve picks both per session, from the goal and from whatever
the member has said they want, because "add a log line" and "port the
extraction pipeline" deserve neither the same harness nor the same model.

**Eve chooses inline, in the turn she is already taking.** No routing model
call, no benchmark table, no scoring function — she has the goal in front of
her and the candidates in the tool schema. This is the same thing she already
does when choosing which specialist to ask.

**Codex breaks ties, rather than being a default.** When nothing in the task or
the member's preferences points anywhere, take the one that rides the
subscription. Cheapest thing that works is the tiebreak, not a policy.

**Member preferences need no new machinery.** They are already memory: *"use
Claude Code for anything touching the graph"* is a Phase 5a authored rule,
revocable from the existing CLI, and it reaches this decision through the
recall snapshot the session takes at creation. Nothing here gets its own
preference store.

#### The model candidates are LiteLLM's live catalogue

Not a curated list. `GET /v1/models` against LiteLLM, cached in Eve's container
with a short TTL, is the candidate set — so a model registered in the
infrastructure repo is usable here the same day, with no change in this
repository.

**This deliberately does not go in `models.py`,** which declares itself *"the
ONLY place model identifiers appear."* That invariant exists so that retiering
*Eve* is a one-file change, and it is about the five tiers she runs on. A
delegated coding model is not a tier: it is an argument to a subprocess,
selected per task, drawn from a set this repository does not define. Putting a
static `CODING_MODELS` table beside `TIER_MODELS` would be the worse outcome —
a second list to keep in sync with the proxy, going stale silently. `models.py`
keeps owning tiers; the proxy owns its own catalogue.

#### One exclusion, and it is not speculative

**`ocp/*` is denied.** ADR 0004 probed it live and found the proxy strips tool
definitions before the model sees them — asked to call a tool, Claude replies
it has no such tool. A coding agent that cannot call tools would answer
fluently and change nothing, for thirty minutes, which is the worst failure
mode available. That is a one-prefix deny-list against a proven finding, not a
guess.

#### Agent × model compatibility is discovered, not declared

The three agents speak three wire shapes — Anthropic Messages for Claude Code,
the Responses API for Codex, OpenAI-compatible for OpenCode — and LiteLLM
translates between them. Some cells of that cross-product will not work.

This design does **not** ship a compatibility matrix, for the reason ADR 0004
learned the hard way: its original fallback plan died on an untested assumption
about exactly this translation, and the fix was a live probe
(`test_live_models.py::test_fallback_model_emits_tool_calls`), not a table.
A declared matrix here would be the same mistake with more rows.

Instead:

- The `live` test tier probes the agent × model pairs that matter and is the
  only thing entitled to claim a pair works.
- At runtime a bad pair **fails at the first prompt**, not silently and not
  thirty minutes in. Eve reports it and can retry with a different pair.
- A pair that failed is a fact Eve can remember, through the memory system she
  already has. No bad-pair registry, no new table.

**Validation at dispatch** is therefore against the live catalogue and the
`ocp/*` deny-list — enough to catch a hallucinated model name before it costs a
member several minutes, and no more than that.

### Worktrees

One clone per repo, one worktree per repo per session, so parallel sessions on
the same repository cannot collide:

```
/home/eve/code/<owner>/<repo>                     the clone, kept fetched
/home/eve/sessions/<session-id>/<repo>/           worktree, branch eve/<slug>-<short-id>
```

A multi-repo session gets one worktree per repo, side by side under the session
directory, which is the agent's `cwd`. That is how a human does a cross-repo
change, and it makes the `fs/*` confinement *simpler* than the single-repo
case: one root to bound, covering every worktree.

The same branch name is used across every repo in a session, so related pull
requests are identifiable at a glance.

**Parallel worktrees share one stash stack.** The operator prompt and
`/home/eve/AGENTS.md` instruct the agents never to use bare `git stash` — a WIP
commit on their own branch instead. One cheap instruction; without it one
session can pop another's work.

### Pull requests

**The box opens them, not the agent.** On session close, per repo that has
commits: `git push -u origin <branch>` then `gh pr create --fill`.

Deterministic, and it hands Eve a real URL rather than hoping the agent
remembered to produce one. `--fill` means the agent's own commit messages
become the PR body, so nothing it authored is lost. A repo the agent touched
but never committed to gets no PR, and Eve says so plainly. Then `git worktree
remove`.

**Cross-repo pull requests are not atomic and this design does not pretend
otherwise.** Eve reports the set of links, in merge order where the goal
implies one. A human merges them. Anything else would be inventing a
distributed-commit protocol for a two-person household.

### Repo access is GitHub collaborator status

`gh auth login` once, by hand, over VNC. The token lives on the PVC exactly
like her browser session cookies: never a Kubernetes Secret, never an
environment variable, never a line in this repository.

Which repositories Eve can touch is therefore a checkbox in your GitHub
account, revocable without a code change — ADR 0015's argument for her other
identities, reused rather than re-litigated. There is no allow-list in this
repo to keep in sync.

### HTTP surface

Alongside the existing `/tasks`, in the same bearer-token shape:

- `POST /sessions` `{id, agent, repos, prompt}` → `202`
- `GET /sessions/{id}?since=n` → `{status, activity, turns, pending}`
- `POST /sessions/{id}/prompt` `{text}` → enqueue the next prompt
- `POST /sessions/{id}/close` → push, open PRs, tear down → `{prs, commits}`
- `DELETE /sessions/{id}` → kill the subprocess, keep the worktrees

**Eve polls the box; the box never calls Eve.** Unchanged, and load-bearing: a
compromised machine still has no inbound path to anything. It is also why the
rejected alternative — proxying raw ACP to Eve's container so she receives
`session/update` push — was rejected. `session/update` is server-push, and
accepting it would invert the one-directional rule the entire `eve-computer`
safety argument rests on, while moving the protocol state machine into the
container that holds the roster.

### Image additions

`gh`, `codex-acp`, `opencode`, `claude-code-acp`.

## Eve's side

New module `src/eve/coding/`, mirroring `src/eve/computer/`: `store.py` (the
`eve_coding_session` table and its Alembic migration), `dispatch.py` (the
tools), `supervisor.py` (the control loop).

### Three tools

- `delegate_coding_task(repos: list[str], goal: str, agent: str | None, model: str | None)` —
  permission check, validate agent and model, create the session, return *"I'm
  on it."* Eve picks both per task, per the section above; Codex breaks ties.
- `check_coding_session()` — lists live sessions with short ids, repos, goal,
  status, and the box's rolling `activity`, so Eve can say what a session is
  doing right now.
- `send_to_coding_session(session_id, message)` — member interjection.

A new `code.delegate` permission gates all three, checked in Eve's container
before the HTTP call, per ADR 0006.

Addressing a session needs no resolution logic: `check_coding_session()`
already returns the live sessions with their short ids, and Eve picks from that
list the way she picks from any tool result. No naming scheme, no fuzzy
matching, no thread-scoped disambiguation.

### The supervisor

A second `asyncio` loop in `eve_ambient/app.py`, ticking at ~20s. Its own
interval, deliberately not the 300s ambient tick: this is a control loop, not a
notification pipeline. Per tick, for each live session:

1. `GET` the box. Still `running` → nothing to do.
2. `idle` → pull new turns since the cursor and make **one** LLM call on
   `Tier.CODE` with structured output `{action: reply | done | escalate, text}`.
3. `reply` → `POST` the prompt back; the session returns to `running`.
   `done` → close the session, which triggers push and PR creation.
   `escalate` → the agent is blocked on something Eve cannot answer.
4. `done` resolves the session and feeds report-back.

**`escalate` does not resolve the session — it parks it.** The session stays
`idle` with its worktrees and subprocess alive, and report-back asks the member
the question. Their answer arrives through `send_to_coding_session` and the
session resumes exactly where it stopped. Escalating and then discarding the
session would throw away the very thing the member's answer is for. A parked
session is closed by its own wall-clock bound if nobody ever answers.

**Recall runs once per session, not once per tick.** The reason the supervisor
lives in Eve's container rather than on the box is household context — but a
full hybrid recall every twenty seconds would be indefensible. Recall runs when
the session is created and snapshots into the session row; every supervisor
call reuses that snapshot alongside the goal and the transcript. One recall per
session, and the reason for the placement survives.

### Interjection is an input, not a second control path

`send_to_coding_session` appends to a pending-message list on the session. A
list, not a slot, so two rapid corrections cannot drop one.

When the session next goes `idle`, pending member messages are handed to the
**existing** supervisor call as extra input, marked as taking priority over its
own judgement. Eve still composes the actual prompt, so a member's correction
and the agent's own open question are answered together in one coherent reply
instead of racing each other. No second code path, no second decision-maker.

**Messages queue rather than interrupt.** A member correcting course mid-turn
waits until the turn ends. ACP does expose `session/cancel`, and *"stop, you're
going the wrong way"* is exactly when you would reach for it — but cancelling
mid-turn risks a half-written file tree, and a correction landing at the end of
the current turn is still interjection. `interrupt=True` is a flag on this same
path once the abort semantics are worth guessing at.

### Report-back

A new ambient source, `src/eve_ambient/sources/coding.py`, mirroring
`sources/computer.py` — including both of its deliberate deviations:

- **The relevance filter is bypassed.** This was explicitly requested by a
  member. An LLM deciding a direct request is "not relevant" and swallowing it
  is the worst available failure mode.
- **`gates.py` gains an explicit `coding` permission mapping.** It fails closed
  on unmapped sources — correct behaviour that will silently notify nobody if
  this step is forgotten.

Everything downstream is existing machinery: gate on permissions, compose a
turn as Eve on the originating thread, push via ntfy.

## Bounds and error handling

| Bound | Enforced by |
|---|---|
| Max turns per session | The box, on `session.py` |
| Max wall-clock per turn | The box, `asyncio.wait_for` |
| Max wall-clock per session | The box |
| Max supervisor turns | Eve's container |
| Spend | The LiteLLM key's own budget |

Hitting a box-side bound parks the session `failed` with the reason, which Eve
reports in her own voice. Hitting `max_supervisor_turns` **parks the session
and escalates to the member** rather than stalling silently — the same instinct as `graph.py`'s
`_LOOP_EXHAUSTED`: whatever the budget is, a loop that blows it has to answer
in English.

A session whose box restarted mid-run is marked `stale` by the supervisor after
a timeout, exactly as `eve.computer.poller` already does for computer tasks,
rather than hanging in `running` forever. Its worktrees survive on the PVC.

Every `tools_client` call degrades a failure to a returned error string rather
than raising, because the caller is a tool whose result goes straight to a
model — the existing posture, unchanged.

## Oversight

**Live:** VNC over `kubectl port-forward`, already there. Sessions run
headless, but their worktrees and logs are on the disk you are looking at.

**Kill switches:** `DELETE /sessions/{id}` for one session; `kubectl scale
deploy/eve-computer --replicas=0` for everything. Worktrees survive both.

**After the fact:** the turn log per session, the branches on the PVC, the pull
requests themselves, and Langfuse traces of every supervisor call — which
happens in Eve's container and is therefore traced like any other Eve turn.

**The real gate is the pull request.** Nothing here merges anything.

## The boundary this does and does not move

The README's *"Eve does not author credentialed capability"* boundary is
untouched by this work — ADR 0015 already settled that a granted, human-
provisioned, revocable identity is a different thing from authored capability
over the family's credentials, and Eve's GitHub account is one more such
identity.

But one consequence deserves to be stated rather than discovered: **Eve can now
open pull requests against this repository.** The README says a tool needing a
secret is *"an `eve-tools` handler in a pull request, forever."* She can now
write that pull request.

This does not weaken the boundary; it routes through it. The gate was never
"Eve cannot propose" — it was "a human merges." That gate is exactly where it
was, and unlike the `propose_tool` interrupt, this one is a code review in
GitHub with a diff, CI, and no 11pm approval prompt. It is a *better*
instance of the same gate.

## Testing

**Unit tier** (`not integration and not live and not docker`), with the box
mocked: the session state machine, the supervisor's four-way decision, the
pending-message merge into a supervisor call, the permission gate, the ambient
source, and worktree path construction.

**Integration** runs the real image under docker-compose: a session against a
throwaway local git repo, driven by a stub ACP agent rather than a real model,
asserting the full lifecycle — create, turn, idle, reply, close, branch pushed.

**Docker tier** extends `test_computer_docker_image.py`'s binary check to
`gh`, `codex-acp`, `opencode`, and `claude-code-acp`, and asserts
`bootstrap.sh` writes all three model-routing config files.

**`live`** drives real sessions across the agent × model pairs that matter,
against a scratch repo. This is the only tier entitled to claim a pair works,
and the only thing that catches a `wire_api` or provider-block mistake. Per
ADR 0004's own history, these are probed, never inferred.

**The test that matters most is the existing one.** `eve-computer`'s
unreachability assertion — that Postgres, `eve-tools`, `eve-sandbox`, Eve's
API, and the Kubernetes API server cannot be reached from inside the box — now
guards three coding agents with a shell instead of one. It must keep passing
unchanged.

## Definition of done

1. A member with `code.delegate` asks Eve for a code change; she dispatches,
   answers immediately, and later reports a pull request link in her own voice.
2. All three agents — Claude Code, Codex, OpenCode — complete a session, and
   every one of them is provably served by LiteLLM. Codex and OpenCode add no
   metered spend.
3. Eve picks different agents and models for a one-line fix and a multi-file
   refactor, and an authored rule stating a preference changes what she picks.
4. A model registered in LiteLLM after this ships is usable with no change in
   this repository.
5. An `ocp/*` model is refused at dispatch, and a wire-incompatible pair fails
   at the first prompt rather than after thirty minutes of fluent nothing.
6. An agent asks a clarifying question mid-session; Eve answers it herself, out
   of household context, without involving the member.
7. A member says "tell it to use httpx instead"; that lands in the agent's next
   prompt and changes the outcome.
8. An agent asks something Eve genuinely cannot answer; the session parks, the
   member is asked, their answer resumes the same session.
9. Two sessions run in parallel on the same repository without colliding, and
   neither blocks a GUI task on `/tasks`.
10. A session spanning two repositories produces two pull requests on one
    branch name.
11. A wiped PVC recovers all three model-routing config files from
   `bootstrap.sh` with no human involved.
12. The `eve-computer` unreachability test still passes.

## Consequences for existing documents

- **New ADR 0016** — *the box runs the protocol, Eve holds the judgement*. The
  load-bearing split in this design: `idle` is classified in Eve's container,
  never on the box, which is what keeps "the box learns nothing about the
  family" true while still letting a real conversation happen.
- **README** — the delegated-coding capability, and the observation above about
  Eve opening pull requests against this repository.
- **`docs/architecture.md`** — the session lane beside the task lane, the ACP
  module map, and the supervisor loop in `eve-ambient`.
- **`family.yaml`** — `code.delegate`, granted to Noah.
- **`src/eve/models.py`** — unchanged, deliberately. It keeps owning Eve's five
  tiers; delegated coding models come from LiteLLM's catalogue at runtime, and
  the spec argues why a second static table there would be the worse outcome.
- **ADR 0004** — an amendment noting that the coding harnesses are a new and
  much heavier consumer of the ChatGPT subscription's rate limits, alongside
  the `-codex` model-name refusal that `codex-acp` has to route around.
- **The `release-eve` skill** — no new image, but `eve-computer`'s image grows
  four binaries.

## What this deliberately does not do

- **No mid-turn interrupt.** Interjections queue to the end of the current
  turn. The flag exists in shape, not in code.
- **No per-action approval gate.** `session/request_permission` is
  auto-approved, consistent with `eve-computer`'s existing posture and ADR
  0010's argument: the pod, the NetworkPolicy, the account isolation, and the
  spend cap are what make a wrong outcome survivable. A gate that depends on a
  human reading carefully at 11pm is not a boundary. The pull request is.
- **No merging.** Eve opens pull requests. A human merges them, forever.
- **No repo-less scratch sessions.** Every session gets at least one repo and
  at least one worktree. Making `repos` optional later is one branch.
- **No routing machinery.** Eve picks agent and model inline, from the goal and
  from memory. There is no scoring function, no benchmark table, no separate
  "which harness for this task" model call, and no learned router. If it turns
  out she chooses badly, the eval harness is where that evidence comes from —
  and the fix is a rule she can be told, not a component to build.
- **No agent × model compatibility matrix.** Bad pairs fail fast and are
  remembered. See above for why declaring one would repeat ADR 0004's mistake.
