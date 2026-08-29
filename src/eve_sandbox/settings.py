"""eve-sandbox's own configuration: one API key and the limits. Nothing else,
deliberately - there is no database URL, no model key, and no third-party
credential for this service to leak."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVE_SANDBOX_", extra="ignore")

    api_key: str = ""
    timeout_seconds: int = 5
    memory_mb: int = 256
    max_output_bytes: int = 65536
    max_concurrency: int = 4


@lru_cache(maxsize=1)
def get_sandbox_settings() -> SandboxSettings:
    return SandboxSettings()
