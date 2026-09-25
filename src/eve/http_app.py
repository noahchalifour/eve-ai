"""The one custom HTTP app Aegra mounts.

`aegra.json`'s `http.app` takes a single app, and this deployment now serves
two resource APIs (widgets and routines). This module is that single app and
nothing else: it owns no routes of its own, so a route's auth boundary and
its handler stay in the same file as each other.

The exception handler is registered HERE rather than on either feature's
module, because it is an app-level concern: a 409 carries a whole snapshot
rather than a message, and returning it nested under `detail` would make a
client unwrap conflicts differently from every other response.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from eve.images.app import router as images_router
from eve.routines.app import router as routines_router
from eve.widgets.app import router as widgets_router

app = FastAPI(title="eve-provider-resources")


@app.exception_handler(HTTPException)
async def _flatten(request: Request, exc: HTTPException) -> JSONResponse:
    if exc.status_code == 409 and isinstance(exc.detail, dict):
        return JSONResponse(status_code=409, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


app.include_router(widgets_router)
app.include_router(routines_router)
app.include_router(images_router)
