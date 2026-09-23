"""Starting a review, with every refusal checked before the box is called.

WHY THE CHECKS ARE HERE AND NOT ON THE BOX. The allowlist, the feature flag,
and the already-reviewed check all decide whether to SPEND, and the spend
starts the moment the box accepts a session. ADR 0006's pattern again: a
denied request never reaches eve-computer at all.

WHY THE ROW IS WRITTEN AFTER THE BOX ACCEPTS. Identical to
`delegate_coding_task`: a row for a session the box never heard of would be
polled forever by the supervisor and eventually reported as stale, for work
that never started.

WHY THERE IS NO `thread_id` PARAMETER. A review is triggered by a GitHub
webhook, not by a member typing in a conversation, so there is no
member-owned thread to attach it to. `store.create_session` accepts
`thread_id=None` for exactly this case (`kind="review"`), and this module
always passes that literal rather than taking a caller-supplied thread.
"""

from __future__ import annotations

import logging
import uuid

from eve.coding import store
from eve.family import UnknownMemberError, get_family
from eve.review import reviewer
from eve.settings import get_settings
from eve.tools_client import create_review_session

logger = logging.getLogger(__name__)

PERMISSION = "code.review"


def _goal(repo: str, pr_number: int) -> str:
    return f"review {repo}#{pr_number}"


async def start(
    repo: str,
    pr_number: int,
    head_sha: str,
    base_ref: str,
    member_sub: str,
    since_sha: str | None = None,
) -> str:
    settings = get_settings()
    if not settings.review_enabled:
        return "error: reviewing is disabled"
    if repo not in settings.review_repos:
        logger.info("refusing to review %s: not in the allowlist", repo)
        return f"error: {repo} is not a repository Eve reviews"

    if await store.review_exists_for(repo, pr_number, head_sha):
        return f"{repo}#{pr_number} at {head_sha[:8]} has already been reviewed"

    implemented_by = await store.implementer_of(repo, head_sha)
    agent, model = reviewer.choose(implemented_by)

    session_id = str(uuid.uuid4())
    goal = _goal(repo, pr_number)
    dispatched = await create_review_session(
        session_id, agent, model, repo, pr_number, base_ref, goal,
        since_sha=since_sha,
    )
    if dispatched.startswith("error:"):
        return dispatched

    await store.create_session(
        session_id=session_id,
        member_sub=member_sub,
        thread_id=None,
        goal=goal,
        agent=agent,
        model=model,
        repos=[repo],
        context="",
        kind="review",
        pr_number=pr_number,
        head_sha=head_sha,
    )
    return f"reviewing {repo}#{pr_number} with {agent}/{model}"


async def restart_on_push(
    repo: str, pr_number: int, head_sha: str, base_ref: str
) -> str:
    """New commits landed on a pull request (EVE-32). Called once the branch
    has been quiet for `review_debounce_seconds`, never per push.

    OPT-IN BY HISTORY. Only a pull request Eve has already been asked to
    review is re-reviewed: the original label or assignment is the opt-in,
    and a pull request nobody asked about commissions nothing however often
    it is pushed to. The re-review runs on behalf of whoever asked for the
    latest review, and is checked against their `code.review` grant as it
    stands now, not as it stood then.

    THE CAP. `review_max_per_pr` reviews in total, counting the first. Past
    it a push is ignored and a human relabels (which goes through `start`,
    uncapped, because that is a person asking) rather than Eve spending on a
    back-and-forth nobody is watching.

    ONE AT A TIME. A review still running is not joined by a second one;
    the push that arrives during it is picked up by the next push, or by a
    relabel.

    INCREMENTAL. The previous review's `head_sha` goes to the box as
    `since_sha`, so the reviewer focuses on what changed after it.
    """
    settings = get_settings()
    if not settings.review_enabled or not settings.review_on_push:
        return "re-review on push is disabled"
    if repo not in settings.review_repos:
        return f"{repo} is not a repository Eve reviews"

    previous = await store.latest_review_for(repo, pr_number)
    if previous is None:
        return f"{repo}#{pr_number} was never reviewed; a push does not opt it in"
    if previous.get("head_sha") == head_sha:
        return f"{repo}#{pr_number} at {head_sha[:8]} has already been reviewed"

    stats = await store.pr_session_stats("review", repo, pr_number)
    if stats["live"]:
        return f"a review of {repo}#{pr_number} is still running"
    if stats["total"] >= settings.review_max_per_pr:
        logger.info(
            "not re-reviewing %s#%s: %d reviews already, the cap is %d",
            repo, pr_number, stats["total"], settings.review_max_per_pr,
        )
        return (
            f"{repo}#{pr_number} has had {stats['total']} reviews, the cap;"
            " relabel it to ask for another"
        )

    member_sub = previous["member_sub"]
    try:
        member = get_family().get(member_sub)
    except UnknownMemberError:
        return f"the member who asked for {repo}#{pr_number}'s review is gone"
    if not member.can(PERMISSION):
        return f"the member who asked for {repo}#{pr_number}'s review can no longer ask"

    return await start(
        repo=repo, pr_number=pr_number, head_sha=head_sha, base_ref=base_ref,
        member_sub=member_sub, since_sha=previous.get("head_sha"),
    )
