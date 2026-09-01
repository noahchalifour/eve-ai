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


@lru_cache(maxsize=1)
def get_computer_settings() -> ComputerSettings:
    return ComputerSettings()
