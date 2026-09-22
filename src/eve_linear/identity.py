"""Who is asking, may they ask, and what may Eve touch.

All three are pure local computation - a dict lookup, a frozenset membership
test, and a string scan over a configured list. That is what lets them run
ahead of the acknowledgement in handler.py: their result decides WHICH
activity to emit, and none of them can block on a network or a database.

THE ALLOWLIST IS THE INJECTION BOUNDARY. Guidance is text that anyone with
workspace access can edit, and an issue body is text anyone with a seat can
write. Both reach a coding agent. The one thing neither can do is widen which
repos are reachable, because `resolve_repos` only ever returns members of the
configured allowlist. Matching guidance against the allowlist (rather than
parsing repos out of guidance and checking them after) is what makes that
true by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from eve.coding.dispatch import PERMISSION
from eve.family import Member, get_family


@dataclass(frozen=True)
class Refusal:
    """Why Eve will not start, in words a human reads in Linear."""

    kind: str  # "unmapped" | "unpermitted" | "no_repo"
    message: str


def resolve_member(linear_user_id: str) -> tuple[Member | None, Refusal | None]:
    """Exactly one of the two is None.

    Both refusals are terminal rather than answerable: no reply typed into
    Linear can add a roster entry or grant a permission, because both are
    pull requests against family.yaml by design.
    """
    member = get_family().by_linear_id(linear_user_id)
    if member is None:
        return None, Refusal(
            "unmapped",
            "I don't act for that Linear account. Mapping a Linear user to a "
            "family member is a change to `family.yaml`, so someone will need "
            "to open a pull request adding a `linear_id` for you.",
        )
    if not member.can(PERMISSION):
        return None, Refusal(
            "unpermitted",
            f"{member.name} doesn't have the `{PERMISSION}` permission, so I "
            "can't take on coding work for them. Granting it is a change to "
            "`family.yaml`.",
        )
    return member, None


def resolve_repos(
    guidance: str | None, allowlist: list[str]
) -> tuple[list[str], Refusal | None]:
    """Which allowed repos this guidance names, in allowlist order.

    Allowlist order rather than mention order so the result does not depend
    on how the guidance sentence was phrased.

    The word-boundary match is what stops `owner/rep` from resolving to
    `owner/repo`: a near-miss that silently widened the boundary would defeat
    the whole point of having one.
    """
    text = guidance or ""
    found = [
        repo
        for repo in allowlist
        if re.search(rf"(?<![\w/-]){re.escape(repo)}(?![\w/-])", text)
    ]
    if not found:
        return [], Refusal(
            "no_repo",
            "I can't tell which repo this should land in. Tell me which repo "
            "to work in, or add it to the team's guidance in Linear.",
        )
    return found, None
