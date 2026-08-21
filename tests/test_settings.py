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
