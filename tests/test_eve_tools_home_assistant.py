import httpx
import pytest
import respx

from eve_tools import home_assistant


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_HOME_ASSISTANT_URL", "http://ha.test")
    monkeypatch.setenv("EVE_TOOLS_HOME_ASSISTANT_TOKEN", "ha-token")


@respx.mock
async def test_get_state_reads_from_home_assistant():
    respx.get("http://ha.test/api/states/light.kitchen").mock(
        return_value=httpx.Response(200, json={"entity_id": "light.kitchen", "state": "on"})
    )
    result = await home_assistant.get_state("light.kitchen")
    assert result["state"] == "on"


@respx.mock
async def test_get_state_sends_the_bearer_token():
    route = respx.get("http://ha.test/api/states/light.kitchen").mock(
        return_value=httpx.Response(200, json={})
    )
    await home_assistant.get_state("light.kitchen")
    assert route.calls.last.request.headers["authorization"] == "Bearer ha-token"


@respx.mock
async def test_call_service_posts_to_home_assistant():
    route = respx.post("http://ha.test/api/services/light/turn_on").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = await home_assistant.call_service(
        "light", "turn_on", "light.kitchen", {"brightness": 200}
    )
    assert result["called"] is True
    body = route.calls.last.request.content
    import json as _json
    assert _json.loads(body) == {"entity_id": "light.kitchen", "brightness": 200}


_ALL_STATES = [
    {
        "entity_id": "light.kitchen",
        "state": "on",
        "attributes": {"friendly_name": "Kitchen", "brightness": 254},
    },
    {"entity_id": "light.porch", "state": "off", "attributes": {}},
    {"entity_id": "sensor.outside_temp", "state": "11.4", "attributes": {}},
]


@respx.mock
async def test_list_entities_returns_every_domain_by_default():
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=_ALL_STATES)
    )
    result = await home_assistant.list_entities()
    assert result["total"] == 3
    assert [e["entity_id"] for e in result["entities"]] == [
        "light.kitchen",
        "light.porch",
        "sensor.outside_temp",
    ]


@respx.mock
async def test_list_entities_filters_to_one_domain():
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=_ALL_STATES)
    )
    result = await home_assistant.list_entities("light")
    assert result["total"] == 2
    assert {e["entity_id"] for e in result["entities"]} == {
        "light.kitchen",
        "light.porch",
    }


@respx.mock
async def test_list_entities_trims_the_attribute_blob_to_a_friendly_name():
    """HA sends every attribute of every entity. Forwarding that wholesale
    would spend the specialist's context on brightness values and colour
    temperatures it never reasons about."""
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=_ALL_STATES)
    )
    result = await home_assistant.list_entities("light")
    kitchen = next(e for e in result["entities"] if e["entity_id"] == "light.kitchen")
    assert kitchen == {"entity_id": "light.kitchen", "state": "on", "name": "Kitchen"}


@respx.mock
async def test_list_entities_caps_the_list_and_says_so():
    many = [
        {"entity_id": f"light.l{i}", "state": "off", "attributes": {}}
        for i in range(home_assistant._MAX_ENTITIES + 5)
    ]
    respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=many)
    )
    result = await home_assistant.list_entities("light")
    assert len(result["entities"]) == home_assistant._MAX_ENTITIES
    assert result["total"] == home_assistant._MAX_ENTITIES + 5
    assert result["truncated"] is True


@respx.mock
async def test_list_entities_sends_the_bearer_token():
    route = respx.get("http://ha.test/api/states").mock(
        return_value=httpx.Response(200, json=[])
    )
    await home_assistant.list_entities()
    assert route.calls.last.request.headers["authorization"] == "Bearer ha-token"
