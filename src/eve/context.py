"""The `load_context` node.

Performs NO model call. Everything here is local computation, because the
latency contract (spec section 6.2, ADR 0002) forbids any LLM call ahead of
Eve's first streamed token.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from langchain_core.runnables import RunnableConfig

from eve.family import Member, get_family
from eve.settings import get_settings
from eve.state import EveState, MemberContext


@lru_cache(maxsize=1)
def load_persona() -> str:
    return get_settings().prompt_file.read_text()


def build_member_context(member: Member, now: datetime) -> MemberContext:
    local = now.astimezone(ZoneInfo(member.timezone))
    return MemberContext(
        sub=member.sub,
        name=member.name,
        role=member.role,
        timezone=member.timezone,
        permissions=sorted(member.permissions),
        local_time=local.strftime("%Y-%m-%d %H:%M %Z"),
    )


def build_system_prompt(persona: str, member: MemberContext) -> str:
    return (
        f"{persona}\n\n"
        "## Who you are speaking with\n"
        f"- Name: {member['name']}\n"
        f"- Role in the family: {member['role']}\n"
        f"- Their local time right now: {member['local_time']}\n"
    )


async def load_context(state: EveState, config: RunnableConfig) -> dict:
    # Aegra injects a pydantic `aegra_api.models.auth.User` here, which has no
    # `__getitem__`; the LangGraph SDK's own documentation describes a
    # dict-shaped principal, and the shape our unit tests hand-build. Read it
    # tolerantly so neither shape breaks the graph.
    principal = config["configurable"]["langgraph_auth_user"]
    identity = (
        principal["identity"] if isinstance(principal, Mapping) else principal.identity
    )
    member = get_family().get(identity)
    member_ctx = build_member_context(member, datetime.now(tz=ZoneInfo("UTC")))
    return {
        "member": member_ctx,
        "system_prompt": build_system_prompt(load_persona(), member_ctx),
    }
