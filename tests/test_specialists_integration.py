"""Integration test exercising the real HTTP boundary between a specialist
and a real (locally-run) eve-tools process, itself talking to a stub Home
Assistant server. Only `ask_home`'s own model call is faked (Task 5's
`FakeToolCallingModel` pattern) - every HTTP hop below it is real.
"""

import importlib

import pytest
from langchain_core.messages import AIMessage

import eve.specialists.home as home_module
from tests.conftest import FakeToolCallingModel

pytestmark = pytest.mark.integration


async def test_ask_home_reads_real_state_through_a_running_eve_tools(
    eve_tools_server, monkeypatch
):
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", eve_tools_server)
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")
    from eve.settings import get_settings

    get_settings.cache_clear()

    tool_call = {
        "name": "get_state",
        "args": {"entity_id": "light.kitchen"},
        "id": "call-1",
        "type": "tool_call",
    }
    monkeypatch.setattr(
        "eve.specialists.home._model_for_test",
        lambda: FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="The kitchen light is off."),
                ]
            )
        ),
    )
    importlib.reload(home_module)
    monkeypatch.setattr(
        "eve.specialists.home._model_for_test",
        lambda: FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="The kitchen light is off."),
                ]
            )
        ),
    )

    state = {
        "member": {
            "sub": "sub-noah", "name": "Noah", "role": "adult",
            "timezone": "America/Vancouver", "permissions": ["home.control"],
            "local_time": "2026-08-21 09:00 PDT",
        },
        "messages": [], "system_prompt": "", "memory": None, "dynamic_tools": [],
    }
    result = await home_module.ask_home.ainvoke(
        {"request": "is the kitchen light on?", "state": state, "config": {"configurable": {}}}
    )
    assert "off" in result.lower()
