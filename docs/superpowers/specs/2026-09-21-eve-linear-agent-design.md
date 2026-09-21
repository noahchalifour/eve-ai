# Eve as a Linear agent: design

**Issue:** [EVE-26 Integrate with Linear AIG](https://linear.app/chalifour-development/issue/EVE-26/integrate-with-linear-aig)
**Status:** Design approved, not yet implemented
**Date:** 2026-09-21

## What this is

Eve becomes a real agent inside Linear. A person delegates an issue to her by
assigning it, or mentions her in a comment, and she picks up the work the same
way she does when Noah asks her in chat: a coding session on `eve-computer`,
supervised by the loop that already exists, ending in a pull request.

What is new is not the work. It is the surface. Linear watchers see a live
Agent Session: an acknowledgement within seconds, an activity per meaningful
step, a question when she is blocked, and a final response carrying the pull
request links. The session is a visible skin over machinery this repository
already has.

This is one repository's worth of change. There is no client work and no new
deployment.

## What this is, and what it is not

**A Linear agent session is a second reporting channel on a coding session.**
Linear's session model (created, then activities, then a terminal response)
maps almost line for line onto what `eve.coding.supervisor` already computes
every twenty seconds. The supervisor already decides `reply`, `escalate`, or
`done`. It gains one side effect: when the session it is driving carries a
Linear session id, each decision is also emitted to Linear as an activity.

The rejected alternative was **Eve as a conversational participant in Linear**:
she reads issues, answers questions, triages, comments, and never writes code.
That is a much smaller build and it fails the point of the request. The
motivating value is that Eve can be handed an issue and hand back a pull
request. A Linear agent that can only talk about the work is a worse version of
the chat surface that already exists, in a place with less context about the
household.

### Non-goals

- **No conversational sessions.** If an issue is delegated to Eve, she codes.
  She does not triage, estimate, or answer questions as a Linear-native
  chatbot. That is a separable feature and can be added later without
  redesigning this.
- **No Linear-side authoring.** Eve does not create issues, edit descriptions,
  move issues between projects, or comment outside her own agent sessions. The
  only issue mutation is the status and delegate change Linear's own best
  practices ask for on accepting a delegation.
- **No mentions outside issues.** Linear supports mentioning agents in
  documents, projects, and other editor surfaces. Only issue sessions are in
  scope.
- **No multi-workspace install.** One workspace, one OAuth install, one
  allowlist. Nothing in the data model forbids more later; nothing in this
  design supports it now.
- **No Linear-originated ambient chatter.** Eve does not poll Linear for
  interesting activity and speak up about it. She acts only when addressed.

## Architecture

```
Linear ──webhook──> eve-ambient  POST /signals/linear
                         │  verify MAC over raw body, check timestamp, 202
                         │  background task:
                         ├──> emit `thought` (the acknowledgement)
                         ├──> family.yaml: linear_id -> member
                         ├──> permissions: code.delegate
                         ├──> repos: guidance ∩ allowlist
                         └──> eve.coding.dispatch (existing) ──> eve-computer

eve.coding.supervisor (existing ~20s loop)
                         │  reply / escalate / done, unchanged
                         └──> eve.tools_client.invoke
                                  └──> eve-tools linear_client
                                           └──> agentActivityCreate
```

Three existing things are reused without modification to their decision
making: the ambient webhook posture (verify, 202, background task, in-flight
dedup, bounded concurrency, drain on shutdown), the coding dispatch path, and
the supervisor's classification of each agent turn.

### Why eve-ambient owns the webhook

`eve-ambient` is already the service that accepts an authenticated push from a
third party, answers immediately, and does the expensive work in a background
task. `/signals/home-assistant` is that pattern in the file the new endpoint
goes next to. It is also the service that hosts the coding supervisor loop,
which is the thing that will emit most of the activities. Putting the webhook
anywhere else would mean either a sixth deployment for one endpoint and one
GraphQL client, or teaching `eve-tools` (a request and response dispatcher with
no inbound webhook or background loop concept) how to run one.

A new `eve-linear` service was rejected on cost. It buys isolation this feature
does not need: the webhook handler holds no credential, and the credential it
would hold belongs in `eve-tools` regardless (see below).

### Why this is not an ambient source

Tempting, because the pipeline exists and already turns a push into a Signal.
It is wrong on the merits. The ambient gate chain exists to protect the family
from Eve speaking without being spoken to: quiet hours, a daily cap, a
relevance filter, a cooldown. Every one of those misfires on explicitly
requested work. Quiet hours would sit on a colleague's delegation until 7am.
The daily cap would silently drop the sixth request of the day. The relevance
filter would spend a model call deciding whether to do work someone already
asked for.

`sources/coding.py` already had to special-case its way around the relevance
filter for exactly this reason, and `sources/computer.py` before it. That is
two precedents saying requested work does not belong in a pipeline built for
unrequested interruptions. A third special case is a signal to stop.

The resolved-session half of the existing coding source stays exactly as it is.
A Linear-originated session still gets an Aegra thread, so the family still gets
the push notification and a thread to talk in. Linear watchers and the family
are two audiences for one piece of work, and both are served.

### Why the credential stays in eve-tools

[ADR 0006](../../adr/0006-eve-tools-isolation.md) says third-party credentials
live in exactly one service. The Linear OAuth token is a third-party
credential, so it lives in `eve-tools` behind a new `linear.*` handler family,
alongside `home.*`, `mail.*`, `finances.*`, and `health.*`.

`eve-ambient` holds only the webhook signing secret, which is not a credential
for reaching Linear. It is a key for verifying that Linear reached us. That is
the same thing it already holds for Home Assistant, and it grants no authority
over the Linear workspace at all.

This is the first time a third-party service initiates contact with Eve rather
than being polled, and it is worth writing down why ADR 0006 survives it:
verification and action are separable. Only the action needs the credential.
The internal hop costs a few milliseconds against a ten second budget, which
is not a trade worth breaking an invariant for.

## The two deadlines

Linear imposes two timings, and they are the hardest constraint in this design:

1. **Five seconds** to return an HTTP response from the webhook receiver.
2. **Ten seconds** from a `created` event to the first emitted activity, or the
   session is shown to users as unresponsive.

A dispatch does a hybrid memory recall and an HTTP call to `eve-computer`.
Neither can be on either path.

**The handler** verifies, then returns 202 and hands off to a background task.
Verification is local computation: an HMAC over bytes already in hand and a
comparison of two integers. Nothing in the 5 second path touches the database,
a model, or the network.

**The background task** emits the `thought` acknowledgement as its very first
act, before the family lookup, before recall, before dispatch. Every refusal
path (unmapped user, missing permission, unresolvable repos) replaces that
thought with an `error` or `elicitation`, and each is a single GraphQL call.
The ordering is not an optimization; it is the contract, and section "Testing"
makes it a test rather than a comment.

`linear_enabled` is checked after the signature and payload shape are known
good and before any spend, in the same position and for the same reason
`ambient_enabled` is checked in the Home Assistant handler, and answers 503
when off. An operator needs one lever that stops Eve acting on Linear without
taking down the webhook.

## Identity and authority

`family.yaml` gains an optional `linear_id` per member, documented the way
`sub` is, including how to find the value. Resolution is a dict lookup built
once from the already-parsed family file. No IO, so it is safe on the
acknowledgement path.

Authority comes from the delegating human, never from the workspace. A Linear
session runs as a family member, with that member's permissions, that member's
remembered preferences, and that member's name on the row. This keeps exactly
one authorization model in the deployment: `family.yaml` is the audit log, and
a pull request is how it changes.

Three refusals, all before anything is spent:

| Condition | Activity | Session outcome |
|---|---|---|
| Linear user has no `linear_id` mapping | `error` | none created |
| Mapped member lacks `code.delegate` | `error`, carrying `permission_denial`'s string | none created |
| No repo resolvable inside the allowlist | `elicitation` asking which repo | parked, awaiting input |

The first two are `error` rather than `elicitation` because no answer typed
into Linear can fix them. Granting a permission is a pull request against
`family.yaml`, by design. The third is an `elicitation` because a human naming
a repo in the thread genuinely does resolve it.

A single fixed operator (every Linear session runs as Noah) was rejected:
anyone who can mention Eve in the workspace would then command Noah's
credentials and his memory, and the audit trail would say Noah did it.

## What Eve is allowed to touch

A coding session needs repos. For a chat-dispatched session the member names
them. A Linear issue has no such field, and the issue body is written by
anyone who can file an issue.

Repos come from Linear's `guidance` field, which the platform provides for
exactly this ("preferred repositories or task constraints", configurable at
workspace, parent team, or team level), intersected with
`EVE_LINEAR_REPO_ALLOWLIST`. Eve never goes outside the allowlist. If guidance
names nothing in it, that is the `elicitation` above rather than a guess.

**The allowlist is the containment boundary for prompt injection**, and this is
the sentence to remember from this document. Issue text reaches a coding agent
that can write code and open pull requests. Nothing in that text can widen
Eve's reach, because the allowlist is configuration rather than text and is
read from settings rather than from anything Linear sent. Inferring repos from
the issue body was rejected outright for this reason: it makes the repo list
attacker-controllable by anyone with a Linear seat.

The issue body is still untrusted input that reaches a model. That is
irreducible for this feature (it is the request), and it is contained the same
way the ambient pipeline contains a signal payload: what the text can influence
is what Eve does inside an allowed repo, not which repos exist and not which
permissions apply.

## Data model

Two nullable columns on `eve_coding_session`, one Alembic migration, no new
table:

| Column | Type | Why |
|---|---|---|
| `linear_session_id` | `text unique` | The idempotency key. |
| `linear_issue_id` | `text` | For the status and delegate mutations, and for a human reading the row. |

Nullable because chat-dispatched sessions have no Linear side. `NOT NULL` on
`thread_id` is untouched: a Linear session gets an Aegra thread too, so the
resolution path, the ambient coding source, and `check_coding_session` all work
on these rows with no change whatsoever.

**Why `unique` rather than an index.** Linear retries on a 5xx and on a
timeout. A retried `created` that dispatches a second coding session spends
real money and opens a second pull request for one request. The constraint
makes "at most one coding session per agent session" an invariant the database
enforces, rather than a race the handler hopes to win. The insert conflict is
the dedup, and it is correct even across a restart, which an in-memory
`_in_flight` set is not.

### Why not a separate `eve_linear_session` table

It is the more general design and it is not yet earned. The relationship is
strictly one to one: a Linear agent session drives exactly one coding session,
and a coding session has at most one Linear session. A separate table buys a
join on that relationship, a second migration, and a second place to look when
a session misbehaves, in exchange for a generalization that pays off only on
the day a second ticketing integration exists.

Two nullable columns are cheap to lift into a table on that day. A table is not
cheap to collapse back. If a second integration arrives, the refactor is
mechanical and the constraint above is the thing that moves.

## Lifecycle mapping

Linear tracks session state automatically from the last emitted activity, so
there is no state to manage. What follows is what Eve emits, not what she sets.

| Eve state | Activity | Resulting Linear state |
|---|---|---|
| Accepted, before dispatch | `thought` | active |
| Supervisor `reply` | `action` with what it did | active |
| Supervisor `escalate` | `elicitation` with the question | awaitingInput |
| `finished` | `response` with the pull request links | complete |
| `failed`, `stale` | `error` | error |

The fidelity is deliberately milestone-level. Mirroring every agent turn would
be a GraphQL write on a twenty second loop and would bury the readable events
in noise, against the AIG's guidance that feedback be immediate and
unobtrusive. Acknowledging and then going silent until the end was rejected
because Linear marks a session stale after 30 minutes, and because a long
silence reads to a human as a hung agent.

### The return path closes itself

Linear's `prompted` webhook (a user sending a message into an existing session,
including answering an elicitation) maps onto `prompt_coding_session`, which is
what `escalate` parks the session for. `escalate` deliberately does not resolve
the session: the subprocess and the worktrees stay up precisely so an answer
can resume it. A human answering in Linear resumes that same session with its
state intact.

