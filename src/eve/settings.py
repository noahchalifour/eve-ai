"""All environment configuration for Eve. This module imports nothing from
the rest of the package, so every other module may depend on it freely."""

from __future__ import annotations

import os
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
    # (spec section 7.3, ADR 0003). The Gemini conditional ADR 0003 carried
    # since Phase 1 resolved when the metered REFLEX key was provisioned: the
    # key is Gemini, so the embedding model is too.
    #
    # gemini-embedding-001 emits 3072 dimensions trained with Matryoshka
    # representation learning. Truncating to 1536 breaks unit norm, so
    # memory/embed.py re-normalises. Cosine distance over non-normalised
    # vectors fails silently - wrong rankings, no error.
    embedding_model: str = "gemini/gemini-embedding-001"
    embedding_dims: int = 1536

    # Phase 3 (Specialists + Skills). See docs/superpowers/specs/
    # 2026-08-21-eve-specialists-design.md section 7 and section 9.
    tools_base_url: str = "http://eve-tools:8090"
    tools_api_key: str = ""
    skills_dir: Path = Path("skills")
    # A specialist's own model+tool loop; not the outer eve<->tools cycle,
    # which is bounded separately below (design doc section 3).
    specialist_max_iterations: int = 6
    # The outer eve<->tools cycle. LangGraph's platform recursion_limit
    # defaults to 10007 and cannot be set at `.compile()` time, so the graph
    # counts its own steps instead - see eve/graph.py.
    max_tool_loop_iterations: int = 6
    dynamic_tools_cap: int = 8

    # Memory (Phase 2). Eve keeps its own small pool rather than reaching into
    # Aegra's internal db_manager.lg_pool: that is a private attribute path,
    # and a silent rename in an aegra-api bump would break memory in
    # production to save fifteen lines. Defaults to Aegra's own DATABASE_URL
    # so the cluster needs no new variable.
    database_url: str = ""
    memory_token_budget: int = 1200
    memory_episodic_half_life_days: float = 90.0
    # The ceiling on how long recall may wait for the embedding before
    # shipping lexical-only. Every millisecond here is spent before Eve's
    # first token (ADR 0002 as amended).
    memory_recall_embed_budget_ms: int = 120
    memory_profile_cap: int = 40
    memory_household_cap: int = 60
    memory_digest_every_n_turns: int = 6

    # Phase 4 (Ambient). See docs/superpowers/specs/
    # 2026-08-23-eve-ambient-design.md sections 5 and 8.2.
    #
    # Off by default: this is the one subsystem that speaks without being
    # spoken to, so a deployment that has not deliberately enabled it must
    # send nothing.
    ambient_enabled: bool = False
    ambient_poll_interval_seconds: int = 300
    ambient_daily_cap: int = 6
    ambient_quiet_hours: str = "21:00-07:00"
    ambient_cooldown_hours: int = 6
    ambient_calendar_lookahead_minutes: int = 90
    # The client returns everything inside this horizon (not just the
    # lookahead) so a change to an event still days away is detected as soon
    # as it happens; only the lookahead governs which events are "starting
    # soon" (fix round 1 item B).
    ambient_calendar_horizon_days: int = 14
    # The impersonation credential (design section 6.1). Held by eve-ambient,
    # which presents it, and by eve, which verifies it.
    ambient_token: str = ""
    ambient_ha_webhook_secret: str = ""
    ambient_ntfy_base_url: str = ""
    ambient_ntfy_topic: str = ""
    ambient_ntfy_token: str = ""
    ambient_thread_url_template: str = ""
    ambient_aegra_base_url: str = "http://eve:2026"

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        if not self.database_url:
            self.database_url = os.environ.get("DATABASE_URL", "")
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
        if self.ambient_token and len(self.ambient_token) < 32:
            raise ValueError(
                "EVE_AMBIENT_TOKEN must be at least 32 characters: it "
                "authenticates as any family member, so a guessable value "
                "fails open"
            )
        if self.ambient_enabled and not self.ambient_token:
            # Enabled-without-a-token still polls, filters, and spends a
            # model call per signal, then fails every delivery on a 401
            # while retrying forever - the least diagnosable failure this
            # subsystem can have. Refuse at startup instead.
            raise ValueError(
                "EVE_AMBIENT_TOKEN is required when EVE_AMBIENT_ENABLED=true"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
