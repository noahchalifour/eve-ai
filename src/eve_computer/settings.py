"""eve-computer's own configuration. No third-party credential here - her
accounts live only as browser session cookies on the PVC (design doc:
"Identity"), never an environment variable."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ComputerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVE_COMPUTER_", extra="ignore")

    api_key: str = ""
    litellm_base_url: str = "https://litellm.chalifour.dev"
    litellm_api_key: str = ""
    # OPENA-22. The GUI task harness must name a model the LiteLLM key is
    # allowed to call; left unset, Claude Code picks its own default, every
    # request 401s, and the task hangs until task_timeout_seconds.
    model: str = "anthropic/claude-sonnet-5"
    max_turns: int = 40
    task_timeout_seconds: int = 1800
    tasks_dir: str = "/home/eve/tasks"

    # EVE-4 (ACP tools). Sessions are a second lane beside the GUI task
    # queue: they need no X display, so serialising them behind the one
    # mouse would be a bound with no reason behind it.
    sessions_dir: str = "/home/eve/sessions"
    code_dir: str = "/home/eve/code"
    max_concurrent_sessions: int = 3
    session_max_turns: int = 40
    session_turn_timeout_seconds: int = 1800
    session_timeout_seconds: int = 14400
    github_owner: str = ""

    # EVE-27. A review is a session with a tighter ceiling than a coding
    # session: four hours of reviewing is a failure at something other than
    # reviewing, and it holds a slot a human is waiting on.
    review_session_timeout_seconds: int = 3600
    # Separate from max_concurrent_sessions so a burst of labelled pull
    # requests cannot starve the delegated coding work that
    # `check_coding_session` promises a member is in flight.
    max_concurrent_reviews: int = 2

    # EVE-24 (the DeepSeek harness as an ACP agent). `dsh` keeps profiles,
    # sessions and credentials under one home; this one is on the PVC so a
    # pod reschedule does not lose the profile or replay the git pull.
    dsh_home: str = "/home/eve/.dsh"
    # The configuration repository `dsh harness-sync` pushes from Noah's
    # laptop, cloned into `dsh_home` on every start (EVE-24: "It should pull
    # my profile from git"). Empty disables the pull, which is the default:
    # a deployment that has not been given a repository must not guess one,
    # and the stock profile boots fine without it.
    dsh_profile_repo: str = ""
    dsh_profile_branch: str = "main"

    # EVE-45. Noah's skills, for every ACP coding session regardless of
    # agent. Named by default because the issue names it; empty disables.
    # Cloned onto the PVC so a failed fetch still leaves the last good copy.
    agent_skills_repo: str = "https://github.com/noahchalifour/agent-skills.git"
    agent_skills_branch: str = "main"
    agent_skills_dir: str = "/home/eve/.eve/agent-skills"
    # dsh and Codex read ~/.agents/skills, Claude Code ~/.claude/skills,
    # OpenCode both - two directories cover all four agents.
    agent_skills_link_dirs: tuple[str, ...] = (
        "/home/eve/.agents/skills",
        "/home/eve/.claude/skills",
    )


@lru_cache(maxsize=1)
def get_computer_settings() -> ComputerSettings:
    return ComputerSettings()