On a `prompted` for a `blocked` row, the status returns to `running` and the
supervisor picks it up on the next tick. On a `prompted` for a row that is
already terminal, Eve emits an `error` saying the session has finished, rather
than silently dropping it or starting a new one that the human did not ask for.

### The heartbeat

A coding agent can work for longer than 30 minutes without producing a
supervisor decision, and Linear marks a silent session stale. If nothing has
been emitted for `EVE_LINEAR_HEARTBEAT_MINUTES` (default 10), the supervisor
emits a `thought` carrying the box's latest activity line, which it already
fetches for `check_coding_session`.

The state is recoverable in Linear (a later activity un-stales the session), so
this is a quality concern rather than a correctness one. It is in scope because
a stale-looking session invites a human to intervene in work that is going
fine.

### Issue status and delegate

On accepting a delegation, Eve moves the issue to the team's lowest-position
`started` workflow state if it is not already in a `started`, `completed`, or
`canceled` type, and sets herself as delegate if no delegate is set. Both are
what Linear's best practices ask for, and both are one mutation each.

Per those same practices, if an automation rather than a human delegated the
issue, it stays in triage and assignment is left to a human.

## Failure modes

**Emission failure must never fail the session.** Every activity call degrades
to a logged warning, the same posture `tools_client.invoke` already takes.
Losing the Linear narration is bad. Killing a running coding session because a
GraphQL call timed out is worse, and it destroys work the member is waiting on.

