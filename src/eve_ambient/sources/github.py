"""GitHub's pull-request webhook, verified and shaped into a Signal.

WHY THE RAW BODY. GitHub signs the bytes it sent. Verifying anything else,
including a reserialised parse of them, is the classic bypass: a body that
reparses differently than it hashed passes a check it should fail. So the
route hands this module `bytes`, and parsing happens only after `verify`
returns True.

WHY THE KEY IS (repo, pr, sha) AND NOT THE DELIVERY ID. A redelivered
webhook, a label removed and reapplied, and two people labelling at once all
describe the same review of the same code, and the ambient layer should
collapse them. Including the commit means new commits are genuinely a
different review, which is the one case where a second run is correct.

TWO MORE SHAPES (EVE-32, EVE-31). `synchronize` is new commits landing on a
pull request, which may re-commission a review. Review and comment events on
a pull request Eve opened are feedback she may address. Both are parsed here
and decided elsewhere: this module stays pure and does no lookup of its own.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import UTC, datetime

from eve_ambient.types import Signal

logger = logging.getLogger(__name__)

# Only these two commission a review. Everything else GitHub sends, including
# the `ping` on hook creation, is acknowledged and dropped: rejecting a ping
# makes the hook look broken in the GitHub UI.
ACTIONS: tuple[str, ...] = ("labeled", "assigned")


def verify(secret: str, signature: str, body: bytes) -> bool:
    """`X-Hub-Signature-256` against the raw body.

    Fails closed on an unconfigured secret: an empty key must not become an
    open door for anyone who can also send an empty signature.
    """
    if not secret or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature[len("sha256=") :].encode())


def from_webhook(payload: dict) -> Signal:
    action = payload.get("action")
    if action not in ACTIONS:
        raise ValueError(f"action {action!r} does not commission a review")

    pull_request = payload.get("pull_request") or {}
    repository = payload.get("repository") or {}
    repo = repository.get("full_name")
    number = pull_request.get("number")
    head_sha = (pull_request.get("head") or {}).get("sha")
    base_ref = (pull_request.get("base") or {}).get("ref")
    if not (repo and number and head_sha and base_ref):
        raise ValueError("payload names no complete pull request")

    return Signal(
        source="review",
        key=f"{repo}#{number}@{head_sha}",
        occurred_at=datetime.now(UTC),
        # Resolved by the route from `sender.login`, which needs the roster;
        # this module stays pure and does no lookup of its own.
        member_sub="",
        summary=f"{(payload.get('sender') or {}).get('login', 'someone')} "
                f"asked for a review of {repo}#{number}",
        payload={
            "repo": repo,
            "pr_number": number,
            "head_sha": head_sha,
            "base_ref": base_ref,
            "url": pull_request.get("html_url", ""),
            "actor": (payload.get("sender") or {}).get("login", ""),
        },
        cooldown_hours=24,
    )


def from_synchronize(payload: dict) -> dict:
    """`pull_request` / `synchronize`: new commits on the branch (EVE-32).

    Returns the pull request and its NEW head. Whether that head is worth a
    review is not this function's question: it needs the store (was this
    pull request ever reviewed?) and the debounce.
    """
    if payload.get("action") != "synchronize":
        raise ValueError("not a synchronize event")
    pull_request = payload.get("pull_request") or {}
    repo = (payload.get("repository") or {}).get("full_name")
    number = pull_request.get("number")
    head_sha = (pull_request.get("head") or {}).get("sha")
    base_ref = (pull_request.get("base") or {}).get("ref")
    if not (repo and number and head_sha and base_ref):
        raise ValueError("payload names no complete pull request")
    return {
        "repo": repo, "pr_number": number, "head_sha": head_sha,
        "base_ref": base_ref,
    }


# The events and actions that carry feedback on a pull request (EVE-31).
# `edited` and `deleted` are deliberately absent: a reworded comment is
# picked up by whichever address session reads the thread next, and a new
# session per typo fix would spend tokens on punctuation.
FEEDBACK: dict[str, str] = {
    "pull_request_review": "submitted",
    "pull_request_review_comment": "created",
    "issue_comment": "created",
}


def feedback_from_webhook(event: str, payload: dict) -> dict:
    """Who said something on which pull request.

    `issue_comment` fires for issues and pull requests alike; only one with
    `issue.pull_request` set is a pull request. `author` rides along so the
    caller can drop strangers and Eve's own replies: addressing her own
    comments would be a loop, not a conversation.
    """
    if FEEDBACK.get(event) != payload.get("action"):
        raise ValueError(f"{event}/{payload.get('action')} carries no feedback")

    repo = (payload.get("repository") or {}).get("full_name")
    if event == "issue_comment":
        issue = payload.get("issue") or {}
        if not issue.get("pull_request"):
            raise ValueError("a comment on an issue, not a pull request")
        number = issue.get("number")
        pr_url = (issue.get("pull_request") or {}).get("html_url") or issue.get("html_url")
        author = ((payload.get("comment") or {}).get("user") or {}).get("login", "")
    else:
        pull_request = payload.get("pull_request") or {}
        number = pull_request.get("number")
        pr_url = pull_request.get("html_url")
        said = payload.get("review") if event == "pull_request_review" else payload.get("comment")
        author = ((said or {}).get("user") or {}).get("login", "")

    if not (repo and number and pr_url and author):
        raise ValueError("payload names no complete piece of feedback")
    return {
        "repo": repo, "pr_number": number, "pr_url": pr_url,
        "author": author, "kind": event,
    }
