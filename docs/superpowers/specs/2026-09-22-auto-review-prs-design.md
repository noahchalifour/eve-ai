# Auto-review pull requests: design

**Issue:** [EVE-27 Auto review PRs](https://linear.app/chalifour-development/issue/EVE-27/auto-review-prs)
**Status:** Design approved, not yet implemented
**Date:** 2026-09-22

## What this is

Eve reviews pull requests on GitHub, including the ones she opened herself.

A PR is labelled or assigned on GitHub. GitHub posts a webhook to `eve-ambient`.
Eve starts an ACP session on `eve-computer` with a reviewer agent deliberately
different from whatever wrote the code, in a real checkout of the PR head. The
agent applies a vendored review rubric, scoped to the repository's stack, and
writes its findings to a file. The box reads that file and posts the review with
`gh` as a `COMMENT`, never an approval and never a block. Eve tells the member
what was found, with a link, and stops.

**A review is a coding session with a different beginning and a different
ending.** The middle, which is the hard part, is untouched. `src/eve/coding/`
and `src/eve_computer/acp/` already clone repositories, manage worktrees, map
four harnesses and any LiteLLM model to a subprocess, hold a multi-turn
conversation, classify an idle turn, and carry a result back to the family
through the ambient gate chain. This feature adds a trigger at one end and a
different ending at the other.

### What is genuinely new

Three things, and no new deployment:

1. A webhook route on `eve-ambient` that GitHub can reach, which means an
   ingress that did not previously need to exist.
2. A review mode on an ACP session: check out a PR head rather than branch from
   the default, and post a review rather than open a pull request.
3. A findings file, which is the contract between the agent's judgement and the
   box's determinism.

### Non-goals

- **No auto-fixing.** An agent reviewing an agent's work and then commissioning
  a third agent to fix it can churn without a human ever looking at it.
- **No merge authority.** The review verb is a hardcoded literal, not a setting.
  See "Posting to GitHub".
- **No re-review when new commits land.** Stated as a non-goal rather than an
  oversight: it needs a debounce and a per-PR cap, and it is a cheap follow-up
  once real reviews exist. Tracked separately as
  [EVE-32](https://linear.app/chalifour-development/issue/EVE-32/re-review-prs-when-new-commits-land).
- **No review of repositories Eve was not configured to watch.** The webhook can
  carry any repository; an allowlist decides which ones spend tokens.
- **No authoring of rules or preferences from a review.** A review is not a
  member speaking. This is the property `ambient_marker` already protects for
  ambient and routine turns.

## The rubric

`prompts/code-review/` gains three files vendored from
[`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills) (MIT,
attributed in a `SOURCE.md` beside them):

| File | What it carries |
|---|---|
| `SKILL.md` | The five review axes, severity vocabulary, change sizing, structural remedies |
| `security-checklist.md` | OWASP Top 10, OWASP Top 10 for LLMs, input validation, dependency and supply-chain review |
| `performance-checklist.md` | Database query plans, index strategy, connection pooling, API and frontend checks |

They sit beside `prompts/eve.md` and `prompts/stylist.md`, which is where this
repository already keeps the text that determines model behaviour.
`Dockerfile.eve-computer` gains a `COPY prompts/code-review ./prompts/code-review`,
since it currently copies only `src/eve_computer`.

### Why vendored rather than installed

The rubric is the review's specification. If it can change without a commit,
two reviews of the same pull request can differ for reasons absent from the git
history, and a model regression becomes indistinguishable from an upstream
rubric edit. Installing it at image build time (`npx skills add`) stays current
automatically, which is the wrong tradeoff for a document whose stability is the
point. Updating the rubric is a visible pull request, which is also the only
honest way to review a change to how Eve reviews.

### Why the security checklist specifically

It carries an OWASP Top 10 for LLMs table, and `eve-ai` is an LLM application.
Two of its entries are things this repository already spends real design effort
on: LLM06 Excessive Agency is what `eve.specialists.permissions.permission_denial`
exists to enforce, and LLM10 Unbounded Consumption is what
`coding_max_supervisor_turns` and `session_max_turns` exist to bound. A reviewer
carrying that table catches a new tool shipped without a permission check, which
is the most likely real defect in this codebase.

### Stack scoping is a rule, not a hope

The performance checklist is substantially web-oriented: Core Web Vitals, bundle
size, images, CSS, fonts. Against a Python service those sections produce noise,
and noise is what trains a member to stop reading Eve's reviews. Its backend half
applies fully and well: N+1 patterns, `EXPLAIN ANALYZE` baselines, composite
index column order, and a connection-pooling section that speaks directly to
`eve.memory.db.get_pool`. The frontend half is not wasted either, because Eve
opens pull requests against `open-assistant`.

So the session prompt instructs the reviewer to apply only the sections matching
the repository's stack and to **name the ones it skipped**, which is recorded in
the findings file. The omission is auditable rather than silent.

## Architecture

```
GitHub  ── pull_request labeled/assigned ──▶  eve-ambient
                                                  POST /signals/github
                                                    verify HMAC on raw body
                                                    resolve labeller -> member
                                                    Signal(source="review")
                                                         │
                                              pipeline.handle_signal
                                                (filter bypassed, as coding)
                                                         │
                                              eve.review.dispatch
                                                    │
                                                    ▼
                                            eve-computer  POST /sessions
                                              repo.add_review_worktree
                                              ACP session (reviewer agent)
                                                    │  writes review.json
                                              repo.post_review  ── gh api ──▶ GitHub
                                                    │
eve.coding.supervisor (20s tick) ◀──────────────────┘
        │  done
        ▼
eve_ambient/sources/review.py  ──▶ Signal ──▶ notification + thread
```

Everything downstream of the emitted `Signal` already exists and is already
tested: dedup and cooldown (`eve_ambient.store.is_fresh`, `already_notified`),
the permission gate (`gates.permitted`), thread creation and the headless run
(`notify.deliver`), the `NOTHING` veto, the ntfy push, and the
`eve_ambient_notice` row.

## The trigger

### Two entry points, one path

A review starts one of two ways. A human labels a pull request `eve-review` or
assigns Eve, and GitHub posts a `pull_request` webhook. Or Eve's own session
opens a pull request and `repo.publish` labels it during publication, so the
same webhook fires.

The second is deliberately not a special case in the code. Eve labels her own
pull request and hears about it exactly the way she hears about anyone else's,
so there is one trigger path to test rather than two.

### The route

`eve-ambient` gains `POST /signals/github`, beside the existing
`/signals/home-assistant`. It reuses that route's proven shape: a 401 that logs
the rejection without logging the presented secret, a 503 when the feature is
disabled so the expensive path cannot run, and a 202 with background handling
because a review takes far longer than GitHub will hold a connection open.

Three differences, each forced by GitHub rather than chosen:

**The signature is an HMAC, not a shared secret.** GitHub sends
`X-Hub-Signature-256`, which is `hmac_sha256(secret, raw_body)`. The handler
verifies against the **raw request body, before parsing**. `await request.json()`
is safe after verification and never before: a body that reparses differently
than it hashed is the classic bypass. The comparison still uses `compare_digest`
on `.encode()`d bytes, for the reason `eve.auth._ambient_subject` documents.

**Only two events matter.** `pull_request` with action `labeled` or `assigned`.
Every other action and every other event type is acknowledged with 202 and
dropped. GitHub sends a `ping` on hook creation, and rejecting it makes the hook
look broken in the GitHub UI.

**The dedup key is `(repo, pr_number, head_sha)`**, not the delivery id. A
redelivered webhook, a label removed and reapplied, and two people labelling at
once all describe the same review of the same code, and `_in_flight` plus
`eve_ambient_seen` should collapse them. Including `head_sha` means a pull
request that gets new commits and is relabelled is genuinely a different review,
which is the one case where a second run is correct.

### The ingress, and its cost

Every other ambient source polls outward. Home Assistant posts inward, but from
inside the house. This is the first path where the public internet reaches a
cluster service on a schedule Eve does not control, and it needs an Ingress with
TLS, the route reachable from GitHub's hook IP ranges, and a second webhook
secret in the deployment.

The mitigations are mostly already in the file's shape: the HMAC is verified
before any parsing, `ambient_enabled` and `review_enabled` both gate the
expensive path, and `_MAX_CONCURRENT_WEBHOOK_SIGNALS` already bounds concurrent
compose turns. One is added: a review session is bounded by its own concurrency
cap, separate from the webhook semaphore, because a review costs a full ACP
session rather than one model call.

This ingress is the reason `review_enabled` defaults to off.

### Authorisation is not the webhook's job

A valid signature proves GitHub sent the event. It does not prove the labeller
was allowed to spend Eve's tokens.

So the handler resolves the labelling GitHub login to a family member through a
new `github_login` field in `family.yaml`, and the signal carries that member's
sub through `gates.permitted` against a new `code.review` permission. An unknown
login gets no review.

Without this, anyone who can label a pull request in a watched repository can
commission an LLM session, which is LLM06 Excessive Agency in the very checklist
Eve is about to review other people's code with.

### Why a Signal at all

Rather than the webhook calling the box directly: the signal carries the review
through `is_fresh`, `gates.permitted`, `already_notified`, and `record_notice`,
which is dedup, permission, and delivery tracking that already exists and is
already tested.

`"review"` joins `pipeline._REQUESTED_SOURCES`, bypassing the relevance filter,
on the same argument `computer` and `coding` already make: somebody asked for
this directly, and an LLM deciding that a direct request is "not relevant" and
swallowing it is the worst failure mode available.

## The review session

### The beginning: a PR checkout, not a new branch

`session.create` today calls `repo.add_worktree`, which branches from
`origin/HEAD`. A review needs the pull request's head instead, so `repo.py`
gains one function beside it:

```python
async def add_review_worktree(repo: str, session_dir: Path, pr_number: int) -> dict
```

It fetches `refs/pull/<n>/head`, creates a **detached** worktree at that commit,
and returns the merge base against the pull request's base branch.

The merge base matters: the reviewer must diff three-dot (`base...head`), or a
pull request opened against a branch that has since moved shows unrelated
commits as findings.

Detached is deliberate. A review creates no branch and pushes nothing, so there
is no branch to name and none to leak, and the existing `remove_worktrees`
teardown works unchanged. It stays in `repo.py` for that file's stated reason:
no git command leaves it.

### The middle: the same session, the same supervisor

`registry.build` already maps agent plus model to argv, so a reviewer is a
session with different arguments. `session.py` needs no new concept of a review.
It needs exactly one change: `_SYSTEM_HINT` is currently hardcoded to "commit
your work, do not push, do not open a pull request", so it becomes a parameter
on `session.create`, with the current text as the default for coding sessions.

The review hint tells the agent: you are reviewing, not fixing; do not edit
source files; the rubric is at these three paths; apply only the sections
matching this repository's stack and name the ones you skipped; write your
findings to `review.json` in the session directory when you are done.

`supervisor.decide` already classifies an idle turn as `reply`, `done`, or
`escalate`, and all three mean something for a review. `reply` answers a reviewer
asking which of two conventions the repository actually follows. `escalate`
handles a reviewer that needs a human. `done` triggers the ending. The prompt
gains a review-aware branch, because "the work is complete" means "the findings
are written" rather than "the code is committed", but the cursor, the turn
budget, the stale timeout, and the twenty-second tick are reused as-is.

### Choosing the reviewer

The issue asks for a different provider or model than the one that implemented
the change, and `eve_coding_session` already records `agent` and `model` per
session. So:

- **For a pull request Eve authored**, the reviewer picks a different agent and
  a different model from the pair recorded on the session that opened it.
- **For a human pull request**, there is nothing to avoid, so it uses a
  configured default reviewer pair.

The chosen pair is written into both the review body on GitHub and the
notification, because "a different model reviewed than implemented" is only
checkable by a human if it is stated where a human looks.

### One bound is new

`session_turn_timeout_seconds` (1800) and `session_timeout_seconds` (14400) are
right for a half-hour implementation across several repositories. A review that
has run four hours has failed at something other than reviewing, and it is
holding one of three concurrent session slots while a human waits on a pull
request. So a review session carries its own shorter ceiling and its own
concurrency cap, separate from coding sessions, so a burst of labelled pull
requests cannot starve the delegated coding work `check_coding_session` promises
a member is in flight.

### The ending: `review.json`

The agent writes it, the box reads it, the box posts. The shape is deliberately
small, because every field is something the agent has to get right unprompted:

```json
{
  "summary": "one or two sentences",
  "skipped_sections": ["performance: frontend (Python service)"],
  "findings": [
    {
      "severity": "critical|required|optional|nit|fyi",
      "axis": "correctness|readability|architecture|security|performance",
      "file": "src/eve/coding/dispatch.py",
      "line": 112,
      "body": "the finding, in prose"
    }
  ]
}
```

`severity` is the skill's own vocabulary, so the prefixes that appear on GitHub
come from the rubric rather than from us. `axis` is the skill's five axes, which
lets Eve's notification say "two security findings" rather than a bare count.
`skipped_sections` is what makes the stack-scoping rule auditable.

**The box validates before it posts.** An unknown severity, a missing file, a
line outside the diff, or malformed JSON is a session failure, not a partial
post. This is the security checklist's LLM05 Improper Output Handling applied to
ourselves: the findings file is model output, so it is untrusted input to the
`gh` call.

**If `review.json` is absent when the agent says it is done**, the session fails
and the member is told the review produced no findings file. It does not fall
back to scraping the transcript. A review that silently degrades into a summary
of whatever the agent happened to say last is worse than no review, because it
looks like a review.

## Posting to GitHub

### The box posts, the agent does not

This is `repo.py`'s existing rule and the argument transfers exactly: `gh` is on
PATH and the agent could run it, but then whether a review appears depends on
whether the agent remembered. Doing it in the box is deterministic, and it gives
Eve the findings as data for her notification rather than as prose she would have
to re-read.

`repo.py` gains one function:

```python
async def post_review(repo: str, pr_number: int, review: dict) -> dict
```

It posts **one** review with a summary body and inline comments, via `gh api`
against `/repos/{owner}/{repo}/pulls/{n}/reviews`, with `event: "COMMENT"`. One
API call rather than one per finding, so the review arrives as a single
notification to the author rather than thirty.

### `COMMENT` is hardcoded, not configured

Not a setting defaulting to `COMMENT`. A setting is an invitation to change it
later without revisiting the argument.

Eve's review carries findings, never merge authority. A wrong `REQUEST_CHANGES`
blocks a human's work until someone dismisses it; an `APPROVE` lets an LLM
approve its way into `main`. Neither failure is reachable if the verb is a
literal. This is LLM06 Excessive Agency enforced in code rather than in a prompt,
which is what that entry actually asks for.

### Findings are untrusted input to `gh`

The agent produced them, so LLM05 Improper Output Handling applies to us:

- Findings are passed as a **JSON request body from a file**, never interpolated
  into a shell string. No finding text can become an argument, a flag, or a
  shell metacharacter.
- `repo` and `pr_number` come from the **webhook payload**, never from
  `review.json`. The agent cannot redirect a review onto a different repository
  or a different pull request, because it is never asked where to post. That is
  the highest-consequence thing the agent could get wrong, and the design removes
  its ability to express it.
- Inline comments are posted with `path`, `line`, and `side: "RIGHT"`. A finding
  whose line is not in the diff is **demoted to the summary body rather than
  dropped**: GitHub rejects the entire review if any one comment is unanchorable,
  and one bad line number must not cost the other twenty-nine findings.

### Body format

The summary carries the axis counts, the skipped sections, and a line naming the
reviewing agent and model. Each inline comment is prefixed with its severity in
the skill's own vocabulary (`Critical:`, `Nit:`, `Optional:`, or unprefixed for
required), so the author can tell what is mandatory from what is taste, which is
the stated purpose of that table.

### Idempotence and failure

Before posting, the box lists existing reviews and skips if it already posted one
for this `head_sha`. The ambient layer dedupes at the signal level, but a retry
after a partial failure reaches here directly, and a duplicated thirty-comment
review is the kind of noise that gets a feature turned off.

A `gh` failure returns in the result dict rather than raising, matching
`publish`'s reasoning: the review took real time and real tokens, and losing the
findings because the post failed would waste all of it. Eve tells the member the
review completed but could not be posted, and the findings ride in the
notification so the work is not lost.

### GitHub identity

The box authenticates as Eve's own GitHub account, which `gh auth login` already
establishes for `pr create`. Posting a review needs no scope beyond what opening
a pull request already requires.

One consequence worth stating: when Eve reviews her own pull request, GitHub
shows the same account as author and reviewer, so a branch protection rule
requiring an approving review from someone else is unaffected. That is the
correct outcome.

## Reporting back

The review posts, the member hears about it once, the session closes, and nothing
else fires. Any follow-up is a coding task the member asks for in their own
words, through `delegate_coding_task`, which already exists.

`eve_ambient/sources/review.py` polls resolved review sessions and emits a
`Signal`, exactly as `sources/coding.py` does for delegated work, including the
24-hour re-derivation window that lets a signal suppressed by a cap or a
transient push failure come back on a later tick rather than being lost.

The summary Eve composes from is built from the findings, not from the agent's
prose: how many findings at what severity, which axes, and the link. "Three
findings on your auth change, one critical, on chalifour-development/eve-ai#41."
A member who wants the detail clicks through to GitHub, where the review already
is. Eve does not restate thirty comments in a push notification.

## Data model

No new table. A review session is a row in `eve_coding_session`, which already
carries `agent`, `model`, `repos`, `status`, `cursor`, `result`, and the
supervisor's turn counter.

Three columns are added, all nullable, so a coding session is simply one with
them unset:

| column | type | notes |
|---|---|---|
| `kind` | `text not null default 'code'` | `code` or `review`; what the supervisor branches on |
| `pr_number` | `int` | the pull request under review |
| `head_sha` | `text` | the reviewed commit, and the idempotence key |

Migration `eve_coding_session_review`, revising whatever head is current at
implementation time. The number is deliberately not fixed here: the routines
design (EVE-25) already claims `0011_eve_routine`, and whichever of the two
lands first takes it.

Adding a `kind` column rather than a second table is the smaller change: every
query, the supervisor loop, the stale timeout, and the ambient source are
identical for both kinds, and a parallel table would duplicate all of it to
express one enum.

## Bounds

This feature spends tokens on a trigger Eve does not control, which is new for
this repository, so the limits are gathered rather than inherited:

| Bound | Why it exists |
|---|---|
| `review_enabled`, default off | Same posture as `ambient_enabled`, `computer_enabled`, and `coding_enabled`. A deployment that has not deliberately enabled outbound comments under Eve's GitHub identity posts none. |
| `code.review` permission, per member | The labeller must map to a family member holding it. An unknown GitHub login gets no review. |
| Repository allowlist | The webhook can carry any repository; only configured ones spend tokens. |
| Separate review concurrency cap | A burst of labelled pull requests must not starve delegated coding work. |
| Shorter review session timeout | A review running four hours has failed at something other than reviewing, while holding a slot a human waits on. |
| One review per `head_sha` | Deduped at the signal layer and again at the post, because a retry reaches the second directly. |
| `COMMENT` hardcoded | No configuration path exists to grant merge authority. |

## What this does not contradict

[ADR 0006](../../adr/0006-eve-tools-isolation.md) keeps credentials out of the
box's reach, and this adds no new credential there: `gh` is already authenticated
for `pr create`, and posting a review needs no additional scope.

The Phase 4 rejection of cron is about firing graph runs from inside the graph,
which this does not do: the trigger is a webhook into `eve-ambient`, and the
delivery path is the existing gate chain.

The one genuinely new posture is inbound public ingress, argued under "The
trigger", and it is the reason `review_enabled` defaults to off. No ADR is
warranted; this spec reference is enough.

There is a second risk worth naming honestly rather than leaving implicit. The
reviewing agent reads a hostile pull request's actual content: commit messages,
file contents, code comments. That content is attacker-influenced input to the
agent itself, not merely to the box it runs on. A sufficiently crafted pull
request could attempt to manipulate the reviewing agent into acting beyond
writing `review.json`, including invoking `gh` directly from its own shell
access, because the box's determinism guarantee (`post_review` always posts
`COMMENT`, only from the webhook's own repo and pull request) protects the
posting path and does nothing to stop the agent from acting independently
through the shell it already holds to read the codebase. This is not a new hole
this feature opens on its own; it is the same posture `eve-computer`'s design
already accepts, that no per-action approval gate exists on that box, extended
to a new use of the same box. It is named here rather than left as an implicit
consequence of an earlier decision because this is the first feature that feeds
a reviewing agent attacker-influenced input alongside write-capable network
egress under Eve's own GitHub identity. Nothing here closes that gap; naming it
is the point.

## Open questions for implementation

- Which repositories seed the allowlist. `eve-ai` and `open-assistant` are the
  obvious two.
- The default reviewer pair for human-authored pull requests, which should be a
  strong model since it has no implementer to differ from.
- Whether `repo.publish` labels Eve's own pull requests unconditionally or only
  when `review_enabled` is set. The latter avoids a label nothing consumes.