**Replay.** The `webhookTimestamp` freshness window (60 seconds) and the unique
constraint together. Neither alone is enough: the window stops a replayed
capture, and the constraint stops a legitimate Linear retry from
double-dispatching.

**Signature verification.** Hex-decoded HMAC-SHA256 over the **raw** request
body, compared with `compare_digest` on bytes. Raw body because re-serializing
parsed JSON changes the bytes and therefore the MAC. Bytes rather than `str`
for the same reason `eve.auth._ambient_subject` documents: `compare_digest`
raises `TypeError` on a `str` operand containing non-ASCII, which a hostile or
merely malformed header can trigger, and an exception here is a 500 that tells
Linear to retry a request that should have been refused.

**Unbounded concurrency.** A semaphore bounding concurrent Linear-originated
dispatches, in the shape `_webhook_semaphore` already establishes, plus a cap
on live Linear-originated sessions (`EVE_LINEAR_MAX_LIVE_SESSIONS`). Five
people delegating at once should queue, not fan out to five coding agents on
one box. Over the cap, Eve emits a `thought` saying she is queued rather than
failing, because the work is still going to happen.

**A dispatch that fails after the acknowledgement.** The thought is already
visible, so silence would be the worst outcome. The failure becomes an `error`
activity carrying the reason, and no row is written, so nothing is left for the
supervisor to poll forever. This is the existing ordering rule from
`dispatch_coding_session`: the row is written only after the box accepts.

