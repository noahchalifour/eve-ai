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


async def invoke(tool: str, arguments: dict, timeout: float = 15.0) -> str:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{settings.tools_base_url}/invoke",
                json={"tool": tool, "arguments": arguments},
                headers={"Authorization": f"Bearer {settings.tools_api_key}"},
            )
            response.raise_for_status()
            body = response.json()

        if "error" in body:
            return f"error: {body['error']}"
        return json.dumps(body["result"])
    except Exception as exc:
        logger.warning("eve-tools call to %r failed", tool, exc_info=True)
        return f"error: eve-tools unavailable ({exc.__class__.__name__})"
