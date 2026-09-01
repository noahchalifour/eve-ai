"""tests/test_specialists_health.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.health as health_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import CONFIG, MEMBER, STATE


def _model_with(*ai_messages):
    return lambda: FakeToolCallingModel(messages=iter(ai_messages))


async def test_ask_health_reads_recovery_through_eve_tools(monkeypatch):
    tool_call = {
        "name": "get_recovery",
        "args": {"days": 1},
        "id": "call-1",
        "type": "tool_call",
    }
    factory = _model_with(
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Your recovery is 68% - a normal training day."),
    )
    monkeypatch.setattr("eve.specialists.health._model_for_test", factory)
    importlib.reload(health_module)
    monkeypatch.setattr("eve.specialists.health._model_for_test", factory)

    mock_invoke = AsyncMock(return_value='{"recovery": []}')
    monkeypatch.setattr(health_module, "invoke", mock_invoke)

    member = {**MEMBER, "permissions": ["health"]}
    result = await health_module.ask_health.ainvoke(
        {
            "request": "how's my recovery?",
            "state": {**STATE, "member": member},
            "config": {"configurable": {"member": member}},
        }
    )
    assert result == "Your recovery is 68% - a normal training day."
    # member_sub crosses the boundary, not the member's name, role, or
    # timezone. ADR 0006 / 0016.
    mock_invoke.assert_awaited_once_with(
        "health.get_recovery", {"member_sub": "sub-noah", "days": 1}
    )


async def test_a_member_without_the_health_permission_is_denied(monkeypatch):
    def _never():
        raise AssertionError("the model must not be built for a denied call")

    monkeypatch.setattr("eve.specialists.health._model_for_test", _never)
    importlib.reload(health_module)
    monkeypatch.setattr("eve.specialists.health._model_for_test", _never)

    member = {**MEMBER, "permissions": ["home.control"]}
    result = await health_module.ask_health.ainvoke(
        {
            "request": "how did I sleep?",
            "state": {**STATE, "member": member},
            "config": CONFIG,
        }
    )
    assert "Permission denied" in result
    assert "health" in result


def test_the_prompt_carries_the_clinical_guardrail():
    """Spec 5.1. A wearable-derived LLM opinion on a symptom reads as
    authoritative when it should not."""
    prompt = health_module.SYSTEM_PROMPT
    assert "doctor" in prompt
    assert "diagnose" in prompt


def test_the_prompt_explains_that_null_is_not_zero():
    """Spec 4.1 is a contract the model has to honour too - it is the thing
    that turns a None into 'WHOOP doesn't count steps' instead of 'you took
    no steps'."""
    assert "null" in health_module.SYSTEM_PROMPT.lower()


def test_the_prompt_explains_the_morning_gap():
    """Spec 4.3.1: an empty recovery result before wake-up is normal, and a
    coach that reports it as a fault is wrong every single morning."""
    assert "scored" in health_module.SYSTEM_PROMPT.lower()


def test_all_three_tools_exist_with_the_names_eve_tools_dispatches():
    """The eve-tools handler table keys are health.get_recovery / get_sleep /
    get_activity; a renamed tool here would 404 at runtime with nothing
    failing at import."""
    assert {
        health_module.get_recovery.name,
        health_module.get_sleep.name,
        health_module.get_activity.name,
    } == {"get_recovery", "get_sleep", "get_activity"}
