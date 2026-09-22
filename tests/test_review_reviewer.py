"""The issue's central requirement: a reviewer that is not the implementer."""

from __future__ import annotations

import pytest

from eve.review import reviewer


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_DEFAULT_AGENT", "claude")
    monkeypatch.setenv("EVE_REVIEW_DEFAULT_MODEL", "anthropic/claude-sonnet-5")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_human_pull_request_gets_the_configured_default():
    assert reviewer.choose(None) == ("claude", "anthropic/claude-sonnet-5")


def test_an_eve_pull_request_is_reviewed_by_a_different_agent():
    agent, model = reviewer.choose(("dsh", "chatgpt/gpt-5.6-sol"))

    assert agent != "dsh"
    assert model != "chatgpt/gpt-5.6-sol"


def test_the_default_is_avoided_when_it_is_what_implemented_the_change():
    """The whole point is a different blind spot. When the configured
    default is the implementer, it must not be chosen anyway."""
    agent, model = reviewer.choose(("claude", "anthropic/claude-sonnet-5"))

    assert agent != "claude"
    assert model != "anthropic/claude-sonnet-5"


def test_the_choice_is_a_known_agent():
    from eve.coding.dispatch import AGENTS

    for implemented_by in (None, ("dsh", "m"), ("claude", "anthropic/claude-sonnet-5")):
        agent, _model = reviewer.choose(implemented_by)
        assert agent in AGENTS


def test_the_choice_is_stable_for_the_same_input():
    """Two reviews of the same pull request must not differ because the
    reviewer was picked at random."""
    first = reviewer.choose(("dsh", "chatgpt/gpt-5.6-sol"))
    second = reviewer.choose(("dsh", "chatgpt/gpt-5.6-sol"))

    assert first == second
