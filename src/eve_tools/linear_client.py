"""The only holder of the Linear API token.

ADR 0006: third-party credentials live in exactly one service. EVE-26 is the
first time a third party initiates contact with Eve rather than being polled,
and the rule survives it because verification and action are separable. The
webhook signing secret (which proves Linear reached us) lives in eve-ambient;
this token (which reaches Linear) lives only here.

GRAPHQL ANSWERS 200 WITH AN ERRORS ARRAY. Checking the HTTP status alone
would report a rejected mutation as a delivered activity, and the caller
would stamp a heartbeat for something Linear never received.
"""

from __future__ import annotations

import logging

import httpx

from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.linear.app/graphql"
_TIMEOUT = 15.0

_CREATE_ACTIVITY = """
mutation AgentActivityCreate($input: AgentActivityCreateInput!) {
  agentActivityCreate(input: $input) { success }
}
"""

_STARTED_STATES = """
query TeamStartedStatuses($teamId: String!) {
  team(id: $teamId) {
    states(filter: { type: { eq: "started" } }) {
      nodes { id name position }
    }
  }
}
"""

_ISSUE_UPDATE_STATE = """
mutation IssueUpdate($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) { success }
}
"""

_ISSUE_UPDATE_DELEGATE = """
mutation IssueSetDelegate($id: String!, $delegateId: String!) {
  issueUpdate(id: $id, input: { delegateId: $delegateId }) { success }
}
"""


class LinearError(Exception):
    """Linear refused, or is unreachable, or is not configured."""


async def _call(query: str, variables: dict) -> dict:
    token = get_tools_settings().linear_api_token
    if not token:
        raise LinearError("the Linear API token is not configured")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        response = await client.post(
            _API_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        body = response.json()
    if body.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in body["errors"])
        raise LinearError(messages)
    return body.get("data") or {}


async def create_activity(session_id: str, content: dict) -> dict:
    data = await _call(
        _CREATE_ACTIVITY,
        {"input": {"agentSessionId": session_id, "content": content}},
    )
    return data.get("agentActivityCreate") or {"success": False}


async def move_issue_to_started(issue_id: str, team_id: str) -> dict:
    """The team's lowest-position `started` state, which is what Linear's
    best practices specify. A team with no started state is a no-op rather
    than an error: the delegation is still valid, the board just has an
    unusual workflow."""
    data = await _call(_STARTED_STATES, {"teamId": team_id})
    nodes = ((data.get("team") or {}).get("states") or {}).get("nodes") or []
    if not nodes:
        return {"success": False, "reason": "no started state"}
    target = min(nodes, key=lambda n: n.get("position", 0))
    updated = await _call(
        _ISSUE_UPDATE_STATE, {"id": issue_id, "stateId": target["id"]}
    )
    return updated.get("issueUpdate") or {"success": False}


async def set_delegate(issue_id: str, actor_id: str) -> dict:
    data = await _call(
        _ISSUE_UPDATE_DELEGATE, {"id": issue_id, "delegateId": actor_id}
    )
    return data.get("issueUpdate") or {"success": False}
