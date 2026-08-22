"""tests/test_specialists_home.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.home as home_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import CONFIG, MEMBER, STATE


async def test_ask_home_calls_get_state_through_eve_tools(monkeypatch):
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
                    AIMessage(content="The kitchen light is on."),
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
                    AIMessage(content="The kitchen light is on."),
                ]
            )
        ),
    )
    mock_invoke = AsyncMock(return_value='{"state": "on"}')
    monkeypatch.setattr(home_module, "invoke", mock_invoke)

    result = await home_module.ask_home.ainvoke(
        {"request": "is the kitchen light on?", "state": STATE, "config": CONFIG}
    )
    assert result == "The kitchen light is on."
    mock_invoke.assert_awaited_once_with(
        "home.get_state", {"entity_id": "light.kitchen"}
    )
