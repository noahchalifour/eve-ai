"""The deny-list is one prefix and it is not a survey. ADR 0004 probed
ocp/* live: the proxy strips tool definitions, so the agent answers
fluently and changes nothing - undetectable at runtime, therefore denied
here. Models the ChatGPT sign-in refuses fail loudly at the first prompt
and need no entry; enumerating them would be a second list to keep in sync
with OpenAI's generation renames."""

import respx
from httpx import Response

import pytest

from eve.coding import catalogue

CATALOGUE = {
    "data": [
        {"id": "chatgpt/gpt-5.6-sol"},
        {"id": "chatgpt/gpt-5.6-luna"},
        {"id": "anthropic/claude-sonnet-5"},
        {"id": "ocp/claude-sonnet-5"},
        {"id": "gemini/gemini-flash-lite-latest"},
    ]
}


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setenv("EVE_LITELLM_BASE_URL", "https://litellm.example")
    from eve.settings import get_settings

    get_settings.cache_clear()
    catalogue._reset_cache()
    yield
    get_settings.cache_clear()
    catalogue._reset_cache()


@respx.mock
async def test_the_catalogue_comes_from_litellm():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    models = await catalogue.available_models()

    assert "chatgpt/gpt-5.6-sol" in models
    assert "anthropic/claude-sonnet-5" in models


@respx.mock
async def test_ocp_models_are_denied():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert "ocp/claude-sonnet-5" not in await catalogue.available_models()


@respx.mock
async def test_the_catalogue_is_cached_within_its_ttl():
    route = respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    await catalogue.available_models()
    await catalogue.available_models()

    assert route.call_count == 1


@respx.mock
async def test_an_unreachable_proxy_yields_an_empty_catalogue_not_an_exception():
    respx.get("https://litellm.example/v1/models").mock(return_value=Response(503))

    assert await catalogue.available_models() == []


@respx.mock
async def test_validate_accepts_a_model_in_the_catalogue():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert await catalogue.validate("chatgpt/gpt-5.6-luna", "codex") == "chatgpt/gpt-5.6-luna"


@respx.mock
async def test_validate_falls_back_for_a_hallucinated_name():
    """A bad name would otherwise kill the session at its first prompt,
    several minutes after Eve promised the member she was on it."""
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert await catalogue.validate("gpt-9-ultra", "codex") == "chatgpt/gpt-5.6-sol"


@respx.mock
async def test_validate_falls_back_for_a_denied_model():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert await catalogue.validate("ocp/claude-sonnet-5", "claude") == "anthropic/claude-sonnet-5"


@respx.mock
async def test_validate_falls_back_when_no_model_was_chosen():
    respx.get("https://litellm.example/v1/models").mock(
        return_value=Response(200, json=CATALOGUE)
    )

    assert await catalogue.validate(None, "claude") == "anthropic/claude-sonnet-5"


@respx.mock
async def test_validate_still_answers_when_the_catalogue_is_empty():
    """A proxy outage must not make delegation impossible - the agent's own
    default is a better answer than refusing to start."""
    respx.get("https://litellm.example/v1/models").mock(return_value=Response(503))

    assert await catalogue.validate("chatgpt/gpt-5.6-sol", "codex") == "chatgpt/gpt-5.6-sol"
