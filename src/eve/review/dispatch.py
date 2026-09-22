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
from eve.review import reviewer
from eve.settings import get_settings
from eve.tools_client import create_review_session

logger = logging.getLogger(__name__)


def _goal(repo: str, pr_number: int) -> str:
    return f"review {repo}#{pr_number}"


async def start(
    repo: str,
    pr_number: int,
    head_sha: str,
    base_ref: str,
    member_sub: str,
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
        session_id, agent, model, repo, pr_number, base_ref, goal
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
