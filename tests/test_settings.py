from pathlib import Path

import pytest

from eve.settings import Settings, get_settings


def test_defaults_are_development_and_dev_auth():
    s = Settings()
    assert s.env == "development"
    assert s.auth_mode == "dev"


def test_embedding_pin_is_present():
    # Pinned forever: changing either value requires re-embedding all memory.
    # Amended 2026-08-18 (ADR 0003): the Gemini conditional resolved, so the
    # pin moved from openai:text-embedding-3-small to the Gemini model below.
    s = Settings()
    assert s.embedding_model == "gemini/gemini-embedding-001"
    assert s.embedding_dims == 1536


def test_production_refuses_dev_auth_mode():
    with pytest.raises(ValueError, match="EVE_AUTH_MODE"):
        Settings(env="production", auth_mode="dev")


OIDC = {
    "oidc_issuer": "https://authentik.test/application/o/eve/",
    "oidc_audience": "eve",
    "oidc_jwks_url": "https://authentik.test/jwks",
}


def test_production_accepts_oidc_auth_mode():
    s = Settings(env="production", auth_mode="oidc", **OIDC)
    assert s.auth_mode == "oidc"


@pytest.mark.parametrize("missing", sorted(OIDC))
def test_oidc_mode_requires_its_issuer_audience_and_jwks_url(missing):
    """A deployment missing any one of these authenticates nobody: every
    request fails a claim or signature check and answers 401, which reads
    like a token problem. Refuse to start rather than fail closed silently."""
    incomplete = {**OIDC, missing: ""}
    with pytest.raises(ValueError, match=f"EVE_{missing.upper()}"):
        Settings(auth_mode="oidc", **incomplete)


def test_dev_mode_does_not_require_oidc_configuration():
    assert Settings(auth_mode="dev").auth_mode == "dev"


def test_env_prefix_is_eve(monkeypatch):
    monkeypatch.setenv("EVE_LITELLM_BASE_URL", "http://localhost:4000")
    assert Settings().litellm_base_url == "http://localhost:4000"


def test_database_url_falls_back_to_the_aegra_variable(monkeypatch):
    """Aegra is configured with DATABASE_URL and the cluster manifests set
    exactly that. Requiring a second EVE_DATABASE_URL saying the same thing
    is a deployment footgun that would surface as memory silently failing."""
    monkeypatch.delenv("EVE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://eve:eve@db:5432/eve")
    get_settings.cache_clear()
    assert get_settings().database_url == "postgresql://eve:eve@db:5432/eve"


def test_explicit_eve_database_url_wins(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://shared/eve")
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://dedicated/eve")
    get_settings.cache_clear()
    assert get_settings().database_url == "postgresql://dedicated/eve"


def test_memory_defaults_match_the_spec():
    s = get_settings()
    assert s.memory_token_budget == 1200
    assert s.memory_episodic_half_life_days == 90.0
    assert s.memory_recall_embed_budget_ms == 120
    assert s.memory_profile_cap == 40
    assert s.memory_household_cap == 60


def test_phase_3_settings_have_sane_defaults():
    s = Settings()
    assert s.tools_base_url == "http://eve-tools:8090"
    assert s.tools_api_key == ""
    assert s.skills_dir == Path("skills")
    assert s.specialist_max_iterations == 6
    assert s.dynamic_tools_cap == 8


def test_phase_4_ambient_defaults():
    s = Settings()
    assert s.ambient_enabled is False
    assert s.ambient_poll_interval_seconds == 300
    assert s.ambient_daily_cap == 6
    assert s.ambient_quiet_hours == "21:00-07:00"
    assert s.ambient_cooldown_hours == 6
    assert s.ambient_calendar_lookahead_minutes == 90
    assert s.ambient_aegra_base_url == "http://eve:2026"
    assert s.ambient_token == ""


def test_a_short_ambient_token_is_refused_at_startup():
    """An impersonation secret is the one credential in this deployment that
    can speak as any family member. A guessable one is worse than none,
    because it fails open rather than closed."""
    with pytest.raises(ValueError, match="EVE_AMBIENT_TOKEN"):
        Settings(ambient_token="short")


def test_an_empty_ambient_token_is_allowed():
    """Ambient off is the default; an unset token must not stop Eve booting."""
    assert Settings(ambient_token="").ambient_token == ""


def test_a_long_ambient_token_is_accepted():
    assert Settings(ambient_token="a" * 32).ambient_token == "a" * 32


def test_enabling_ambient_without_a_token_is_refused_at_startup():
    """Enabled-without-a-token would poll, filter, and spend a model call per
    signal, then fail every delivery on a 401 forever - the least
    diagnosable failure this subsystem can have."""
    with pytest.raises(ValueError, match="EVE_AMBIENT_TOKEN"):
        Settings(ambient_enabled=True, ambient_token="")


def test_enabling_ambient_with_a_token_is_accepted():
    s = Settings(ambient_enabled=True, ambient_token="a" * 32)
    assert s.ambient_enabled is True


def test_self_authoring_is_off_by_default():
    """The one subsystem that rewrites Eve's own standing instructions must
    not be live in a deployment that did not ask for it."""
    assert Settings().self_authoring_enabled is False


def test_rule_cap_has_a_default():
    assert Settings().memory_rule_cap == 20


def test_eval_defaults():
    from eve.settings import Settings

    s = Settings()
    assert s.eval_dataset_limit == 200
    assert s.eval_voice_call_ceiling == 60
    assert s.eval_regression_points == 10
    assert s.eval_dead_rule_days == 90
    assert s.eval_decision_retention_days == 180
    assert s.eval_hygiene_apply_enabled is False
    assert s.eval_turns_file == "tests/eval/turns.yaml"


def test_eval_turns_file_is_overridable(monkeypatch):
    """None of this repo's Dockerfiles copy `tests/`, so the hardcoded
    default only works from a working directory shaped like this repo.
    Whoever builds Phase 5c's CronJob image needs to point this somewhere
    else without a code change."""
    from eve.settings import Settings

    monkeypatch.setenv("EVE_EVAL_TURNS_FILE", "/data/turns.yaml")
    assert Settings().eval_turns_file == "/data/turns.yaml"
