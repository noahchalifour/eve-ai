"""Eve monitors the pull requests she opens (EVE-31).

A review or comment lands on a pull request Eve opened, from a chat
delegation or from Linear alike: both go through a `kind="code"` session
whose close records the pull request's URL. After the thread has gone quiet
for `pr_followup_debounce_seconds`, this starts an `address` session: a
coding agent on the pull request's own branch, given every review and comment
from people Eve works for, told to apply the vendored receiving-code-review
skill, which pushes its fixes and replies on the thread.

WHO MAY COMMISSION ONE. Nobody asks for this explicitly; the pull request's
origin does. The session runs on behalf of the member who asked for the
original change, and under their `code.delegate` grant as it stands now.
Only feedback from a family member with a `github_login` counts (Eve's own
login is excluded, see below): a stranger commenting on a public repository must not be able to
make an agent push code under Eve's identity.

EVE'S OWN REVIEWS ARE NOT FEEDBACK HERE. EVE-27 posts its review under
Eve's GitHub identity, and addressing it automatically is precisely the
"agent reviewing an agent then commissioning a third to fix it" loop that
EVE-27 made a non-goal. So `github_login`'s own reviews and comments are
dropped at the webhook, and a member who wants the findings addressed says
so in a comment, which is a person asking.

WHY THE CHECKS ARE HERE. The same reason `eve.review.dispatch` gives: every
refusal decides whether to spend, and the spend starts when the box accepts.
"""

from __future__ import annotations

import logging
import uuid

from eve.coding import store
from eve.family import UnknownMemberError, get_family
from eve.settings import get_settings
from eve.tools_client import create_address_session, kill_coding_session

logger = logging.getLogger(__name__)

PERMISSION = "code.delegate"


def trusted_authors() -> list[str]:
    """The GitHub logins whose feedback an address session acts on: every
    family member with one mapped. Eve's own login is never among them."""
    own = get_settings().github_login.lower()
    return [
        m.github_login for m in get_family().members()
        if m.github_login and m.github_login.lower() != own
    ]


def is_trusted(login: str) -> bool:
    return bool(login) and login.lower() in {a.lower() for a in trusted_authors()}


async def start(repo: str, pr_number: int, pr_url: str) -> str:
    """Address the feedback on one pull request. Called once the thread has
    been quiet, never per comment. Returns a short outcome for the log;
    refusals start with a reason rather than "error:" because most are the
    normal case (a comment on somebody else's pull request)."""
    settings = get_settings()
    if not settings.pr_followup_enabled:
        return "following up on pull requests is disabled"

    origin = await store.origin_of_pr(pr_url)
    if origin is None:
        return f"{pr_url} was not opened by Eve"

    stats = await store.pr_session_stats("address", repo, pr_number)
    if stats["live"]:
        # The running session reads the whole thread when it starts, so the
        # comment that arrived during it is either already in it or is
        # picked up by the next comment. Two agents pushing to one branch is
        # the thing to avoid.
        return f"feedback on {repo}#{pr_number} is already being addressed"
    if stats["total"] >= settings.pr_followup_max_per_pr:
        logger.info(
            "not addressing %s#%s: %d follow-ups already, the cap is %d",
            repo, pr_number, stats["total"], settings.pr_followup_max_per_pr,
        )
        return f"{repo}#{pr_number} has had {stats['total']} follow-ups, the cap"

    try:
        member = get_family().get(origin["member_sub"])
    except UnknownMemberError:
        return f"the member who asked for {pr_url} is gone"
    if not member.can(PERMISSION):
        return f"the member who asked for {pr_url} can no longer delegate code"

    # The implementer that opened the pull request answers for it. It
    # already knows the change, and a different model "fixing" another's
    # work is the churn this feature is bounded against.
    agent, model = origin["agent"], origin["model"]
    goal = f"address feedback on {repo}#{pr_number}"
    session_id = str(uuid.uuid4())
    authors = trusted_authors()

    dispatched = await create_address_session(
        session_id, agent, model, repo, pr_number, goal, authors
    )
    if dispatched.startswith("error:"):
        return dispatched

    try:
        await store.create_session(
            session_id=session_id,
            member_sub=member.sub,
            # The original conversation, so the outcome is reported where
            # the member asked for the change. A Linear-dispatched session
            # has one too (the handler opens a thread for it).
            thread_id=origin["thread_id"],
            goal=goal,
            agent=agent,
            model=model,
            repos=[repo],
            context=origin.get("context") or "",
            kind="address",
            pr_number=pr_number,
            head_sha=None,
            linear_session_id=None,
            linear_issue_id=origin.get("linear_issue_id"),
        )
    except Exception:
        logger.warning("could not record address session for %s", pr_url, exc_info=True)
        await kill_coding_session(session_id)
        return "error: could not record the session"
    return f"addressing feedback on {repo}#{pr_number} with {agent}/{model}"
