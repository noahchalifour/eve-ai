"""eve-computer's own HTTP surface: a task queue with one worker, because
one machine has one X display and one mouse (design doc: "One task at a
time, queued"). Same bearer-token auth shape as eve-tools and eve-sandbox.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from eve_computer import store
from eve_computer.harness import run_task
from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

_queue: asyncio.Queue | None = None
_worker: asyncio.Task | None = None
_inflight: dict[str, asyncio.Task] = {}


def _check_auth(authorization: str | None) -> None:
    settings = get_computer_settings()
    if not settings.api_key or authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=401, detail="unauthorized")


async def _work_forever(queue: asyncio.Queue) -> None:
    while True:
        task_id = await queue.get()
        task = await store.get(task_id)
        if task is None or task.status == "killed":
            queue.task_done()
            continue
        await store.set_status(task_id, "running")
        runner = asyncio.ensure_future(run_task(task_id, task.goal))
        _inflight[task_id] = runner
        try:
            result = await runner
            status = "failed" if result.get("error") else "finished"
        except asyncio.CancelledError:
            status, result = "killed", {"error": "killed"}
        except Exception as exc:
            logger.warning("task %s raised", task_id, exc_info=True)
            status, result = "failed", {"error": f"{exc.__class__.__name__}: {exc}"}
        finally:
            _inflight.pop(task_id, None)
        await store.set_result(task_id, status, result)
        queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue, _worker
    _queue = asyncio.Queue()
    _worker = asyncio.create_task(_work_forever(_queue))
    yield
    _worker.cancel()
    try:
        await _worker
    except asyncio.CancelledError:
        pass


app = FastAPI(title="eve-computer", lifespan=lifespan)


class TaskRequest(BaseModel):
    id: str
    goal: str


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/tasks", status_code=202)
async def create_task_route(
    body: TaskRequest, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    await store.create(body.id, body.goal)
    assert _queue is not None
    await _queue.put(body.id)
    return {"id": body.id, "status": "queued"}


@app.get("/tasks/{task_id}")
async def get_task_route(
    task_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    out_dir = Path(get_computer_settings().tasks_dir) / task_id / "out"
    artifacts = (
        sorted(p.name for p in out_dir.glob("*") if p.is_file()) if out_dir.is_dir() else []
    )
    return {"status": task.status, "result": task.result, "artifacts": artifacts}


@app.get("/tasks/{task_id}/artifacts/{name}")
async def get_artifact_route(
    task_id: str, name: str, authorization: str | None = Header(default=None)
):
    _check_auth(authorization)
    if name in ("..", ".") or "/" in name or "\\" in name:
        raise HTTPException(status_code=404, detail="unknown artifact")
    path = Path(get_computer_settings().tasks_dir) / task_id / "out" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="unknown artifact")
    return FileResponse(path)


@app.delete("/tasks/{task_id}")
async def delete_task_route(
    task_id: str, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="unknown task")
    runner = _inflight.get(task_id)
    if runner is not None:
        runner.cancel()
    else:
        await store.set_status(task_id, "killed")
    return {"id": task_id, "status": "killed"}
