"""Mail specialist: Gmail via eve-tools. `send_email` is gated separately from
the read tools (design doc section 4) - the coarse ask_mail check requires
mail.read OR mail.send, but sending additionally requires mail.send.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.specialists.permissions import permission_denial
from eve.tools_client import invoke

SYSTEM_PROMPT = (
    "You manage the requesting family member's Gmail. Summarise before "
    "quoting a whole thread. Never send an email without the exact "
    "recipient, subject, and body the request specified or clearly implied."
)


def _model_for_test():
    return get_model(Tier.MECHANICAL)


@tool
async def list_messages(query: str, config: RunnableConfig) -> str:
    """Search Gmail. Gmail query syntax, e.g. 'is:unread from:school'."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke("mail.list_messages", {"member_sub": member_sub, "query": query})


@tool
async def get_thread(thread_id: str, config: RunnableConfig) -> str:
    """Read a full Gmail thread by id."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke(
        "mail.get_thread", {"member_sub": member_sub, "thread_id": thread_id}
    )


@tool
async def send_email(to: str, subject: str, body: str, config: RunnableConfig) -> str:
    """Send an email. Requires the mail.send permission."""
    member = config["configurable"]["member"]
    denial = permission_denial(member.get("permissions", []), "mail.send")
    if denial:
        return denial
    return await invoke(
        "mail.send_email",
        {"member_sub": member["sub"], "to": to, "subject": subject, "body": body},
    )


ask_mail = build_specialist(
    name="mail",
    tools=[list_messages, get_thread, send_email],
    system_prompt=SYSTEM_PROMPT,
    permission=["mail.read", "mail.send"],
    model_factory=lambda _tier: _model_for_test(),
)
