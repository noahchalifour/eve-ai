"""The dashboard resource API, beside the widget one.

Same boundary as `eve.widgets.app`: authentication is Aegra's (the router
depends on `require_auth`), authorization is ours, and every query is scoped
by the authenticated member. A 404 on GET is the normal "no dashboard for
this device yet" answer, which a client turns into onboarding; it is never
"unsupported" (the capability probe is what says that).

Building a dashboard is not a route: it spends a model call, so it is a
stateless graph run (`eve.dashboards.setup`). These routes are the
model-free rest: read, rearrange, reset.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from aegra_api.core.auth_deps import require_auth

from eve.dashboards import layout as grid
from eve.dashboards import store
from eve.widgets import store as widgets
from eve.widgets.app import PREFIX, current_member

router = APIRouter(dependencies=[Depends(require_auth)])


class LayoutRequest(BaseModel):
    layout: list[dict]
    expectedRevision: int


def _device(device_id: str) -> str:
    if not grid.valid_device_id(device_id):
        raise HTTPException(status_code=400, detail="invalid device id")
    return device_id


async def _view(member_sub: str, row: dict) -> dict:
    """The dashboard as the client renders it. A tile whose widget left the
    library is dropped here, on read, rather than by a cascade."""
    library = {w["id"]: w for w in await widgets.list_for(member_sub)}
    tiles = [
        {**entry, "title": library[entry["resourceId"]]["title"], "kind": library[entry["resourceId"]]["kind"]}
        for entry in row["layout"]
        if entry.get("resourceId") in library
    ]
    return {
        "deviceId": row["device_id"],
        "purpose": row["purpose"],
        "columns": row["columns"],
        "revision": row["revision"],
        "tiles": tiles,
    }


async def _load(member_sub: str, device_id: str) -> dict:
    row = await store.get(member_sub, device_id)
    if row is None:
        raise HTTPException(status_code=404, detail="no dashboard")
    return row


@router.get(f"{PREFIX}/dashboards/{{device_id}}")
async def get_dashboard(device_id: str, member: dict = Depends(current_member)) -> dict:
    row = await _load(member["sub"], _device(device_id))
    return await _view(member["sub"], row)


@router.put(f"{PREFIX}/dashboards/{{device_id}}/layout")
async def put_layout(
    device_id: str, body: LayoutRequest, member: dict = Depends(current_member)
) -> dict:
    sub = member["sub"]
    row = await _load(sub, _device(device_id))
    # Validated against the stored layout, not the request's own claims: the
    # allowed sizes and the set of tiles are the server's, never the client's.
    normalised = grid.validate(body.layout, row["layout"], row["columns"])
    if isinstance(normalised, str):
        raise HTTPException(status_code=400, detail=normalised)
    updated = await store.update_layout(sub, device_id, normalised, body.expectedRevision)
    if updated is None:
        # Stale revision (or the dashboard vanished mid-request): hand back
        # the truth so the client adopts it rather than reporting a conflict.
        current = await _load(sub, device_id)
        raise HTTPException(status_code=409, detail=await _view(sub, current))
    return await _view(sub, updated)


@router.delete(f"{PREFIX}/dashboards/{{device_id}}", status_code=204)
async def delete_dashboard(device_id: str, member: dict = Depends(current_member)) -> None:
    """Resets this device's dashboard. Its widgets stay in the library."""
    if not await store.delete(member["sub"], _device(device_id)):
        raise HTTPException(status_code=404, detail="no dashboard")
