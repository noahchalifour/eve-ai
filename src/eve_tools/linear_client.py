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

# Through the issue rather than by team id: the coding-session row stores the
# issue id but not the team, and one query here is cheaper than a migration.
_ISSUE_TEAM_STARTED_STATES = """
query IssueTeamStartedStatuses($issueId: String!) {
  issue(id: $issueId) {
    team {
      states(filter: { type: { eq: "started" } }) {
        nodes { id name position }
      }
    }
  }
}
"""

_ATTACH_URL = """
mutation AttachmentLinkURL($issueId: String!, $url: String!) {
  attachmentLinkURL(issueId: $issueId, url: $url) { success }
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
    """Linear can answer 200 with no `errors` array and a payload shaped
    `{"success": false}`: a rejected mutation with no error detail. `_call`
    only catches the `errors`-array shape, so this checks the mutation's own
    `success` field and raises on it too. Without this, a rejected activity
    is reported all the way up as delivered, and the caller stamps a
    heartbeat for something Linear never recorded."""
    data = await _call(
        _CREATE_ACTIVITY,
        {"input": {"agentSessionId": session_id, "content": content}},
    )
    result = data.get("agentActivityCreate") or {"success": False}
    if not result.get("success"):
        raise LinearError("agentActivityCreate returned success: false")
    return result


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


async def move_issue_to_review(issue_id: str, pr_urls: list[str]) -> dict:
    """The team has two `started` states (In Progress and In Review), so
    position cannot pick one: match by name. A team without an In Review
    state is a no-op for the same reason `move_issue_to_started` tolerates a
    missing started state.

    Attaching the PRs is best effort. The state move is what the board
    reads; a PR that only appears in the activity text is still reachable."""
    data = await _call(_ISSUE_TEAM_STARTED_STATES, {"issueId": issue_id})
    team = (data.get("issue") or {}).get("team") or {}
    nodes = (team.get("states") or {}).get("nodes") or []
    target = next(
        (n for n in nodes if (n.get("name") or "").strip().lower() == "in review"),
        None,
    )
    if target is None:
        return {"success": False, "reason": "no In Review state"}
    updated = await _call(
        _ISSUE_UPDATE_STATE, {"id": issue_id, "stateId": target["id"]}
    )
    for url in pr_urls:
        try:
            await _call(_ATTACH_URL, {"issueId": issue_id, "url": url})
        except Exception:
            logger.warning("could not attach %s to issue %s", url, issue_id, exc_info=True)
    return updated.get("issueUpdate") or {"success": False}
