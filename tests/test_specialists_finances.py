"""tests/test_specialists_finances.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.finances as finances_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import CONFIG, MEMBER, STATE


async def test_ask_finances_reads_transactions_through_eve_tools(monkeypatch):
    tool_call = {
        "name": "list_transactions",
        "args": {"limit": 5, "category": None},
        "id": "call-1",
        "type": "tool_call",
    }
    monkeypatch.setattr(
        "eve.specialists.finances._model_for_test",
        lambda: FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="You spent $42 at the grocery store."),
                ]
            )
        ),
    )
    importlib.reload(finances_module)
    monkeypatch.setattr(
        "eve.specialists.finances._model_for_test",
        lambda: FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(content="", tool_calls=[tool_call]),
                    AIMessage(content="You spent $42 at the grocery store."),
                ]
            )
        ),
    )
    mock_invoke = AsyncMock(return_value='{"transactions": []}')
    monkeypatch.setattr(finances_module, "invoke", mock_invoke)
    member = {**MEMBER, "permissions": ["finances"]}
    state = {**STATE, "member": member}
    result = await finances_module.ask_finances.ainvoke(
        {"request": "what did I spend recently?", "state": state, "config": CONFIG}
    )
    assert result == "You spent $42 at the grocery store."
    mock_invoke.assert_awaited_once_with(
        "finances.list_transactions", {"limit": 5, "category": None}
    )
