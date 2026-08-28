"""The eve-sandbox HTTP surface: one route that runs one pure function.

Same contract shape as eve-tools' /invoke, so eve.tools_client works against
it with one added parameter. Holds no credential beyond the shared bearer
token that authenticates Eve to it.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from eve_sandbox.execute import run_tool
from eve_sandbox.settings import get_sandbox_settings

app = FastAPI(title="eve-sandbox")

# Bounded here rather than by the process count: four concurrent subprocesses
# at 256 MiB each is the memory ceiling this pod is sized for.
_semaphore: asyncio.Semaphore | None = None


def _gate() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_sandbox_settings().max_concurrency)
    return _semaphore


class InvokeBody(BaseModel):
    tool: str
    arguments: dict = {}
    source: str
    source_sha256: str


def _check_auth(authorization: str | None) -> None:
    settings = get_sandbox_settings()
    if not settings.api_key or authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/invoke")
async def invoke(
    body: InvokeBody, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    async with _gate():
        return await run_tool(body.source, body.source_sha256, body.arguments)
