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


@respx.mock
async def test_invoke_degrades_to_error_on_malformed_json():
    """Malformed or non-JSON responses (proxy errors, truncated) must not
    raise json.JSONDecodeError."""
    respx.post("http://eve-tools.test/invoke").mock(
        return_value=httpx.Response(200, text="<html>Gateway Error</html>")
    )
    result = await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert result.startswith("error:") and "JSONDecodeError" in result


@respx.mock
async def test_invoke_degrades_to_error_on_missing_result_and_error_keys():
    """A response with neither 'result' nor 'error' keys must not raise
    KeyError."""
    respx.post("http://eve-tools.test/invoke").mock(
        return_value=httpx.Response(200, json={"data": "something"})
    )
    result = await invoke("home.get_state", {"entity_id": "light.kitchen"})
    assert result.startswith("error:") and "KeyError" in result


@respx.mock
async def test_invoke_targets_the_sandbox_when_asked(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_BASE_URL", "http://sandbox:8091")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "s" * 32)
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", "http://tools:8090")
    from eve.settings import get_settings

    get_settings.cache_clear()

    route = respx.post("http://sandbox:8091/invoke").respond(
        json={"result": {"n": 42}}
    )
    from eve.tools_client import invoke

    out = await invoke("amortise", {"a": 41}, target="sandbox")

    assert route.called
    assert "42" in out
    assert route.calls[0].request.headers["authorization"] == "Bearer " + "s" * 32


@respx.mock
async def test_invoke_still_defaults_to_eve_tools(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_BASE_URL", "http://tools:8090")
    monkeypatch.setenv("EVE_TOOLS_API_KEY", "t" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()

    route = respx.post("http://tools:8090/invoke").respond(json={"result": 1})
    from eve.tools_client import invoke

    await invoke("home.get_state", {"entity_id": "x"})
    assert route.called


@respx.mock
async def test_a_dead_sandbox_returns_an_error_string(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_BASE_URL", "http://sandbox:8091")
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "s" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()

    respx.post("http://sandbox:8091/invoke").mock(side_effect=ConnectionError)
    from eve.tools_client import invoke

    out = await invoke("amortise", {}, target="sandbox")
    assert out.startswith("error:")
