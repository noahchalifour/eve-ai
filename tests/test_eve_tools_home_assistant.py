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
