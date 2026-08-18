"""All environment configuration for Eve. This module imports nothing from
the rest of the package, so every other module may depend on it freely."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EVE_", env_file=".env", extra="ignore"
    )

    # Deployment
    env: str = "development"  # "development" | "production"

    # Authentication (see docs/adr and spec section 8)
    auth_mode: str = "dev"  # "dev" | "oidc"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    # dev mode only: opaque token -> Authentik subject in family.yaml
    dev_tokens: dict[str, str] = Field(default_factory=dict)

    # Model access
    litellm_base_url: str = "https://litellm.chalifour.dev"
    litellm_api_key: str = ""

    # Data files
    family_file: Path = Path("family.yaml")
    prompt_file: Path = Path("prompts/eve.md")

    # PINNED. Changing either value requires re-embedding ALL of Eve's memory
    # (spec section 7.3, ADR 0003). Unused until Phase 2; declared here so
    # Phase 2 inherits the pin rather than re-deciding it.
    embedding_model: str = "openai:text-embedding-3-small"
    embedding_dims: int = 1536

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        if self.env == "production" and self.auth_mode != "oidc":
            raise ValueError(
                "EVE_AUTH_MODE must be 'oidc' when EVE_ENV=production; "
                f"got {self.auth_mode!r}"
            )
        if self.auth_mode == "oidc":
            # Without these, every request fails a signature or claim check
            # and the deployment answers 401 to everyone - a symptom that
            # reads like a token problem. Refuse to start instead.
            missing = [
                name
                for name in ("oidc_issuer", "oidc_audience", "oidc_jwks_url")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    "EVE_AUTH_MODE=oidc requires "
                    + ", ".join(f"EVE_{name.upper()}" for name in missing)
                )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
