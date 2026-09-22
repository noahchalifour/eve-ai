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
