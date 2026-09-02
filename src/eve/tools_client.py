"""The one door from Eve's main container to eve-tools. Every call is an HTTP
request with a timeout; failures degrade to a returned error string rather
than a raised exception, because the caller is always a tool whose result
goes straight to a model - a raised exception here would fail the whole
turn instead of letting Eve explain the problem in her own words.
"""

from __future__ import annotations

import json
import logging

import httpx

from eve.settings import get_settings

logger = logging.getLogger(__name__)

_TARGETS = {
    "tools": ("tools_base_url", "tools_api_key"),
    "sandbox": ("sandbox_base_url", "sandbox_api_key"),
}


async def invoke(
    tool: str,
    arguments: dict,
    timeout: float = 15.0,
    *,
    target: str = "tools",
    extra: dict | None = None,
) -> str:
    """One door to eve-tools, and since Phase 5c one to eve-sandbox.

    Two targets rather than two modules: the /invoke contract is identical, and
    so is the failure posture that matters - every failure degrades to a
    returned error string, because the caller is always a tool whose result
    goes straight to a model.
    """
    settings = get_settings()
    url_attr, key_attr = _TARGETS.get(target, _TARGETS["tools"])
    base_url = getattr(settings, url_attr)
    api_key = getattr(settings, key_attr)
    payload = {"tool": tool, "arguments": arguments, **(extra or {})}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/invoke",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            body = response.json()

        if "error" in body:
            return f"error: {body['error']}"
        return json.dumps(body["result"])
    except Exception as exc:
        logger.warning("eve-%s call to %r failed", target, tool, exc_info=True)
        return f"error: eve-{target} unavailable ({exc.__class__.__name__})"


async def dispatch_task(task_id: str, goal: str, timeout: float = 15.0) -> str:
    """POST /tasks on eve-computer. Not routed through `invoke()`: the box's
    task API is a lifecycle (create, poll, fetch an artifact, kill), not the
    {tool, arguments} -> {result|error} shape eve-tools and eve-sandbox
    share, so it gets its own thin wrapper instead of a second meaning for
    `target`."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.computer_base_url}/tasks",
                json={"id": task_id, "goal": goal},
                headers={"Authorization": f"Bearer {settings.computer_api_key}"},
            )
            response.raise_for_status()
        return "ok"
    except Exception as exc:
        logger.warning("eve-computer dispatch failed for %s", task_id, exc_info=True)
        return f"error: eve-computer unavailable ({exc.__class__.__name__})"


async def get_computer_task(task_id: str, timeout: float = 15.0) -> dict | None:
    """GET /tasks/{id} on eve-computer. `None` means the box could not be
    asked at all - down, timed out, or the task id is unknown to it (e.g.
    after a restart, since eve-computer keeps no task state on disk). The
    poller (eve.computer.poller) treats that as "still waiting" until it has
    been true past its own stale timeout."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{settings.computer_base_url}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {settings.computer_api_key}"},
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        logger.warning("eve-computer status check failed for %s", task_id, exc_info=True)
        return None


# --- Coding sessions (EVE-4) ------------------------------------------
#
# Sessions run on eve-computer, so these reuse computer_base_url and
# computer_api_key rather than introducing a fourth door to the same box.
#
# Same failure posture as everything else in this module: a returned error
# string or None, never a raised exception. The callers are a tool whose
# result goes to a model and a supervisor loop that must not die because
# one session's box hiccuped.


async def _session_request(
    method: str, path: str, *, json_body: dict | None = None,
    params: dict | None = None, timeout: float = 15.0,
) -> dict | None:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                f"{settings.computer_base_url}{path}",
                json=json_body,
                params=params,
                headers={"Authorization": f"Bearer {settings.computer_api_key}"},
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        logger.warning("eve-computer session call %s %s failed", method, path, exc_info=True)
        return None


async def create_coding_session(
    session_id: str, agent: str, model: str, repos: list[str], prompt: str
) -> str:
    body = await _session_request(
        "POST", "/sessions",
        json_body={"id": session_id, "agent": agent, "model": model,
                   "repos": repos, "prompt": prompt},
    )
    return "ok" if body is not None else "error: eve-computer unavailable"


async def get_coding_session(session_id: str, since: int = 0) -> dict | None:
    return await _session_request(
        "GET", f"/sessions/{session_id}", params={"since": since}
    )


async def prompt_coding_session(session_id: str, text: str, kind: str = "reply") -> str:
    body = await _session_request(
        "POST", f"/sessions/{session_id}/prompt",
        json_body={"text": text, "kind": kind},
    )
    return "ok" if body is not None else "error: eve-computer unavailable"


async def close_coding_session(session_id: str) -> dict | None:
    # Longer than the default: closing pushes branches and opens a pull
    # request per repo, which is several network round trips to GitHub.
    return await _session_request("POST", f"/sessions/{session_id}/close", timeout=120.0)


async def kill_coding_session(session_id: str) -> str:
    body = await _session_request("DELETE", f"/sessions/{session_id}")
    return "ok" if body is not None else "error: eve-computer unavailable"
