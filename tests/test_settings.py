import pytest

from eve.settings import Settings


def test_defaults_are_development_and_dev_auth():
    s = Settings()
    assert s.env == "development"
    assert s.auth_mode == "dev"


def test_embedding_pin_is_present():
    # Pinned forever: changing either value requires re-embedding all memory.
    s = Settings()
    assert s.embedding_model == "openai:text-embedding-3-small"
    assert s.embedding_dims == 1536


def test_production_refuses_dev_auth_mode():
    with pytest.raises(ValueError, match="EVE_AUTH_MODE"):
        Settings(env="production", auth_mode="dev")


def test_production_accepts_oidc_auth_mode():
    s = Settings(env="production", auth_mode="oidc")
    assert s.auth_mode == "oidc"


def test_env_prefix_is_eve(monkeypatch):
    monkeypatch.setenv("EVE_LITELLM_BASE_URL", "http://localhost:4000")
    assert Settings().litellm_base_url == "http://localhost:4000"
