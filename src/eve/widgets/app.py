"""The direct resource API: read a widget's data without a model call.

Mounted into Aegra through `aegra.json`'s `http.app`, with
`enable_custom_route_auth` so Aegra's own `require_auth` runs first and the
client reuses exactly the origin and bearer it already holds for LangGraph.

Authentication is Aegra's. **Authorization is ours**: Aegra's `@auth.on`
handlers scope threads and its store API, and they do not reach a custom
route, so every handler below resolves the member from the authenticated
principal and passes it into an owner-scoped query. A resource id is a
locator and never a capability.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from eve.specialists.permissions import permission_denial
from eve.widgets import recipe as recipe_rules, resolve, store

logger = logging.getLogger(__name__)

app = FastAPI(title="eve-provider-resources")

PREFIX = "/provider-resources/v1"
PROTOCOL = "provider-resource/1.0"

# Risk classes, per the spec. `safe` executes on tap; a `confirm` action needs
# the client's confirmation sheet first. Nothing outside this map is legal, so
# an action cannot be invented by an authored recipe.
ACTION_RISK = {"filters.replace": "safe"}

# The surface-level action id the inline range control carries
# (`eve.widgets.resolve._range_control`). The client maps it onto a
# `filters.replace` action rather than sending it verbatim, so the route's
# vocabulary stays one entry long; this constant exists so the two modules
# cannot drift on the spelling.
RANGE_ACTION_ID = "widget.setRange"


def current_member(request: Request) -> dict:
    """The authenticated principal, as Aegra's `require_auth` left it.

    Overridden in tests. In production `enable_custom_route_auth` has already
    rejected an unauthenticated request before this runs; the guard here is
    for a misconfiguration, where failing closed is the only safe answer.
    """
    user = request.scope.get("user")
    if user is None or not getattr(user, "identity", None):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {
        "sub": user.identity,
        "permissions": list(getattr(user, "permissions", []) or []),
    }


class ActionRequest(BaseModel):
    type: str
    input: dict = {}
    expectedRevision: int
    idempotencyKey: str | None = None

    # No `member_sub` field, deliberately: pydantic drops unknown keys, so a
    # body that carries one is ignored rather than trusted.


def _require_permissions(member: dict, resource: dict) -> None:
    for required in recipe_rules.required_permissions(resource.get("recipe") or {}):
        if permission_denial(member["permissions"], required):
            raise HTTPException(status_code=403, detail="forbidden")


async def _load(member: dict, resource_id: str) -> dict:
    resource = await store.get(member["sub"], resource_id)
    if resource is None:
        # Absent and foreign are the same answer: a 403 here would confirm
        # that someone else's id exists.
        raise HTTPException(status_code=404, detail="not found")
    return resource


@app.get(f"{PREFIX}/capabilities")
async def capabilities(member: dict = Depends(current_member)) -> dict:
    """What this deployment supports. A client that 404s here concludes the
    provider has no widget support at all; a transport failure means
    temporarily unavailable, which is a different thing entirely."""
    return {
        "protocol": PROTOCOL,
        "kinds": sorted(recipe_rules.KINDS),
        "sourceTypes": sorted(recipe_rules.SOURCE_TYPES),
        "actions": [
            {"type": name, "risk": risk} for name, risk in sorted(ACTION_RISK.items())
        ],
        "limits": {"maxDays": recipe_rules.MAX_DAYS},
    }


@app.get(f"{PREFIX}/resources")
async def list_resources(member: dict = Depends(current_member)) -> dict:
    resources = await store.list_for(member["sub"])
    return {"resources": [
        {
            "resourceId": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "revision": row["revision"],
        }
        for row in resources
    ]}


@app.get(f"{PREFIX}/resources/{{resource_id}}/snapshot")
async def snapshot(
    resource_id: str, member: dict = Depends(current_member)
) -> dict:
    resource = await _load(member, resource_id)
    # Re-checked here rather than trusted from authoring time: a permission
    # can be revoked after a widget was saved.
    _require_permissions(member, resource)
    return await resolve.snapshot(resource, member["sub"])


@app.post(f"{PREFIX}/resources/{{resource_id}}/actions")
async def run_action(
    resource_id: str,
    body: ActionRequest,
    member: dict = Depends(current_member),
) -> dict:
    resource = await _load(member, resource_id)

    if body.type not in ACTION_RISK:
        raise HTTPException(status_code=400, detail="unknown action")

    _require_permissions(member, resource)

    error = recipe_rules.validate_filters(body.input)
    if error is not None:
        raise HTTPException(status_code=400, detail=f"invalid filters: {error}")

    updated = await store.update_filters(
        member["sub"], resource_id, body.input, body.expectedRevision
    )
    if updated is None:
        # Somebody else moved first. Answer with the current snapshot so the
        # client can show fresh data instead of an error it cannot act on.
        current = await _load(member, resource_id)
        fresh = await resolve.snapshot(current, member["sub"])
        raise HTTPException(status_code=409, detail=fresh)

    return await resolve.snapshot(updated, member["sub"])


@app.delete(f"{PREFIX}/resources/{{resource_id}}", status_code=204)
async def delete_resource(
    resource_id: str, member: dict = Depends(current_member)
) -> None:
    if not await store.delete(member["sub"], resource_id):
        raise HTTPException(status_code=404, detail="not found")


@app.exception_handler(HTTPException)
async def _flatten(request: Request, exc: HTTPException) -> JSONResponse:
    """A 409 carries a whole snapshot, not a message. Returning it nested
    under `detail` would make the client unwrap conflicts differently from
    every other response."""
    if exc.status_code == 409 and isinstance(exc.detail, dict):
        return JSONResponse(status_code=409, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}
    )