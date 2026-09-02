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


@respx.mock
async def test_dispatch_task_posts_to_the_computer_tasks_endpoint(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_BASE_URL", "http://eve-computer.test")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "c" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve.tools_client import dispatch_task

    route = respx.post("http://eve-computer.test/tasks").mock(
        return_value=httpx.Response(202, json={"id": "t1", "status": "queued"})
    )
    result = await dispatch_task("t1", "book the flight")

    assert result == "ok"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"id": "t1", "goal": "book the flight"}
    assert route.calls.last.request.headers["authorization"] == "Bearer " + "c" * 32


@respx.mock
async def test_dispatch_task_degrades_to_an_error_string_on_failure(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_BASE_URL", "http://eve-computer.test")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "c" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve.tools_client import dispatch_task

    respx.post("http://eve-computer.test/tasks").mock(side_effect=httpx.ConnectError)
    result = await dispatch_task("t1", "book the flight")
    assert result.startswith("error:")


@respx.mock
async def test_get_computer_task_returns_the_boxs_status(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_BASE_URL", "http://eve-computer.test")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "c" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve.tools_client import get_computer_task

    respx.get("http://eve-computer.test/tasks/t1").mock(
        return_value=httpx.Response(
            200, json={"status": "finished", "result": {"summary": "done"}, "artifacts": []}
        )
    )
    status = await get_computer_task("t1")
    assert status == {"status": "finished", "result": {"summary": "done"}, "artifacts": []}


@respx.mock
async def test_get_computer_task_returns_none_when_the_box_is_unreachable(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_BASE_URL", "http://eve-computer.test")
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "c" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    from eve.tools_client import get_computer_task

    respx.get("http://eve-computer.test/tasks/t1").mock(side_effect=httpx.ConnectError)
    assert await get_computer_task("t1") is None


@respx.mock
async def test_create_coding_session_posts_the_agent_and_model(monkeypatch):
    from httpx import Response as HTTPXResponse

    from eve import tools_client

    route = respx.post("http://eve-computer:8092/sessions").mock(
        return_value=HTTPXResponse(202, json={"id": "s1", "status": "queued"})
    )

    result = await tools_client.create_coding_session(
        "s1", "codex", "chatgpt/gpt-5.6-sol", ["acme/repo"], "fix it"
    )

    assert result == "ok"
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "id": "s1", "agent": "codex", "model": "chatgpt/gpt-5.6-sol",
        "repos": ["acme/repo"], "prompt": "fix it",
    }


@respx.mock
async def test_create_coding_session_degrades_to_an_error_string():
    from httpx import Response as HTTPXResponse

    from eve import tools_client

    respx.post("http://eve-computer:8092/sessions").mock(
        return_value=HTTPXResponse(500)
    )

    result = await tools_client.create_coding_session("s1", "codex", "m", ["r"], "p")

    assert result.startswith("error:")


@respx.mock
async def test_get_coding_session_passes_the_cursor():
    from httpx import Response as HTTPXResponse

    from eve import tools_client

    route = respx.get("http://eve-computer:8092/sessions/s1").mock(
        return_value=HTTPXResponse(200, json={"status": "idle", "turns": [], "cursor": 3})
    )

    result = await tools_client.get_coding_session("s1", since=3)

    assert result["cursor"] == 3
    assert route.calls.last.request.url.params["since"] == "3"


@respx.mock
async def test_get_coding_session_returns_none_when_the_box_is_unreachable():
    from httpx import Response as HTTPXResponse

    from eve import tools_client

    respx.get("http://eve-computer:8092/sessions/s1").mock(
        return_value=HTTPXResponse(503)
    )

    assert await tools_client.get_coding_session("s1") is None


@respx.mock
async def test_prompt_carries_its_kind():
    from httpx import Response as HTTPXResponse

    from eve import tools_client

    route = respx.post("http://eve-computer:8092/sessions/s1/prompt").mock(
        return_value=HTTPXResponse(200, json={"status": "queued"})
    )

    await tools_client.prompt_coding_session("s1", "use httpx", kind="interjection")

    assert json.loads(route.calls.last.request.content)["kind"] == "interjection"


@respx.mock
async def test_close_returns_the_pull_requests():
    from httpx import Response as HTTPXResponse

    from eve import tools_client

    respx.post("http://eve-computer:8092/sessions/s1/close").mock(
        return_value=HTTPXResponse(200, json={"prs": [{"repo": "acme/repo", "pr_url": "u"}]})
    )

    result = await tools_client.close_coding_session("s1")

    assert result["prs"][0]["pr_url"] == "u"


@respx.mock
async def test_every_session_call_sends_the_computer_bearer_token(monkeypatch):
    from httpx import Response as HTTPXResponse

    from eve import tools_client

    # 32+ chars: Settings refuses a shorter computer_api_key outright
    # ("a guessable value fails open"), so the header check needs a key
    # that survives validation.
    key = "secret-0123456789abcdef0123456789"
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", key)
    from eve.settings import get_settings

    get_settings.cache_clear()
    route = respx.post("http://eve-computer:8092/sessions").mock(
        return_value=HTTPXResponse(202, json={})
    )

    await tools_client.create_coding_session("s1", "codex", "m", ["r"], "p")

    assert route.calls.last.request.headers["authorization"] == f"Bearer {key}"
