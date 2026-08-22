"""Finances specialist: Monarch Money via eve-tools. Read-only - Monarch's
write surface (categorising, flagging) is deferred to when a concrete need
for it shows up (design doc section 4).
"""

from __future__ import annotations

from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.tools_client import invoke

SYSTEM_PROMPT = (
    "You answer questions about the family's finances using Monarch Money "
    "data. State dollar amounts exactly as returned; never estimate."
)


def _model_for_test():
    return get_model(Tier.MECHANICAL)


@tool
async def list_transactions(limit: int = 20, category: str | None = None) -> str:
    """List recent transactions, optionally filtered by category."""
    return await invoke(
        "finances.list_transactions", {"limit": limit, "category": category}
    )


@tool
async def get_budgets() -> str:
    """Read current budget and cash-flow summary."""
    return await invoke("finances.get_budgets", {})


ask_finances = build_specialist(
    name="finances",
    tools=[list_transactions, get_budgets],
    system_prompt=SYSTEM_PROMPT,
    permission="finances",
    model_factory=lambda _tier: _model_for_test(),
)
