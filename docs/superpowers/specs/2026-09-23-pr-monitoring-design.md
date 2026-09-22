# Monitoring pull requests: re-review on push, and addressing feedback

Covers two follow-ups to EVE-27 (`2026-09-22-auto-review-prs-design.md`):

- [EVE-32](https://linear.app/chalifour-development/issue/EVE-32/re-review-prs-when-new-commits-land):
  re-review a pull request when new commits land.
- [EVE-31](https://linear.app/chalifour-development/issue/EVE-31/eve-should-monitor-prs-she-creates):
  Eve monitors the pull requests she creates and addresses the reviews and
  comments left on them, using the `receiving-code-review` skill.

Both hang off the same GitHub webhook route (`/signals/github` on
`eve-ambient`) and the same in-memory debouncer.

## Re-review on push (EVE-32)

```
GitHub ── pull_request synchronize ──▶ eve-ambient
                                         │ debounce ("review", repo, pr), 300s
                                         ▼
                               dispatch.restart_on_push
                                         │ opt-in? cap? live? grant?
                                         ▼
                               dispatch.start(since_sha=previous head)
                                         ▼
                   eve-computer: review worktree, review_hint(since=…)
```

**Opt-in or opt-out: opt-in, by history.** A push re-reviews only a pull
request Eve has already been asked to review, by label or assignment. The
first request is the opt-in; a pull request nobody asked about commissions
nothing, however often it is pushed to. `EVE_REVIEW_ON_PUSH=false` turns
re-review off entirely. The pusher is not checked: the re-review runs on
behalf of the member who asked for the latest review, and their
`code.review` grant is checked again when the debounce fires.

**Debounce.** Each push replaces the pending action for that pull request,
so a burst of commits produces one review of the newest head, started once
the branch has been quiet for `EVE_REVIEW_DEBOUNCE_SECONDS` (300). It lives
in memory because `eve-ambient` runs as one replica. A restart drops what is
pending; the next push, or a relabel, recreates it.

**Cap.** `EVE_REVIEW_MAX_PER_PR` (3) reviews in total, counting the first.
Past it a push is ignored. Relabelling goes through the EVE-27 path, which
has no cap, because that is a person asking.

**One at a time.** A push while a review is still running starts nothing.

**Incremental.** The previous review's `head_sha` (already on the session
row) goes to the box as `since_sha`. The box keeps it only if it is still an
ancestor of the new head. After a force-push it isn't, and the review falls
back to the whole pull request. The re-review hint focuses the agent on
`git diff <since>..HEAD` and on whether the earlier findings were resolved.
Inline comments still anchor to the full pull request diff through the merge
base, so `post_review` is unchanged.

The existing `(pr_number, head_sha)` uniqueness still stops the same commit
from being reviewed twice.

## Addressing feedback on Eve's own pull requests (EVE-31)

```
GitHub ── pull_request_review / _review_comment / issue_comment ──▶ eve-ambient
             │ author is a family member, not Eve?
             │ debounce ("followup", repo, pr), 180s
             ▼
      followup.start ── opened by Eve? (store.origin_of_pr) cap? live? grant?
             ▼
      eve-computer: kind="address"
        worktree on the PR's own branch, feedback.json, address_hint
             ▼ (supervisor: done once followup.json is written)
      close_address: validate followup.json → push (never forced) → reply
```

**Which pull requests.** One that a `kind="code"` session opened. That covers
chat delegations and Linear sessions alike, since both publish through
`repo.publish`, which records the `pr_url`. Matching uses that URL, which
GitHub's webhook and `gh pr create` spell the same way.

**Whose feedback.** Only family members with a `github_login`. The box
filters to those authors again when it fetches the thread, so a stranger's
comment on a public repository never reaches an agent that pushes. Eve's own
login (`EVE_GITHUB_LOGIN`) is excluded. That covers her own replies, which
would otherwise loop, and the reviews EVE-27 posts under her identity.
EVE-27 made "an agent reviewing an agent and commissioning a third to fix it"
a non-goal, and this keeps it that way: a member who wants Eve's findings
addressed says so in a comment.

**The skill.** `prompts/receiving-code-review/SKILL.md`, vendored verbatim
from `obra/superpowers` and pinned (see its `SOURCE.md`). It was found with
the `find-skills` skill, as the issue asked. The address hint tells the agent
to read and follow it before changing anything: verify each point against
the code, fix what is right, push back with reasoning on what is wrong, and
don't agree just to be agreeable.

**The session.** The implementer that opened the pull request (same agent and
model) runs on the pull request's branch, which is not detached. It competes
for coding slots rather than review slots. It commits and does not push. It
writes `followup.json`:

```json
{"summary": "…", "replies": [{"comment_id": 123, "body": "…"}]}
```

**Closing.** The box validates `followup.json` before pushing anything. Each
reply must name an inline review comment the box itself fetched. It then
pushes without forcing: if a human pushed in the meantime, the push is
rejected and reported, and no replies are posted, because Eve never says
"fixed" about a fix that never landed. The pushed head is recorded in the
same `prs` shape as a coding session, so `store.implementer_of` treats the
address session as the commit's author. That means a re-review of that
commit picks a different model.

**Reporting.** The row has `kind="address"`, the original session's
`thread_id`, and the member who asked. It is reported through the `coding`
ambient source, on the thread the change came from.

**Bounds.** Off by default (`EVE_PR_FOLLOWUP_ENABLED`), because it pushes.
There is a 180s quiet period (`EVE_PR_FOLLOWUP_DEBOUNCE_SECONDS`), so one
review with ten inline comments is one session. At most
`EVE_PR_FOLLOWUP_MAX_PER_PR` (5) sessions run per pull request, one at a
time. Pull requests from forks are refused.

## Webhook setup

The GitHub hook must also send **Pull request reviews**, **Pull request
review comments**, and **Issue comments**. EVE-27 already required **Pull
requests**, which carries `synchronize`.

No migration is needed. `kind` is free text, the `eve_coding_session_review_or_threaded`
check already permits an `address` row, which always has a thread, and
`head_sha` was already stored.
