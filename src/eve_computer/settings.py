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


@lru_cache(maxsize=1)
def get_computer_settings() -> ComputerSettings:
    return ComputerSettings()
