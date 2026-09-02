"""Agent name + model in, argv + environment out. Three entries in a dict.

No plugin system and no abstract base class: ACP exists precisely so that
the second and third harness are a line of config rather than a second
integration, and a registry with three entries that grows a factory is a
registry that has forgotten why it was cheap.

The three route to LiteLLM three different ways and there is no honest way
to flatten that. Claude Code speaks Anthropic Messages and reads
ANTHROPIC_BASE_URL from its own environment; Codex and OpenCode read a
provider block from a config file `bootstrap.sh` writes (Task 6) and take
only the key here. Pretending one mechanism served all three would mean
inventing an abstraction over exactly the part that differs.
"""

from __future__ import annotations

from eve_computer.settings import get_computer_settings

# Order is load-bearing only in that `codex` is first: it rides the ChatGPT
# subscription, so it is what Eve falls back to when nothing in the task or
# the member's preferences points anywhere (spec: "Codex breaks ties").
AGENT_NAMES: tuple[str, ...] = ("codex", "claude", "opencode")


class UnknownAgent(Exception):
    """An agent name outside AGENT_NAMES."""


def build(agent: str, model: str) -> tuple[list[str], dict[str, str]]:
    if agent not in AGENT_NAMES:
        raise UnknownAgent(f"unknown agent {agent!r}; expected one of {AGENT_NAMES}")
    settings = get_computer_settings()

    if agent == "claude":
        return (
            ["claude-code-acp"],
            {
                "ANTHROPIC_BASE_URL": settings.litellm_base_url,
                "ANTHROPIC_API_KEY": settings.litellm_api_key,
                "ANTHROPIC_MODEL": model,
            },
        )
    if agent == "codex":
        return (
            ["codex-acp", "--model", model],
            {"LITELLM_API_KEY": settings.litellm_api_key},
        )
    return (
        ["opencode", "acp", "--model", model],
        {"LITELLM_API_KEY": settings.litellm_api_key},
    )
