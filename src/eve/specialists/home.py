"""Home specialist: Home Assistant device control via eve-tools. The specialist
never talks to Home Assistant directly - every tool call here is a thin HTTP
relay (design doc section 4, section 7).
"""

from __future__ import annotations

from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.tools_client import invoke

SYSTEM_PROMPT = (
    "You control the family's smart home via Home Assistant. You do not know "
    "which entities exist: call list_entities first whenever you need to find "
    "or count devices, and only pass an entity_id to get_state or "
    "call_service once you have seen it in that list. Look up a device's "
    "state before changing it if the request is ambiguous. Report exactly "
    "what you found or changed, in one sentence."
)


def _model_for_test():
    """Indirection so unit tests can substitute a fake model, via
    importlib.reload, without a live LiteLLM call at import time."""
    return get_model(Tier.MECHANICAL)


@tool
async def list_entities(domain: str | None = None) -> str:
    """List the home's entities with their current states, optionally limited
    to one domain, e.g. domain='light'. Call this before get_state whenever
    you do not already know the exact entity_id - guessing ids wastes the
    whole request."""
    return await invoke("home.list_entities", {"domain": domain})


@tool
async def get_state(entity_id: str) -> str:
    """Read the current state of a Home Assistant entity, e.g. 'light.kitchen'."""
    return await invoke("home.get_state", {"entity_id": entity_id})


@tool
async def call_service(
    domain: str, service: str, entity_id: str, data: dict | None = None
) -> str:
    """Call a Home Assistant service, e.g. domain='light', service='turn_on'."""
    return await invoke(
        "home.call_service",
        {
            "domain": domain,
            "service": service,
            "entity_id": entity_id,
            "data": data or {},
        },
    )


ask_home = build_specialist(
    name="home",
    tools=[list_entities, get_state, call_service],
    system_prompt=SYSTEM_PROMPT,
    permission="home.control",
    model_factory=lambda _tier: _model_for_test(),
)
