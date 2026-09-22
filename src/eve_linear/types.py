"""The parsed webhook event.

A dataclass rather than a pydantic model: nothing here is a structured-output
schema for a model, and every field is optional in practice because Linear
sends agent sessions for surfaces this feature does not support (documents,
projects). Parsing must never raise - a 500 on the webhook path tells Linear
to retry a request that will fail the same way forever.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LinearEvent:
    action: str
    session_id: str | None
    issue_id: str | None
    team_id: str | None
    actor_id: str | None
    guidance: str
    prompt_body: str
    prompt_context: str
