import pytest

from eve.settings import Settings


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
