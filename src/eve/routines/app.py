"""The routines resource API: read and manage standing requests without a
model call.

Mounted through `eve.http_app`. The auth boundary is declared on this router
as `Depends(require_auth)` rather than relying on Aegra's
`enable_custom_route_auth` walk, which is a no-op in aegra-api 0.10.3 (it
rewrites `route.dependencies` after the routes are built, but FastAPI
resolves dependencies from the dependant constructed at route-creation time).
Same reasoning, same shape as `eve.widgets.app`.

There is deliberately NO create route. Writing an instruction Eve will act on
unattended belongs in conversation, where she can ask what "a good deal"
means before it is committed to a table. This surface manages what exists.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from aegra_api.core.auth_deps import require_auth

from eve.routines import cadence as cadence_rules, store

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_auth)])

PREFIX = "/provider-resources/v1/routines"
PROTOCOL = "provider-routine/1.0"

# What a client may assign. `expired` is absent deliberately: it is the
# server's conclusion from the expiry it was given, not a state to be claimed.
_ASSIGNABLE_STATUS = ("active", "paused")


class PatchRequest(BaseModel):
    title: str | None = None
    cadence: dict | None = None
    status: str | None = None
    expires_at: str | None = None
    expectedRevision: int

    # No `member_sub` and no `next_run_at` field, deliberately: pydantic drops
    # unknown keys, so a body carrying either is ignored rather than trusted.
    # The owner comes from the authenticated principal and the schedule is
    # computed from the cadence.


def current_member(request: Request) -> dict:
    """The authenticated principal, as the router's `require_auth` left it.

    Overridden in tests. In production the explicit `Depends(require_auth)`
    has already rejected an unauthenticated request; the guard here is for a
    misconfiguration, where failing closed is the only safe answer.
    """
    user = request.scope.get("user")
    if user is None or not getattr(user, "identity", None):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {
        "sub": user.identity,
        "permissions": list(getattr(user, "permissions", []) or []),
    }


def _public(row: dict) -> dict:
    """The wire shape. `member_sub` is never echoed: the caller is the owner
    by construction, so it carries no information and inviting a client to
    read it invites one to send it."""
    return {
        "routineId": row["id"],
        "title": row["title"],
        "instruction": row["instruction"],
        "cadence": row["cadence"],
        "status": row["status"],
        "nextRunAt": row["next_run_at"].isoformat() if row.get("next_run_at") else None,
        "lastRunAt": row["last_run_at"].isoformat() if row.get("last_run_at") else None,
        "lastOutcome": row.get("last_outcome"),
        "consecutiveFailures": row.get("consecutive_failures", 0),
        "expiresAt": row["expires_at"].isoformat() if row.get("expires_at") else None,
        "revision": row["revision"],
    }


@router.get(f"{PREFIX}/capabilities")
async def capabilities(member: dict = Depends(current_member)) -> dict:
    """What this deployment supports. A client that 404s here concludes the
    provider has no routine support at all; a transport failure means
    temporarily unavailable, which is a different thing entirely and must not
    hide the destination."""
    return {
        "protocol": PROTOCOL,
        "cadenceKinds": ["every_hours", "daily_at", "weekly_at"],
        "statuses": list(_ASSIGNABLE_STATUS),
        "limits": {
            "minEveryHours": cadence_rules.MIN_EVERY_HOURS,
            "maxEveryHours": cadence_rules.MAX_EVERY_HOURS,
        },
        # Authoring is conversational; the client must not offer a create form.
        "canCreate": False,
    }


@router.get(PREFIX)
async def list_routines(member: dict = Depends(current_member)) -> dict:
    rows = await store.list_for(member["sub"])
    return {"routines": [_public(row) for row in rows]}


@router.patch(f"{PREFIX}/{{routine_id}}")
async def patch_routine(
    routine_id: str,
    body: PatchRequest,
    member: dict = Depends(current_member),
) -> dict:
    fields: dict = {}

    if body.title is not None:
        fields["title"] = body.title

    if body.status is not None:
        if body.status not in _ASSIGNABLE_STATUS:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of: {', '.join(_ASSIGNABLE_STATUS)}",
            )
        fields["status"] = body.status

    if body.expires_at is not None:
        try:
            expiry = datetime.fromisoformat(body.expires_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="expires_at must be ISO-8601")
        fields["expires_at"] = (
            expiry if expiry.tzinfo else expiry.replace(tzinfo=UTC)
        )

    if body.cadence is not None:
        error = cadence_rules.validate(body.cadence)
        if error is not None:
            raise HTTPException(status_code=400, detail=f"invalid cadence: {error}")
        fields["cadence"] = body.cadence
        # Recomputed here, never accepted from the client: a caller that
        # supplied its own next_run_at could schedule a routine to fire
        # immediately, and then again on every tick.
        existing = await store.get(member["sub"], routine_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="not found")
        fields["next_run_at"] = cadence_rules.next_after(
            body.cadence, existing["timezone"], datetime.now(UTC)
        )

    if not fields:
        raise HTTPException(status_code=400, detail="nothing to change")

    updated = await store.update(
        member["sub"], routine_id, body.expectedRevision, **fields
    )
    if updated is None:
        # Absent, foreign, or stale. A missing row is a 404 that reveals
        # nothing; a real row at another revision is a 409 carrying the truth.
        current = await store.get(member["sub"], routine_id)
        if current is None:
            raise HTTPException(status_code=404, detail="not found")
        raise HTTPException(status_code=409, detail=_public(current))

    if fields.get("status") == "active":
        # Resuming is the acknowledgement the failure counter waited for.
        await store.clear_failures(member["sub"], routine_id)

    return _public(updated)


@router.delete(f"{PREFIX}/{{routine_id}}", status_code=204)
async def delete_routine(
    routine_id: str, member: dict = Depends(current_member)
) -> None:
    if not await store.delete(member["sub"], routine_id):
        raise HTTPException(status_code=404, detail="not found")
