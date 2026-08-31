"""The only third-party-credentialed HTTP surface in the deployment. One route
dispatches by a namespaced tool name; anything not in the table 404s rather
than growing a big if/elif. Every handler's exception becomes {"error": ...}
with a 200 - the caller (eve.tools_client.invoke) already treats a non-2xx
response as a transport failure, and an upstream API error is a normal,
expected outcome here, not a transport failure.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from eve_tools import caldav_client, gmail, home_assistant, mcp_dispatch, monarch
from eve_tools.settings import get_tools_settings

app = FastAPI()


class InvokeRequest(BaseModel):
    tool: str
    arguments: dict


_HANDLERS = {
    "home.list_entities": lambda a: home_assistant.list_entities(a.get("domain")),
    "home.get_state": lambda a: home_assistant.get_state(a["entity_id"]),
    "home.call_service": lambda a: home_assistant.call_service(
        a["domain"], a["service"], a["entity_id"], a.get("data") or {}
    ),
    "home.weather": lambda a: home_assistant.weather(a.get("entity_id")),
    "calendar.list_events": lambda a: caldav_client.list_events(
        a["member_sub"], a.get("lookahead_minutes", 90), a.get("horizon_days", 14)
    ),
    "mail.list_messages": lambda a: gmail.list_messages(a["member_sub"], a["query"]),
    "mail.get_thread": lambda a: gmail.get_thread(a["member_sub"], a["thread_id"]),
    "mail.send_email": lambda a: gmail.send_email(
        a["member_sub"], a["to"], a["subject"], a["body"]
    ),
    "finances.list_transactions": lambda a: monarch.list_transactions(
        a.get("limit", 20), a.get("category")
    ),
    "finances.get_budgets": lambda a: monarch.get_budgets(),
    "mcp.invoke": lambda a: mcp_dispatch.invoke(
        a["server_id"], a["tool_name"], a["arguments"]
    ),
}


def _check_auth(authorization: str | None) -> None:
    settings = get_tools_settings()
    if not settings.api_key or authorization != f"Bearer {settings.api_key}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/invoke")
async def invoke_tool(
    body: InvokeRequest, authorization: str | None = Header(default=None)
) -> dict:
    _check_auth(authorization)
    handler = _HANDLERS.get(body.tool)
    if handler is None:
        raise HTTPException(status_code=404, detail=f"unknown tool {body.tool!r}")
    try:
        result = await handler(body.arguments)
    except Exception as exc:
        return {"error": str(exc)}
    return {"result": result}
