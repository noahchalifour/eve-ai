# tests/test_widgets_actions.py
from __future__ import annotations

import json

import pytest


def test_toggle_risk_depends_on_the_domain():
    from eve.widgets.actions import REGISTRY

    toggle = REGISTRY["home.toggle"]
    assert toggle.risk_for("light.kitchen") == "safe"
    assert toggle.risk_for("lock.front_door") == "confirm"
    assert toggle.risk_for("cover.garage") == "confirm"
    assert toggle.risk_for("sensor.outside") is None  # not toggleable at all


def test_preset_toggle_domains_match_the_action():
    from eve.widgets import presets
    from eve.widgets.actions.home import TOGGLE_RISK

    assert presets.TOGGLE_DOMAINS == set(TOGGLE_RISK)


def test_media_actions_only_accept_media_players():
    from eve.widgets.actions import accepts

    assert accepts("home.media.next", "media_player.lr")
    assert not accepts("home.media.next", "light.kitchen")
    assert not accepts("home.nope", "light.kitchen")


@pytest.fixture
def calls(monkeypatch):
    from eve.widgets.actions import home

    seen = []

    async def fake_invoke(tool, arguments):
        seen.append((tool, arguments))
        if tool == "home.get_state":
            return json.dumps({"entity_id": arguments["entity_id"], "state": "locked"})
        return json.dumps({"called": True})

    monkeypatch.setattr(home, "invoke", fake_invoke)
    return seen


def _ctx(target):
    from eve.widgets.actions.base import ActionContext

    return ActionContext(member={"sub": "s", "permissions": ["home.control"]}, resource={}, target=target,
                         input={"target": target}, expected_revision=1)


async def test_toggling_a_light_calls_its_domain_toggle(calls):
    from eve.widgets.actions import REGISTRY

    await REGISTRY["home.toggle"].run(_ctx("light.kitchen"))
    assert calls == [("home.call_service", {"domain": "light", "service": "toggle", "entity_id": "light.kitchen", "data": {}})]


async def test_toggling_a_lock_reads_state_then_unlocks(calls):
    """HA has no lock.toggle; the action decides from the current state."""
    from eve.widgets.actions import REGISTRY

    await REGISTRY["home.toggle"].run(_ctx("lock.front_door"))
    assert calls[-1][1]["service"] == "unlock"


async def test_an_upstream_failure_is_action_failed(monkeypatch):
    from eve.widgets.actions import REGISTRY, home
    from eve.widgets.actions.base import ActionFailed

    async def down(tool, arguments):
        return "error: eve-tools unavailable (ConnectError)"

    monkeypatch.setattr(home, "invoke", down)
    with pytest.raises(ActionFailed):
        await REGISTRY["home.toggle"].run(_ctx("light.kitchen"))
