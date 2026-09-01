"""Health coach specialist: WHOOP and Oura via eve-tools. Read-only - neither
provider is written to, and nothing has asked for it (design doc section 8).

Per-member, so every tool passes `member_sub` across the eve-tools boundary
the way `mail.py` does. ADR 0016 records that this makes health the second
domain to do so; the subs stay opaque and eve-tools still learns no names,
roles, timezones, or permissions.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from eve.models import Tier, get_model
from eve.specialists.base import build_specialist
from eve.tools_client import invoke

SYSTEM_PROMPT = (
    "You are the family's health coach. You answer questions about sleep, "
    "recovery, and training load using WHOOP and Oura data.\n\n"
    "State every number exactly as returned; never estimate or interpolate. "
    "A null field means that device does not measure it - say so rather than "
    "treating it as zero. An empty recovery result early in the morning "
    "means last night's sleep has not been scored yet, which is normal; say "
    "that rather than reporting a problem. If a member has two devices, "
    "report both rather than choosing between them.\n\n"
    "You give practical guidance on training, rest, and sleep habits "
    "grounded in these numbers. You do not diagnose, interpret symptoms, or "
    "give medical advice - if a question touches illness, injury, "
    "medication, or anything clinical, say it needs a doctor."
)


def _model_for_test():
    return get_model(Tier.MECHANICAL)


# `config` precedes `days` because `days` carries a default and Python forbids
# a non-defaulted parameter after one. `@tool` excludes RunnableConfig-
# annotated parameters from the tool schema, so position does not affect what
# the model sees.
@tool
async def get_recovery(config: RunnableConfig, days: int = 1) -> str:
    """Recovery score, HRV, and resting heart rate for recent days."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke(
        "health.get_recovery", {"member_sub": member_sub, "days": days}
    )


@tool
async def get_sleep(config: RunnableConfig, days: int = 1) -> str:
    """Sleep duration, stages, and efficiency for recent nights."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke("health.get_sleep", {"member_sub": member_sub, "days": days})


@tool
async def get_activity(config: RunnableConfig, days: int = 1) -> str:
    """Training load, calories, steps, and workouts for recent days."""
    member_sub = config["configurable"]["member"]["sub"]
    return await invoke(
        "health.get_activity", {"member_sub": member_sub, "days": days}
    )


# No per-tool permission check: all three are reads, so unlike mail.send there
# is nothing to gate beyond the coarse ask_health boundary.
ask_health = build_specialist(
    name="health",
    tools=[get_recovery, get_sleep, get_activity],
    system_prompt=SYSTEM_PROMPT,
    permission="health",
    model_factory=lambda _tier: _model_for_test(),
)
