"""OPENA-22: the GUI task harness must pin a model the LiteLLM key may call.

Left unset, the bundled Claude Code CLI picks its own default
(`claude-opus-5`), which Eve's key is not allowed to use: every request 401s,
the CLI retries silently, and the task only resolves when the wall-clock
timeout fires half an hour later - "it never comes back"."""

from __future__ import annotations

import pytest
from claude_agent_sdk import ResultMessage

from eve_computer import harness
from eve_computer.settings import get_computer_settings


@pytest.fixture
def captured(monkeypatch, tmp_path):
    monkeypatch.setenv("EVE_COMPUTER_TASKS_DIR", str(tmp_path))
    get_computer_settings.cache_clear()
    seen = {}

    async def fake_run(goal, options):
        seen["options"] = options
        return ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s", result="Example Domain",
        )

    monkeypatch.setattr(harness, "_run", fake_run)
    yield seen
    get_computer_settings.cache_clear()


async def test_the_task_runs_on_the_configured_model(captured):
    result = await harness.run_task("t1", "read the heading")

    assert result["summary"] == "Example Domain"
    options = captured["options"]
    assert options.model == "anthropic/claude-sonnet-5"
    # Claude Code's background calls (titles, summaries) use a separate
    # "small fast" model; it must ride the same allowed model or those 401 too.
    for var in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
    ):
        assert options.env[var] == "anthropic/claude-sonnet-5", var


async def test_the_model_is_overridable(captured, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_MODEL", "chatgpt/gpt-5.6-sol")
    get_computer_settings.cache_clear()

    await harness.run_task("t2", "anything")

    assert captured["options"].model == "chatgpt/gpt-5.6-sol"
