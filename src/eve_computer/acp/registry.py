"""Agent name + model in, argv + environment out. Four entries in a dict.

No plugin system and no abstract base class: ACP exists precisely so that
the second and third harness are a line of config rather than a second
integration, and a registry with four entries that grows a factory is a
registry that has forgotten why it was cheap.

The four route to LiteLLM four different ways and there is no honest way to
flatten that. Claude Code speaks Anthropic Messages and reads
ANTHROPIC_BASE_URL from its own environment; Codex and OpenCode read a
provider block from a config file `bootstrap.sh` writes (Task 6) and take
only the key here; the DeepSeek harness has no model flag at all and reads
a composed profile, so `harness.py` writes one and this file passes its
path. Pretending one mechanism served all four would mean inventing an
abstraction over exactly the part that differs.
"""

from __future__ import annotations

from eve_computer.acp import harness
from eve_computer.settings import get_computer_settings

# Order is load-bearing only in that the first entry breaks ties: it is what
# Eve falls back to when nothing in the task or the member's preferences
# points anywhere. `dsh` leads because EVE-24 asks for it - it carries Noah's
# own profile, pulled from git, so the untargeted case is the one configured
# the way he works.
AGENT_NAMES: tuple[str, ...] = ("dsh", "codex", "claude", "opencode")


class UnknownAgent(Exception):
    """An agent name outside AGENT_NAMES."""


def build(agent: str, model: str) -> tuple[list[str], dict[str, str]]:
    if agent not in AGENT_NAMES:
        raise UnknownAgent(f"unknown agent {agent!r}; expected one of {AGENT_NAMES}")
    settings = get_computer_settings()

    if agent == "dsh":
        # `--patch` is the last layer the launcher applies, so the route
        # survives a profile pulled from git (harness.py's whole argument).
        # No `--model`: `dsh` has none, and the model rides the environment
        # into the `!!js` expression that patch file carries.
        return (
            ["dsh", "--profile", "acp", "--patch", str(harness.route_patch_path())],
            {
                "DSH_HOME": str(harness.home_path()),
                harness.MODEL_VAR: model,
                harness.API_KEY_VAR: settings.litellm_api_key,
            },
        )
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
