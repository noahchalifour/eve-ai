"""The direct resource API: read a widget's data without a model call.

Mounted into Aegra through `aegra.json`'s `http.app`. The auth boundary is
declared on the widget router itself, as `Depends(require_auth)`, rather than
relying on Aegra's `enable_custom_route_auth` walk: in aegra-api 0.10.3 that
walk rewrites `route.dependencies` after the routes are built, but FastAPI
resolves dependencies from the dependant constructed at route-creation time,
so the walk never actually guards anything. Declaring the dependency next to
the routes it guards keeps the boundary explicit and leaves Aegra's health
probes unauthenticated. The client reuses exactly the origin and bearer it
already holds for LangGraph.

Authentication is Aegra's. **Authorization is ours**: Aegra's `@auth.on`
handlers scope threads and its store API, and they do not reach a custom
route, so every handler below resolves the member from the authenticated
principal and passes it into an owner-scoped query. A resource id is a
locator and never a capability.

Writes carry an `idempotencyKey`, and the field is accepted for transport
compatibility. There is deliberately no idempotency store: a retried action
that sends the same `expectedRevision` and key converges anyway, because a
revision-guarded update is value-idempotent. A stale retry fails the
revision guard and is answered with a 409 carrying the current snapshot,
which is the client's expected conflict path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aegra_api.core.auth_deps import require_auth

from eve.specialists.permissions import permission_denial
from eve.widgets import actions as action_registry
from eve.widgets import presets
from eve.widgets import recipe as recipe_rules, resolve, store
from eve.widgets import sources as source_registry
from eve.widgets.actions.base import ActionConflict, ActionContext, ActionFailed, ActionRejected

logger = logging.getLogger(__name__)

app = FastAPI(title="eve-provider-resources")
router = APIRouter(dependencies=[Depends(require_auth)])

PREFIX = "/provider-resources/v1"
PROTOCOL = "provider-resource/1.0"

MIN_REFRESH_SECONDS = 5

# The surface-level action id the inline range control carries
# (`eve.widgets.presets._chart`). The client maps it onto a
# `filters.replace` action rather than sending it verbatim, so the route's
# vocabulary stays one entry long; this constant exists so the two modules
# cannot drift on the spelling.
RANGE_ACTION_ID = "widget.setRange"


def current_member(request: Request) -> dict:
    """The authenticated principal, as the router's `require_auth` left it.

    Overridden in tests. In production the explicit `Depends(require_auth)`
    on the router has already rejected an unauthenticated request before this
    runs; the guard here is for a misconfiguration, where failing closed is
    the only safe answer.
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
    # `idempotencyKey` is accepted for transport compatibility and is
    # currently informational only: there is no idempotency store. A retried
    # action with the same expectedRevision + key converges because the
    # revision guard makes the update value-idempotent; a stale retry gets a
    # 409 carrying the current snapshot.

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


@router.get(f"{PREFIX}/capabilities")
async def capabilities(member: dict = Depends(current_member)) -> dict:
    """Generated from the registries: registering a source, action or preset
    is the whole job of advertising it."""
    return {
        "protocol": PROTOCOL,
        "kinds": sorted([*presets.PRESETS, "custom"]),
        # Separate from `kinds` (renderable widget families): a client asks
        # "can this provider host a per-device dashboard?", which is a
        # capability of the provider, not a widget kind.
        "features": ["dashboard"],
        "sourceTypes": sorted(source_registry.REGISTRY),
        "actions": [
            {"type": a.name, "risk": a.default_risk, "label": a.label}
            for a in sorted(action_registry.REGISTRY.values(), key=lambda a: a.name)
        ],
        "limits": {"maxDays": recipe_rules.MAX_DAYS, "minRefreshSeconds": MIN_REFRESH_SECONDS},
    }


@router.get(f"{PREFIX}/resources")
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


@router.get(f"{PREFIX}/resources/{{resource_id}}/snapshot")
async def snapshot(
    resource_id: str, member: dict = Depends(current_member)
) -> dict:
    resource = await _load(member, resource_id)
    # Re-checked here rather than trusted from authoring time: a permission
    # can be revoked after a widget was saved.
    _require_permissions(member, resource)
    return await resolve.snapshot(resource, member["sub"])


@router.post(f"{PREFIX}/resources/{{resource_id}}/actions")
async def run_action(
    resource_id: str,
    body: ActionRequest,
    member: dict = Depends(current_member),
) -> dict:
    resource = await _load(member, resource_id)
    action = action_registry.REGISTRY.get(body.type)
    if action is None:
        raise HTTPException(status_code=400, detail="unknown action")

    _require_permissions(member, resource)
    if action.permission and permission_denial(member["permissions"], action.permission):
        raise HTTPException(status_code=403, detail="forbidden")

    target = None
    if action.targeted:
        target = body.input.get("target")
        # The security boundary: only targets THIS widget's sources declared.
        if not isinstance(target, str) or target not in recipe_rules.declared_targets(resource["recipe"]):
            raise HTTPException(status_code=400, detail="unknown target")
        if action.risk_for(target) is None:
            raise HTTPException(status_code=400, detail="action cannot act on that target")

    ctx = ActionContext(member=member, resource=resource, target=target, input=body.input,
                        expected_revision=body.expectedRevision)
    try:
        updated = await action.run(ctx)
    except ActionConflict:
        current = await _load(member, resource_id)
        raise HTTPException(status_code=409, detail=await resolve.snapshot(current, member["sub"]))
    except ActionRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ActionFailed:
        logger.warning("widget action %s failed on %s", action.name, resource_id, exc_info=True)
        raise HTTPException(status_code=502, detail="the device did not respond")

    return await resolve.snapshot(updated or resource, member["sub"])


@router.delete(f"{PREFIX}/resources/{{resource_id}}", status_code=204)
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


app.include_router(router)
