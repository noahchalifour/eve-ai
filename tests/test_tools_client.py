"""tests/test_tools_client.py"""
import json

import httpx
import pytest
import respx

from eve.tools_client import invoke


@pytest.fixture(autouse=True)
def _tools_settings(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", "http://eve-tools.test")
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "test-key")


@respx.mock
async def test_invoke_returns_the_result_as_json_text():
    respx.post("http://eve-tools.test/invoke").mock(
        return_value=httpx.Response(200, json={"result": {"state": "on"}})
    )
    result = await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert json.loads(result) == {"state": "on"}


@respx.mock
async def test_invoke_sends_the_shared_bearer_token():
    route = respx.post("http://eve-tools.test/invoke").mock(
        return_value=httpx.Response(200, json={"result": {}})
    )
    await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert route.calls.last.request.headers["authorization"] == "Bearer test-key"


@respx.mock
async def test_invoke_surfaces_a_server_side_error_as_a_string():
    respx.post("http://eve-tools.test/invoke").mock(
        return_value=httpx.Response(200, json={"error": "Home Assistant unreachable"})
    )
    result = await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert result == "error: Home Assistant unreachable"


@respx.mock
async def test_invoke_degrades_to_an_error_string_on_transport_failure():
    """A down eve-tools must not fail the whole turn - the caller is always a
    tool whose result goes straight to a model (design doc section 7)."""
    respx.post("http://eve-tools.test/invoke").mock(side_effect=httpx.ConnectError)
    result = await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert result.startswith("error:")