## Settings

All new settings are on `eve.settings.Settings`, following the existing naming
and the existing validation posture.

| Setting | Default | Note |
|---|---|---|
| `linear_enabled` | `false` | Off by default, like `ambient_enabled`: this subsystem acts on input from outside the household. |
| `linear_webhook_secret` | `""` | Held by `eve-ambient`. Required when enabled. |
| `linear_api_token` | `""` | Held by `eve-tools` only. The `actor=app` OAuth token. |
| `linear_repo_allowlist` | `[]` | The containment boundary. Enabled with an empty allowlist is a startup error. |
| `linear_heartbeat_minutes` | `10` | Against Linear's 30 minute stale threshold. |
| `linear_max_live_sessions` | `3` | Concurrent Linear-originated coding sessions. |

`model_post_init` refuses to start on `linear_enabled` without the webhook
secret, without the API token, or with an empty allowlist. This follows the
precedent set by `ambient_enabled` requiring `ambient_token`: an
enabled-but-unconfigured deployment would accept webhooks, spend a dispatch,
and fail every emission on a 401 while Linear retries, which is the least
diagnosable failure this subsystem can have.

The token is a plain setting rather than a row in `eve_tools.oauth_store`
because an `actor=app` install is a one-time workspace grant with no refresh
cycle, and the store exists to manage per-member refreshable grants. If Linear
later requires rotation, the store is the right home and the move is local to
`linear_client.py`.

