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
        "suggestions": [],
    }
    result = await home_module.ask_home.ainvoke(
        {"request": "is the kitchen light on?", "state": state, "config": {"configurable": {}}}
    )
    assert "off" in result.lower()


_LIGHTS = [
    "light.kitchen",
    "light.living_room",
    "light.bedroom",
    "light.porch",
    "light.garage",
    "light.office",
]


def _counting_model(entity_ids, final):
    """A model that answers "how many lights are on?" the only way `ask_home`
    allows: one `get_state` per entity, because neither the specialist nor
    eve-tools can enumerate entities. Each guess costs one round, so this is
    the request shape that hit EVE-15 in real use."""
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "get_state",
                    "args": {"entity_id": entity_id},
                    "id": f"call-{i}",
                    "type": "tool_call",
                }
            ],
        )
        for i, entity_id in enumerate(entity_ids)
    ]
    messages.append(AIMessage(content=final))
    return FakeToolCallingModel(messages=iter(messages))


async def _ask_how_many_lights(monkeypatch, eve_tools_server, entity_ids, final):
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", eve_tools_server)
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")
    from eve.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        "eve.specialists.home._model_for_test",
        lambda: _counting_model(entity_ids, final),
    )
    importlib.reload(home_module)
    monkeypatch.setattr(
        "eve.specialists.home._model_for_test",
        lambda: _counting_model(entity_ids, final),
    )
    state = {
        "member": {
            "sub": "sub-noah", "name": "Noah", "role": "adult",
            "timezone": "America/Vancouver", "permissions": ["home.control"],
            "local_time": "2026-08-21 09:00 PDT",
        },
        "messages": [], "system_prompt": "", "memory": None, "dynamic_tools": [],
        "suggestions": [],
    }
    return await home_module.ask_home.ainvoke(
        {
            "request": "how many lights are on?",
            "state": state,
            "config": {"configurable": {}},
        }
    )


async def test_how_many_lights_are_on_survives_a_get_state_per_light(
    eve_tools_server, monkeypatch
):
    """EVE-15 end to end over the real HTTP boundary: six `get_state` rounds
    used to raise GraphRecursionError on the third, so this whole class of
    question was unanswerable."""
    result = await _ask_how_many_lights(
        monkeypatch, eve_tools_server, _LIGHTS, "Three of your six lights are on."
    )
    assert result == "Three of your six lights are on."


async def test_more_lights_than_the_budget_degrades_to_a_sentence(
    eve_tools_server, monkeypatch
):
    """Past the budget the member must still get English, not the name of an
    exception class."""
    too_many = _LIGHTS + ["light.hallway", "light.basement"]
    result = await _ask_how_many_lights(
        monkeypatch, eve_tools_server, too_many, "never reached"
    )
    assert "GraphRecursionError" not in result
    assert "recursion" not in result.lower()
    assert "home" in result


async def test_how_many_lights_are_on_answers_from_one_list_entities_call(
    eve_tools_server, monkeypatch
):
    """The shape a real model takes now that `list_entities` exists: one call
    returns every light and its state, so counting costs one round instead of
    one-per-guess. Asserts on the real payload crossing two real HTTP hops -
    the stub home has six lights, three of them on."""
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", eve_tools_server)
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")
    from eve.settings import get_settings

    get_settings.cache_clear()
    seen = {}

    def _model():
        return FakeToolCallingModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_entities",
                                "args": {"domain": "light"},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="Three of the six lights are on."),
                ]
            )
        )

    monkeypatch.setattr("eve.specialists.home._model_for_test", _model)
    importlib.reload(home_module)
    monkeypatch.setattr("eve.specialists.home._model_for_test", _model)

    # Assert on what eve-tools actually returned, not just the faked sentence:
    # the fake model would say "three" no matter what came back.
    seen["payload"] = await home_module.list_entities.ainvoke({"domain": "light"})

    state = {
        "member": {
            "sub": "sub-noah", "name": "Noah", "role": "adult",
            "timezone": "America/Vancouver", "permissions": ["home.control"],
            "local_time": "2026-08-21 09:00 PDT",
        },
        "messages": [], "system_prompt": "", "memory": None, "dynamic_tools": [],
        "suggestions": [],
    }
    result = await home_module.ask_home.ainvoke(
        {
            "request": "how many lights are on?",
            "state": state,
            "config": {"configurable": {}},
        }
    )
    payload = seen["payload"]
    assert "sensor.outside_temp" not in payload, "domain filter crossed the wire"
    assert payload.count("'state': 'on'") == 3 or payload.count('"state": "on"') == 3, (
        f"expected 3 lights on in the real eve-tools payload, got: {payload}"
    )
    assert result == "Three of the six lights are on."
