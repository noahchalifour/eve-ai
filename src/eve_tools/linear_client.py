"""The only holder of the Linear API token.

ADR 0006: third-party credentials live in exactly one service. EVE-26 is the
first time a third party initiates contact with Eve rather than being polled,
and the rule survives it because verification and action are separable. The
webhook signing secret (which proves Linear reached us) lives in eve-ambient;
this token (which reaches Linear) lives only here.

GRAPHQL ANSWERS 200 WITH AN ERRORS ARRAY. Checking the HTTP status alone
would report a rejected mutation as a delivered activity, and the caller
would stamp a heartbeat for something Linear never received.

EVE-41: the app token is a client_credentials grant that expires after 30
days with no refresh token. When the app's client id/secret are configured,
an authentication failure mints a fresh token from the same grant
scripts/linear_client_credentials_refresh.py uses and retries the call once.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

_API_URL = "https://api.linear.app/graphql"
_TIMEOUT = 15.0
_TOKEN_URL = "https://api.linear.app/oauth/token"
# Must match scripts/linear_client_credentials_refresh.py: a minted token
# with fewer scopes would pass the retry and then fail the mutations.
_SCOPE = "read,write,app:assignable"

# A minted token lives only in this process. It is never written back to
# Vault: that stays the refresh script's job, and a restart simply mints
# again on the first 401.
_minted_token: str | None = None
# Concurrent calls that all hit the same expired token should mint once,
# not once each.
_refresh_lock = asyncio.Lock()

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

_ISSUE_UPDATE_DELEGATE = """
mutation IssueSetDelegate($id: String!, $delegateId: String!) {
  issueUpdate(id: $id, input: { delegateId: $delegateId }) { success }
}
"""


class LinearError(Exception):
    """Linear refused, or is unreachable, or is not configured."""


def _is_auth_failure(response, body: dict) -> bool:
    """Linear signals an expired token either as HTTP 401 or as a 200 whose
    GraphQL errors carry an authentication code."""
    if response.status_code == 401:
        return True
    for error in body.get("errors") or []:
        extensions = error.get("extensions") or {}
        if extensions.get("code") == "AUTHENTICATION_ERROR":
            return True
        if "authentication" in str(extensions.get("type", "")).lower():
            return True
    return False


async def _post(client, query: str, variables: dict, token: str):
    response = await client.post(
        _API_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = response.json() if response.status_code < 400 else {}
    return response, body


async def _refresh(client, stale_token: str, client_id: str, client_secret: str) -> str:
    global _minted_token
    async with _refresh_lock:
        # Another call may have minted while this one waited on the lock.
        if _minted_token and _minted_token != stale_token:
            return _minted_token
        try:
            response = await client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "scope": _SCOPE,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise LinearError(f"could not refresh the Linear token: {exc}") from exc
        if response.status_code >= 400:
            raise LinearError(
                f"could not refresh the Linear token: HTTP {response.status_code}"
            )
        access_token = response.json().get("access_token")
        if not access_token:
            raise LinearError("could not refresh the Linear token: no access_token")
        logger.info("minted a fresh Linear app token after an authentication failure")
        _minted_token = access_token
        return access_token


async def _call(query: str, variables: dict) -> dict:
    settings = get_tools_settings()
    client_id, client_secret = settings.linear_client_id, settings.linear_client_secret
    can_refresh = bool(client_id and client_secret)
    token = _minted_token or settings.linear_api_token
    if not token and not can_refresh:
        raise LinearError("the Linear API token is not configured")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        if not token:
            token = await _refresh(client, "", client_id, client_secret)
        response, body = await _post(client, query, variables, token)
        if can_refresh and _is_auth_failure(response, body):
            token = await _refresh(client, token, client_id, client_secret)
            response, body = await _post(client, query, variables, token)
            # Exactly one retry: a freshly minted token being rejected means
            # the app itself is misconfigured, and minting again won't help.
            if _is_auth_failure(response, body):
                raise LinearError("Linear rejected a freshly minted token")
        response.raise_for_status()
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


async def set_delegate(issue_id: str, actor_id: str) -> dict:
    data = await _call(
        _ISSUE_UPDATE_DELEGATE, {"id": issue_id, "delegateId": actor_id}
    )
    return data.get("issueUpdate") or {"success": False}