## Testing

**Unit.**

- Signature verification: a good MAC, a bad MAC, a missing header, a non-ASCII
  header (the `TypeError` case), and a body that was re-serialized rather than
  taken raw.
- Timestamp window: inside, outside, missing, and far-future.
- Identity: mapped member, unmapped Linear user, mapped member without
  `code.delegate`.
- Repo resolution: guidance inside the allowlist, guidance outside it, guidance
  naming nothing, empty guidance.
- The decision-to-activity mapping table above, one case per row.
- Heartbeat timing: emitted after the threshold, not emitted before it, reset
  by any other emission.
- `prompted` against a blocked row (resumes), against a terminal row (errors),
  and against an unknown session id.

**Integration**, with `eve-computer` and Linear both faked.

- The full `created` path: 202 returned, thought emitted, session dispatched,
  row written, all in order.
- A duplicate `created` (same `linear_session_id`) dispatches exactly once.
- An elicitation answered by a `prompted` resumes the same session id.
- An emission failure mid-session leaves the coding session running.

**The canary.** Assert that the acknowledgement is emitted *before* the recall
call, not merely that both eventually happen. That ordering is the entire ten
second contract, it is invisible in the code once written, and it is exactly
what a well-meaning refactor that hoists the family lookup will silently break.

Live tests against a real Linear workspace are out of scope for CI, consistent
with how every other third-party client in this repository is tested.

## Definition of done

1. `POST /signals/linear` on `eve-ambient` verifies, dedups, answers 202, and
   dispatches in the background.
2. A delegated Linear issue produces a coding session, with a `thought` visible
   in Linear within ten seconds.
3. The supervisor emits `action`, `elicitation`, `response`, and `error`
   activities at the transitions in the mapping table.
4. A human answering an elicitation in Linear resumes the parked session.
5. The three refusals produce their activities and spend no model call.
6. A retried `created` dispatches once.
7. The family still receives the existing push notification and thread when the
   session resolves.
8. `linear_enabled=false` serves 503 and does nothing else.
9. Unit and integration tests above pass, including the ordering canary.
10. `docs/architecture.md` gains a "Linear" section; this document and the
    implementation plan are linked from it.

## Consequences for existing documents

- **`docs/architecture.md`**: a new "Linear" section after "Ambient",
  describing the endpoint, the mapping, and the allowlist boundary. The
  "Ambient" section gains a sentence noting that `/signals/linear` shares the
  webhook posture but deliberately bypasses the gate chain, with the reason.
- **ADR 0006 (`eve-tools` isolation)**: unchanged in substance, but this is its
  first inbound-initiated third party. A short amendment noting that
  verification does not require the credential, and therefore may live outside
  `eve-tools`, prevents the next reader from concluding the rule was broken.
- **`family.yaml`**: gains the optional `linear_id` key with a comment
  explaining how to obtain it and that an unmapped Linear user is refused.
- **No ADR of its own.** Nothing here reverses or amends an accepted decision.
  The two shape choices worth recording (columns rather than a table, the
  bypassed gate chain) are argued in this document, which is where the existing
  specs put reasoning of that weight.

## What this deliberately does not do

- **No stop or cancel handling.** An abandoned session keeps burning tokens
  until it resolves on its own. Linear's Inbox Notifications category carries
  `issueUnassignedFromYou`, which is the natural trigger for killing a session,
  and `kill_coding_session` already exists. It needs a second webhook category
  enabled and its own refusal semantics, so it is a real increment rather than
  a line of code, and it is the first thing to build after this ships.
- **No reaction or comment reading.** Comments are editable and therefore
  unreliable as input, which is the reason Linear recommends reading Agent
  Activities instead. Eve reads `promptContext` and activities, never the
  comment thread directly.
- **No cost attribution back to Linear.** A delegated session spends against
  the same budget as a chat-dispatched one, and nothing reports per-issue cost.
- **No per-team configuration.** One allowlist, one set of bounds, for the
  whole workspace.
