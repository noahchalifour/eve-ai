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
    "You control the family's smart home via Home Assistant. Look up a "
    "device's state before changing it if the request is ambiguous. Report "
    "exactly what you changed, in one sentence."
)


def _model_for_test():
    """Indirection so unit tests can substitute a fake model, via
    importlib.reload, without a live LiteLLM call at import time."""
    return get_model(Tier.MECHANICAL)


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
    tools=[get_state, call_service],
    system_prompt=SYSTEM_PROMPT,
    permission="home.control",
    model_factory=lambda _tier: _model_for_test(),
)
