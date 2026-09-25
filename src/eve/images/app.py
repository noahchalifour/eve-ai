"""`POST /images` and `GET /images/{id}` - Eve is the image proxy by
construction (spec 2.3, 4.1). The phone only ever fetches its own Eve base
URL with the bearer it already sends; it never learns an Immich URL and never
fetches a model-chosen one.

Owner-only reads. Household sharing is a one-line change to the `get` call
below if the family ever wants it (spec 8).
"""

from __future__ import annotations

from datetime import UTC, datetime

from aegra_api.core.auth_deps import require_auth
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, UploadFile
from starlette.concurrency import run_in_threadpool

from eve.images import store
from eve.images.process import normalise
from eve.settings import get_settings

router = APIRouter(dependencies=[Depends(require_auth)])


def current_member(request: Request) -> dict:
    """Same contract as `eve.routines.app.current_member`; overridden in tests."""
    user = request.scope.get("user")
    if user is None or not getattr(user, "identity", None):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"sub": user.identity, "permissions": list(getattr(user, "permissions", []) or [])}


@router.post("/images")
async def upload(
    file: UploadFile,
    thread_id: str | None = Form(default=None),
    member: dict = Depends(current_member),
) -> dict:
    limit = get_settings().image_max_upload_bytes
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail="image too large")
    try:
        # Decoding a 12 MP photo is ~100 ms of CPU; off the event loop so
        # one upload does not stall every streaming turn on this pod.
        image = await run_in_threadpool(normalise, raw)
    except ValueError:
        raise HTTPException(status_code=415, detail="not an image") from None
    row = await store.put(member["sub"], image, origin="upload", thread_id=thread_id)
    return {"image_id": row.id, "width": row.width, "height": row.height}


@router.get("/images/{image_id}")
async def download(image_id: str, member: dict = Depends(current_member)) -> Response:
    row = await store.get(image_id, member["sub"], include_expired=True)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.expires_at <= datetime.now(UTC):
        # Distinct from 404 so the phone can say "no longer available" rather
        # than "something is broken" (spec 4.3).
        raise HTTPException(status_code=410, detail="expired")
    return Response(
        content=row.bytes,
        media_type=row.content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
