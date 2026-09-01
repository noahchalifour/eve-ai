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


@lru_cache(maxsize=1)
def get_computer_settings() -> ComputerSettings:
    return ComputerSettings()
