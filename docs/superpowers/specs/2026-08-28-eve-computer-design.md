# Eve's computer — design

**Issue:** [EVE-1 Computer use](https://linear.app/chalifour-development/issue/EVE-1/computer-use)
**Status:** Design approved, not yet implemented
**Date:** 2026-08-28

## What this is

Eve gets a computer. One long-lived Linux machine in the home lab cluster with
a persistent home directory, a graphical desktop, unrestricted access to the
public internet, a shell she can install packages with, and her own accounts on
the services she needs. A family member asks her for something that needs a
computer; she dispatches the task, answers "I'm on it," and reports back in her
own voice when it's done.

This is the fifth deploy from this repository — `eve-computer`, beside `eve`,
`eve-tools`, `eve-ambient`, and `eve-sandbox`.

## Why this is not `eve-sandbox` grown up

`eve-sandbox` exists to hold nothing. ADR 0010 makes the pod the security
boundary precisely by removing network, filesystem, environment, and
credentials from it, and every guarantee in that document is written to survive
the AST checker being defeated. A machine that persists files, browses the web,
and holds login sessions is the exact inverse of those invariants. One service
satisfying both satisfies neither — the same reasoning ADR 0006 used to
separate `eve-tools` from Eve's main container, applied once more.

**`eve-sandbox` is unchanged by this work.** It keeps running machine-authored
pure functions with no network and no credentials, forever. `eve-computer` is a
new thing beside it, with the opposite contract and a different reason to be
safe.

## The boundary this revises

The README lists four permanent boundaries. This design moves one of them and
grazes another, and both deserve to be argued rather than assumed.

**"Eve does not author credentialed capability."** EVE-1 is credentialed
capability: Eve takes real actions in the world, with real logins, unattended.
The revision is narrower than it first appears, and rests on three properties:

1. **The credentials are hers, not yours.** Eve gets her own Google account,
   her own GitHub, her own everything. You grant her access to your material by
   sharing it to her — a calendar, a Drive folder — exactly as you would onboard
   a human assistant. Revocation is a checkbox in your account, not a code
   change in this repository.
2. **A human provisions every one of them.** Eve cannot create an account or
   obtain a credential. You log her in once, by hand, over VNC.
3. **The blast radius is recoverable.** A maximally bad day costs Eve's own
   accounts and some compute. It cannot cost the family roster, anyone's
   permissions, the Home Assistant credential, the Monarch credential, anyone's
   Gmail token, the database, or the cluster — because the machine cannot reach
   any of them.

This keeps the *shape* of the original boundary: Eve still does not author
credentialed capability, because she authors nothing here. A human granted a
bounded identity to a bounded machine. What changed is that "actions Eve can
take" is no longer categorically off the table; it is on the table exactly to
the extent that a wrong outcome is recoverable.

**"Eve does not learn unsupervised."** The worker maintains an `AGENTS.md` on
its own disk — how the machine is set up, where things live, what worked last
time. That is unsupervised learning, and calling it anything else would be
dishonest. It is bounded to the operation of her own machine and has no route
into her persona, her authored rules, or her behaviour toward any family
member: those still come from a specific turn with a specific member, and
Phase 5a's revocation CLI still owns them.

## What the machine is

### Image

`Dockerfile.eve-computer`, built and released with the other four images.

Debian slim, plus `Xvfb`, a lightweight window manager, `x11vnc`, Chromium, and
fonts; plus the working set — Python and `uv`, `git`, `curl`, `ripgrep`,
`ffmpeg`, `jq`; plus Codex CLI (see "Codex is a program, not a harness"); plus
the harness service.

Runs as user `eve`, uid 10004, **with passwordless sudo**. This is deliberate.
Root inside a pod with all capabilities dropped, `hostUsers: false`, no host
mounts, no ServiceAccount token, and `seccompProfile: RuntimeDefault` is not
root on the node — and a computer she cannot install a package on is not a
computer. The pod spec, not the user account, is the thing doing the
containing.

### Storage

One PVC, ~50 GiB, mounted at `/home/eve`. Everything outside it is ephemeral.

That creates the one real UX defect in this design: she `apt install`s a tool,
the pod reschedules, the tool is gone. The fix is a `bootstrap.sh` that runs on
container start and replays `/home/eve/.eve/packages.txt`, a file the worker
maintains itself. Self-healing across restarts, no image rebuild, no pull
request. Packages that prove durable get promoted into the image by a human,
eventually.

### Network

This section is the load-bearing one. Every safety claim in this document
reduces to it.

**Egress:** the public internet, and nothing on the inside.

- Allowed: DNS; `0.0.0.0/0` on 80/443.
- **Denied: all RFC1918 ranges, the cluster pod and service CIDRs, and
  169.254.169.254.**

She reaches `litellm.chalifour.dev` and `langfuse.chalifour.dev` the same way
any internet host does — both are public names, which is what makes this
possible without punching a hole inward. She cannot reach Postgres,
`eve-tools`, `eve-sandbox`, Eve's own API, or the Kubernetes API server.

**Ingress:** the harness port (8092) from the `eve` and `eve-ambient` pods
only, and the VNC port from nothing (reached by `kubectl port-forward`, which
does not traverse the NetworkPolicy).

`automountServiceAccountToken: false`. No secret mounts beyond the two below.

### Identity

Her accounts are logged in **once, by a human, over VNC**, into a browser
profile that lives on the PVC. Those session cookies are then the only form her
third-party credentials ever take: never a Kubernetes Secret, never an
environment variable, never a line in this repository, never anything Eve's
main container can read.

The pod holds exactly two secrets:

- `EVE_COMPUTER_API_KEY` — the bearer token Eve authenticates to the harness
  with.
- A **dedicated LiteLLM virtual key with its own spend cap**, distinct from the
  key Eve's main container uses. The cap is the hard ceiling on what a runaway
  task can cost, and it is enforced by LiteLLM, not by anything running on the
  machine.

## The harness

### v1: Claude, and a seam

The loop is driven by `claude-agent-sdk`, which supplies bash, read, write, and
edit tools directly. The GUI tool is Anthropic's reference computer-use
implementation (`xdotool` plus screenshots against `:99`), lifted rather than
rewritten.

This choice is forced by the platform, not by preference. OpenAI's computer use
was evaluated and does not run here:

| Option | Verdict |
|---|---|
| Codex app "background computer use" | **macOS only.** Windows drives the foreground desktop; Linux unsupported. Community Linux ports attach screenshot context only and grant no control. |
| Codex CLI | Runs on Linux, subscription-funded, but ships with no GUI, no mouse, no computer use. It is a coding agent. |
| `computer-use-preview` (Responses API) | Would run on Linux, but is access-gated behind a metered tier-3 OpenAI key. This lab has no metered OpenAI key — ADR 0004 established that the OpenAI credential here is a ChatGPT/Codex sign-in serving a restricted model set. |

Meanwhile `anthropic/claude-sonnet-5` is already wired into LiteLLM as a
metered key (ADR 0004, EVE-2 amendment, 2026-08-28), and published OSWorld
comparisons point the same direction on GUI control.

**The swap seam is the task API itself.** Whatever drives the loop lives behind
`POST /tasks` on the box; Eve's side never learns which. Internally that is one
module, `harness.py`, exposing `run_task(task) -> Result`, selected by an env
var. Changing drivers later is one new file and a config value. No plugin
registry, no abstract base class with a single implementation.

### Codex is a program, not a harness

The box has a shell, so `codex exec "..."` is simply a command the worker can
run. Codex CLI installs on Linux and signs in with the ChatGPT plan through the
same VNC browser flow as her other accounts. Coding work therefore rides the
subscription at zero metered spend and zero integration cost, while the GUI
half uses the only driver that runs on Linux.

### Persona

The worker is **not Eve**. It runs a plain operator prompt. Eve remains the
family's one voice and narrates results in her own words, exactly as
specialists work out of view today.

### Memory

`/home/eve/AGENTS.md`, which the worker may edit, accumulating on the PVC across
months. No schema, no table, no migration. Per-task scratch space lives at
`/home/eve/tasks/<id>/`, with outputs under `out/`.

### Bounds

Per-task maximum turns and wall-clock timeout; the LiteLLM key's own budget as
the ceiling on spend.

## Dispatch

Eve gets one tool: `dispatch_computer_task(goal) -> task_id`. She returns
immediately — *"I'm on it, I'll let you know."* The task row lives in **Eve's**
Postgres: id, member subject, thread id, goal, status, result, timestamps.

`tools_client` gains a third door, `http://eve-computer:8092`, in the same
bearer-token shape as the existing two.

- `POST /tasks` `{id, goal}` → `202`
- `GET /tasks/{id}` → `{status, result, artifacts}`
- `GET /tasks/{id}/artifacts/{name}` → file bytes
- `DELETE /tasks/{id}` → kills the run

**Eve polls the box; the box never calls Eve.** This keeps the network boundary
one-directional: a compromised machine has no inbound path to anything. The
poller is a tick inside `eve-ambient`, which already owns polling, a store, and
a schedule.

**The box learns nothing about the family.** Only the goal text and a task id
cross the boundary — no member subject, no names, no roster, no permissions.
Where a name genuinely matters to the task, Eve places it in the goal text
deliberately. This is stricter than the `eve-tools` boundary, which does carry
`member_sub` for per-member Gmail tokens; `eve-computer` needs no such
exception because it holds no per-member credential.

Permission checks happen in Eve's container before the HTTP call, per ADR 0006.
A new `computer.use` permission gates dispatch.

**One task at a time, queued.** One machine has one X display and one mouse;
concurrent GUI tasks would fight over the same cursor. Serializing is both
correct and simpler than partitioning GUI work from headless work.

## Reporting back

A new ambient source, `src/eve_ambient/sources/computer.py`, polls for tasks
that finished since the last tick. Everything downstream is existing machinery:
gate on permissions, compose a turn as Eve on the originating thread, push via
ntfy.

Two deliberate deviations from how other ambient sources behave:

- **The relevance filter is bypassed for this source.** Every other signal is a
  guess about what the family might want to know; this one was explicitly
  requested by a member. An LLM deciding the answer to a direct request is "not
  relevant" and swallowing it is the worst available failure mode.
- **`gates.py` must gain an explicit permission mapping for `computer`.** It
  fails closed on unmapped sources — correct behaviour that will silently
  notify nobody if this step is forgotten.

Artifacts are referenced in the result payload and fetched on request through
the authenticated door. Tasks whose machine restarted mid-run are marked stale
by the poller after a timeout rather than hanging in `running` forever.

## Oversight

**Live:** `x11vnc` reached by `kubectl port-forward`. No ingress, no auth layer
to build — cluster access is already the credential. You watch her actual
screen and can take the mouse mid-task. Zero code.

**Kill switches:** `DELETE /tasks/{id}` for one run; `kubectl scale
deploy/eve-computer --replicas=0` for everything. Her disk survives both.

**After the fact:** per-task turns and screenshots under
`/home/eve/tasks/<id>/`, and Langfuse traces over the public hostname, so a
computer task appears in the same tracing UI as every other Eve turn.

## Testing

Unit tier (`not integration and not live and not docker`) covers the task store,
the poller state machine, the ambient source, and the permission gate, with the
box mocked.

Integration runs the real image under docker-compose against a headless task.
A `live`-marked test drives the actual desktop.

**The test that matters most** is written in ADR 0010's spirit: assert from
inside the running box that Postgres, `eve-tools`, `eve-sandbox`, Eve's API, and
the Kubernetes API server are all unreachable. Every claim in this document
rests on that NetworkPolicy, so it gets a test that fails loudly the day
someone loosens it.

## Definition of done

1. `eve-computer` runs in the cluster with a persistent home, a desktop, and
   internet access; her accounts are logged in and survive a pod restart.
2. A family member with `computer.use` asks Eve for something requiring a
   computer; Eve dispatches, answers immediately, and pushes a result in her own
   voice when the work finishes.
3. A package she installs survives a pod reschedule via `packages.txt`.
4. `kubectl port-forward` to VNC shows her working live, and the mouse can be
   taken from her.
5. The unreachability test passes, and fails when the NetworkPolicy is relaxed.
6. `eve-sandbox` is byte-identical to before this work.

## Consequences for existing documents

- **ADR 0012** records the boundary revision argued above.
- **README** amends the "Where the program ends" section: the second boundary
  now reads as a distinction between authored capability and granted identity,
  and the five-phase program gains a fifth deploy.
- **The `release-eve` skill** gains a fifth image; all five continue to move to
  the same version together.
- **`docs/architecture.md`** gains the service and its network boundary.

## What this deliberately does not do

- **No per-action approval gate.** Dispatching the task is the gate. The pod,
  the NetworkPolicy, the account isolation, and the spend cap are what make a
  wrong outcome survivable — the same argument ADR 0010 makes one level down,
  and for the same reason: a gate that depends on a human reading carefully at
  11pm is not a boundary.
- **No per-member machines.** One machine, shared. Member-scoped content passes
  through as goal text and task output; it is not a filesystem tenancy model,
  and this design does not pretend otherwise.
- **No second harness in v1.** The seam exists; a second driver does not.
