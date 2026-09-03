import pytest

from eve_computer.acp.registry import AGENT_NAMES, UnknownAgent, build


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_BASE_URL", "https://litellm.example")
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_API_KEY", "sk-test")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    yield
    get_computer_settings.cache_clear()


def test_every_named_agent_builds():
    for agent in AGENT_NAMES:
        argv, env = build(agent, "some/model")
        assert argv and isinstance(argv[0], str)
        assert env


def test_codex_is_first_because_it_breaks_ties():
    assert AGENT_NAMES[0] == "codex"


def test_the_model_reaches_every_agent():
    for agent in AGENT_NAMES:
        argv, env = build(agent, "chatgpt/gpt-5.6-luna")
        assert "chatgpt/gpt-5.6-luna" in [*argv, *env.values()]


def test_claude_is_routed_through_litellms_anthropic_door():
    _argv, env = build("claude", "anthropic/claude-sonnet-5")
    assert env["ANTHROPIC_BASE_URL"] == "https://litellm.example"
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["ANTHROPIC_MODEL"] == "anthropic/claude-sonnet-5"


def test_codex_and_opencode_carry_the_litellm_key():
    for agent in ("codex", "opencode"):
        _argv, env = build(agent, "chatgpt/gpt-5.6-sol")
        assert env["LITELLM_API_KEY"] == "sk-test"


def test_an_unknown_agent_is_refused():
    with pytest.raises(UnknownAgent):
        build("cursor", "chatgpt/gpt-5.6-sol")
