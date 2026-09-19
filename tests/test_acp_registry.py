import pytest

from eve_computer.acp.registry import AGENT_NAMES, UnknownAgent, build


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_BASE_URL", "https://litellm.example")
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_API_KEY", "sk-test")
    monkeypatch.setenv("EVE_COMPUTER_DSH_HOME", str(tmp_path / "harness"))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    yield
    get_computer_settings.cache_clear()


def test_every_named_agent_builds():
    for agent in AGENT_NAMES:
        argv, env = build(agent, "some/model")
        assert argv and isinstance(argv[0], str)
        assert env


def test_dsh_is_first_because_it_breaks_ties():
    assert AGENT_NAMES[0] == "dsh"


def test_dsh_boots_the_acp_profile_under_this_boxs_route():
    """`dsh` takes no `--model` flag: the model is configuration, and the
    route that serves it is a patch file this box owns. Both reach the
    subprocess as environment, so one profile serves every model Eve names
    and a profile pulled from git cannot outrank the routing."""
    from eve_computer.acp import harness

    argv, env = build("dsh", "chatgpt/gpt-5.6-sol")

    assert argv[:3] == ["dsh", "--profile", "acp"]
    assert argv[3] == "--patch"
    assert argv[4] == str(harness.route_patch_path())
    assert env["DSH_HOME"] == str(harness.home_path())
    assert env[harness.MODEL_VAR] == "chatgpt/gpt-5.6-sol"
    assert env[harness.API_KEY_VAR] == "sk-test"


def test_dsh_never_carries_the_key_in_its_argv():
    """argv is world-readable in `ps`; the key belongs in the environment
    the subprocess is handed and in the reference the route names."""
    argv, _env = build("dsh", "chatgpt/gpt-5.6-sol")

    assert not any("sk-test" in argument for argument in argv)


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
