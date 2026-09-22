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
    # Extraction runs after Eve's last token, so its latency never delays a
    # word she says - but it does delay the turn ENDING, because the run is
    # only complete when the graph reaches END. That holds the SSE stream and
    # the client's "done" open for a REFLEX call plus writes. Backgrounding it
    # moves that wait into the gap where the member is typing (ADR 0012).
    memory_extract_background: bool = True
    # How long the next turn waits for the previous turn's extraction before
    # reading memory anyway. Generous next to the 120ms embed budget because
    # it is normally already satisfied - a human had to type in between. When
    # it is not, the degrade is one turn of slightly stale candidates.
    memory_extract_join_budget_ms: int = 5000

    # Reply suggestions (EVE-7). See docs/superpowers/specs/
    # 2026-08-31-eve-suggestions-design.md.
    #
    # Default ON, unlike ambient_enabled and sandbox_enabled: this subsystem
    # reaches nothing outside the process and writes nothing durable.
    suggest_enabled: bool = True
    # Bounds how long the run stays open after Eve's last token. Missing the
    # budget ships no chips rather than delaying the turn ending - the same
    # degradation as recall's embedding arm.
    suggest_budget_ms: int = 1500

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

    # Phase 5a (Self-improvement). See docs/superpowers/specs/
    # 2026-08-27-eve-self-improvement-design.md sections 6.5 and 8.2.
    #
    # Off by default for the same reason ambient_enabled is: this subsystem
    # rewrites Eve's own standing instructions without being asked, so a
    # deployment that has not deliberately enabled it must author nothing.
    self_authoring_enabled: bool = False
    # Rows per scope before evict_over_cap retires the weakest. A starting
    # number, not a derived one: at roughly one sentence each, 20 is a few
    # hundred tokens against memory_token_budget's 1200.
    memory_rule_cap: int = 20

    # Phase 5b (Eval harness). See docs/superpowers/specs/
    # 2026-08-27-eve-eval-harness-design.md section 9.2.
    eval_dataset_limit: int = 200
    # Above this many VOICE-tier calls, `eve-eval run` requires --yes. Both
    # subscription proxies share a max_budget of 20 per 30 days with Noah's
    # own work; a harness that can silently spend the month is one that will.
    eval_voice_call_ceiling: int = 60
    eval_regression_points: int = 10
    eval_dead_rule_days: int = 90
    eval_decision_retention_days: int = 180
    eval_hygiene_apply_enabled: bool = False
    langfuse_host: str = "https://langfuse.chalifour.dev"
    # Overridable rather than hardcoded: none of this repo's Dockerfiles copy
    # `tests/`, so `eve-eval build`/`run` need a working directory that
    # contains this file, or this setting pointed at wherever it lives - a
    # packaging detail Phase 5c owns, not this one.
    eval_turns_file: str = "tests/eval/turns.yaml"

    # Phase 5c (Gated tool code). See docs/superpowers/specs/
    # 2026-08-27-eve-sandboxed-tools-design.md section 10.
    sandbox_enabled: bool = False
    sandbox_base_url: str = "http://eve-sandbox:8091"
    sandbox_api_key: str = ""
    sandbox_timeout_seconds: int = 5
    sandbox_memory_mb: int = 256
    sandbox_max_output_bytes: int = 65536
    sandbox_max_concurrency: int = 4

    # Phase 6 (Eve's computer). See docs/superpowers/specs/
    # 2026-08-28-eve-computer-design.md.
    computer_enabled: bool = False
    computer_base_url: str = "http://eve-computer:8092"
    computer_api_key: str = ""
    # How long the poller waits for the box to answer about a task before
    # giving up and marking it stale (design doc: "Reporting back" - covers a
    # pod restart mid-run, since eve-computer keeps no task state on disk).
    computer_task_stale_minutes: int = 120

    # EVE-4 (ACP tools). See docs/superpowers/specs/
    # 2026-09-01-eve-acp-tools-design.md.
    #
    # Off by default for the same reason ambient_enabled and computer_enabled
    # are: this one opens pull requests under Eve's own GitHub account, and a
    # deployment that has not deliberately enabled it must open none.
    #
    # No base URL of its own: sessions run on eve-computer, so
    # computer_base_url and computer_api_key already point at the right box.
    coding_enabled: bool = False
    # The tiebreak when neither the task nor the member's preferences point
    # anywhere (EVE-24: "It should be the default ACP"). The DeepSeek harness
    # boots Noah's own profile, pulled from git, so the untargeted case is the
    # one configured the way he already works - and it reaches LiteLLM like
    # every other agent, so the tiebreak costs no more than the one it
    # replaced. Still a setting: a deployment without a profile repository
    # can name `codex` and lose nothing but the preferences.
    coding_default_agent: str = "dsh"
    # Deliberately not ambient_poll_interval_seconds. The supervisor is a
    # control loop with an agent waiting on the other end, not a notification
    # pipeline; 300s of latency per conversational turn would make Eve a
    # worse correspondent than the member she is standing in for.
    coding_supervisor_interval_seconds: int = 20
    coding_session_stale_minutes: int = 120
    # Whatever the budget is, a loop that blows it has to answer in English
    # (graph.py's _LOOP_EXHAUSTED). Hitting this parks the session and asks
    # the member rather than stalling silently.
    coding_max_supervisor_turns: int = 30
    coding_catalogue_ttl_seconds: int = 3600
    # The outermost bound. The box enforces per-turn and per-session limits
    # of its own, but a session parked on `blocked` is not running anything
    # for the box to time out - it is waiting on a human who may never
    # answer, holding a subprocess, a worktree, and a concurrency slot.
    coding_session_timeout_seconds: int = 28800

    # EVE-27 (auto-review pull requests). See docs/superpowers/specs/
    # 2026-09-22-auto-review-prs-design.md.
    #
    # Off by default for the same reason coding_enabled is, and for one more:
    # this is the first path where the public internet reaches a cluster
    # service on a schedule Eve does not control. A deployment that has not
    # deliberately enabled outbound review comments under Eve's GitHub
    # identity posts none.
    review_enabled: bool = False
    # GitHub's webhook secret. The signature is an HMAC over the raw body,
    # not a shared secret compared directly, so this is the HMAC key.
    review_webhook_secret: str = ""
    # The allowlist. A webhook can name any repository; only these spend
    # tokens. Empty means none, which is the safe reading of "not configured".
    review_repos: list[str] = Field(default_factory=list)
    # The reviewer for a human-authored pull request, which has no
    # implementing pair to differ from. Deliberately a strong model: this is
    # the case where nothing else constrains the choice.
    review_default_agent: str = "claude"
    review_default_model: str = "anthropic/claude-sonnet-5"

    # EVE-32 (re-review when new commits land). Opt-in by construction
    # rather than by flag: only a pull request Eve has already been asked to
    # review is re-reviewed, so the original label is still the signal. This
    # flag exists to turn the behaviour off without turning reviewing off.
    review_on_push: bool = True
    # How long a pull request must go without a push before it is
    # re-reviewed. A burst of commits is one review, not one per commit.
    review_debounce_seconds: int = 300
    # Reviews per pull request, counting the first, after which a push
    # triggers nothing and a human relabels or asks by hand. Bounds a long
    # back-and-forth that would otherwise spend indefinitely.
    review_max_per_pr: int = 3

    # EVE-31 (Eve monitors the pull requests she opens). A review or comment
    # on a pull request Eve opened starts an `address` session: a coding
    # agent on the PR's own branch that applies the receiving-code-review
    # discipline, pushes its fixes, and replies on the thread. Off by
    # default for review_enabled's reason, and one more: it pushes.
    pr_followup_enabled: bool = False
    # Eve's own GitHub account. Feedback authored by it is her own replies
    # and reviews, and addressing those would be a loop, not a conversation.
    github_login: str = ""
    # A reviewer's inline comments arrive one webhook each; wait for the
    # burst to settle so one session addresses the whole review.
    pr_followup_debounce_seconds: int = 180
    # Address sessions per pull request. Past this Eve stops and the member
    # takes over: two agents trading review rounds forever is exactly the
    # churn EVE-27's no-auto-fix non-goal warned about.
    pr_followup_max_per_pr: int = 5

    # EVE-25 (Scheduled routines). See docs/superpowers/specs/
    # 2026-09-21-eve-routines-design.md.
    #
    # Off by default for the same reason ambient_enabled and coding_enabled
    # are: a routine is a recurring paid VOICE-tier turn that nobody is
    # watching, so a deployment that has not deliberately accepted standing
    # spend must run none.
    routines_enabled: bool = False
    # Consecutive INFRASTRUCTURE failures before a routine pauses itself and
    # says so once. A NOTHING veto is not a failure: silence is the routine
    # working. Five is roughly a day of hourly retries.
    routine_failure_limit: int = 5
    routine_max_title_chars: int = 80
    # The instruction is replayed into a VOICE turn on every firing, so its
    # length is a standing cost rather than a one-off one.
    routine_max_instruction_chars: int = 2000

    # EVE-26 (Linear). See docs/superpowers/specs/
    # 2026-09-21-eve-linear-agent-design.md.
    #
    # Off by default, like ambient_enabled and coding_enabled: this subsystem
    # acts on input from outside the household, so a deployment that has not
    # deliberately enabled it must refuse every webhook.
    linear_enabled: bool = False
    # Held by eve-ambient, which verifies with it. NOT a credential for
    # reaching Linear - that token lives only in eve-tools (ADR 0006).
    linear_webhook_secret: str = ""
    # The containment boundary for prompt injection. Issue text reaches an
    # agent that writes code and opens pull requests; the one thing that text
    # can never widen is which repos are reachable, because this is read from
    # settings rather than from anything Linear sent.
    linear_repo_allowlist: list[str] = []
    # Against Linear's 30-minute stale threshold. A coding agent can work
    # longer than that without producing a supervisor decision, and a
    # stale-looking session invites a human to intervene in work going fine.
    linear_heartbeat_minutes: int = 10
    # Concurrent Linear-originated coding sessions. Five people delegating at
    # once should queue, not fan out to five agents on one box.
    linear_max_live_sessions: int = 3

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
        if self.sandbox_api_key and len(self.sandbox_api_key) < 32:
            raise ValueError(
                "EVE_SANDBOX_API_KEY must be at least 32 characters: it "
                "authenticates a service that executes code, so a guessable "
                "value fails open"
            )
        if self.sandbox_enabled and not self.sandbox_api_key:
            raise ValueError(
                "EVE_SANDBOX_API_KEY is required when EVE_SANDBOX_ENABLED=true"
            )
        if self.computer_api_key and len(self.computer_api_key) < 32:
            raise ValueError(
                "EVE_COMPUTER_API_KEY must be at least 32 characters: it "
                "authenticates a service that takes real-world actions, so "
                "a guessable value fails open"
            )
        if self.computer_enabled and not self.computer_api_key:
            raise ValueError(
                "EVE_COMPUTER_API_KEY is required when EVE_COMPUTER_ENABLED=true"
            )
        if self.linear_webhook_secret and len(self.linear_webhook_secret) < 32:
            raise ValueError(
                "EVE_LINEAR_WEBHOOK_SECRET must be at least 32 characters: it "
                "is the only thing distinguishing Linear from anyone who "
                "knows the URL, so a guessable value fails open"
            )
        if self.linear_enabled:
            # Enabled-but-unconfigured accepts webhooks, spends a dispatch,
            # then fails every emission on a 401 while Linear retries - the
            # least diagnosable failure this subsystem can have.
            if not self.linear_webhook_secret:
                raise ValueError(
                    "EVE_LINEAR_WEBHOOK_SECRET is required when "
                    "EVE_LINEAR_ENABLED=true"
                )
            if not self.linear_repo_allowlist:
                raise ValueError(
                    "EVE_LINEAR_REPO_ALLOWLIST is required when "
                    "EVE_LINEAR_ENABLED=true: it is the only boundary between "
                    "an issue anyone can file and a repo Eve can write to"
                )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
