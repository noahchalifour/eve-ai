# src/eve/widgets/actions/home.py
"""Home Assistant actions. Adding one = one `register` call below."""
from __future__ import annotations

import json

from eve.tools_client import invoke
from eve.widgets.actions.base import ActionContext, ActionFailed, ActionType, Risk, register

# Everyday switches are one tap. Anything that opens the house asks first.
TOGGLE_RISK: dict[str, Risk] = {
    "light": "safe", "switch": "safe", "fan": "safe", "input_boolean": "safe",
    "lock": "confirm", "cover": "confirm",
}


def _domain(target: str | None) -> str:
    return (target or "").split(".", 1)[0]


async def _call(tool: str, arguments: dict) -> dict:
    raw = await invoke(tool, arguments)
    if raw.startswith("error:"):
        raise ActionFailed(f"{tool} failed")
    return json.loads(raw)


async def _service(domain: str, service: str, entity_id: str) -> None:
    await _call("home.call_service", {"domain": domain, "service": service, "entity_id": entity_id, "data": {}})


async def _toggle(ctx: ActionContext) -> None:
    domain = _domain(ctx.target)
    if domain == "lock":
        state = (await _call("home.get_state", {"entity_id": ctx.target})).get("state")
        await _service("lock", "unlock" if state == "locked" else "lock", ctx.target)
    else:
        await _service(domain, "toggle", ctx.target)
    return None


register(ActionType(
    name="home.toggle", label="Toggle", run=_toggle,
    risk_for=lambda target: TOGGLE_RISK.get(_domain(target)),
    default_risk="safe", permission="home.control",
))


def _media(name: str, service: str, label: str) -> None:
    async def run(ctx: ActionContext) -> None:
        await _service("media_player", service, ctx.target)

    register(ActionType(
        name=name, label=label, run=run,
        risk_for=lambda target: "safe" if _domain(target) == "media_player" else None,
        default_risk="safe", permission="home.control",
    ))


_media("home.media.play_pause", "media_play_pause", "Play or pause")
_media("home.media.next", "media_next_track", "Next track")
_media("home.media.previous", "media_previous_track", "Previous track")
_media("home.media.volume_up", "volume_up", "Louder")
_media("home.media.volume_down", "volume_down", "Quieter")
