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
