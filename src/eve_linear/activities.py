"""What Eve says in Linear, and the one wrapper that says it.

FIVE TYPES, VALIDATED SERVER-SIDE. Linear rejects a malformed content shape,
so the builders exist to make the shapes unmistakable at the call site rather
than assembled inline in the supervisor.

EMIT NEVER RAISES. Every caller is either the webhook's background task or
the supervisor tick, and both are driving real work. Losing the narration is
bad; failing a running coding session because a GraphQL call timed out is
worse, and it destroys work someone is waiting on. This is the same posture
`tools_client.invoke` already takes for tool calls.
"""

from __future__ import annotations

import logging

from eve.coding import store
from eve.tools_client import invoke

logger = logging.getLogger(__name__)


def thought(body: str) -> dict:
    return {"type": "thought", "body": body}


def elicitation(body: str) -> dict:
    return {"type": "elicitation", "body": body}


def response(body: str) -> dict:
    return {"type": "response", "body": body}


def error(body: str) -> dict:
    return {"type": "error", "body": body}


def action(action: str, parameter: str, result: str | None = None) -> dict:
    """`result` is omitted entirely when absent, not set to null: a started
    action and a completed one with no output are different things to
    Linear."""
    content = {"type": "action", "action": action, "parameter": parameter}
    if result is not None:
        content["result"] = result
    return content


async def emit(
    linear_session_id: str, content: dict, session_id: str | None = None
) -> bool:
    """True when Linear accepted it. `session_id` is Eve's own row id, and
    passing it stamps the heartbeat clock - only on success, so a failed
    emission does not make the heartbeat believe Linear heard from us."""
    try:
        result = await invoke(
            "linear.create_activity",
            {"session_id": linear_session_id, "content": content},
        )
    except Exception:
        logger.warning(
            "emitting a %s to linear session %s raised",
            content.get("type"),
            linear_session_id,
            exc_info=True,
        )
        return False

    if isinstance(result, str) and result.startswith("error:"):
        logger.warning(
            "could not emit a %s to linear session %s: %s",
            content.get("type"),
            linear_session_id,
            result,
        )
        return False

    if session_id:
        try:
            await store.touch_linear_emitted(session_id)
        except Exception:
            # The activity did land. Failing here would report a delivered
            # activity as lost, and the caller would retry a duplicate.
            logger.warning(
                "could not stamp the linear heartbeat for %s", session_id, exc_info=True
            )
    return True
