import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from eve.auth import authenticate, extract_bearer
from eve.family import Family, Member

NOAH = Member(
    sub="sub-noah",
    name="Noah",
    role="adult",
    timezone="America/Toronto",
    permissions=frozenset({"spend"}),
)
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _token(**overrides):
    claims = {
        "sub": "sub-noah",
        "iss": "https://authentik.test/application/o/eve/",
        "aud": "eve",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256")


@pytest.fixture
def oidc(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    monkeypatch.setattr("eve.auth._signing_key_for", lambda _t: PRIVATE_KEY.public_key())
    monkeypatch.setenv("EVE_AUTH_MODE", "oidc")
    monkeypatch.setenv("EVE_OIDC_ISSUER", "https://authentik.test/application/o/eve/")
    monkeypatch.setenv("EVE_OIDC_AUDIENCE", "eve")
    monkeypatch.setenv("EVE_OIDC_JWKS_URL", "https://authentik.test/jwks")
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_extract_bearer_handles_bytes_headers():
    assert extract_bearer({b"authorization": b"Bearer abc"}) == "abc"
    assert extract_bearer({"Authorization": "Bearer abc"}) == "abc"


async def test_valid_token_yields_member_identity_and_permissions(oidc):
    user = await authenticate({"Authorization": f"Bearer {_token()}"})
    assert user["identity"] == "sub-noah"
    assert user["display_name"] == "Noah"
    assert user["role"] == "adult"
    assert user["permissions"] == ["spend"]
    assert user["is_authenticated"] is True


async def test_expired_token_is_rejected(oidc):
    stale = _token(exp=int(time.time()) - 10)
    with pytest.raises(Exception):
        await authenticate({"Authorization": f"Bearer {stale}"})


async def test_wrong_audience_is_rejected(oidc):
    with pytest.raises(Exception):
        await authenticate({"Authorization": f"Bearer {_token(aud='other-app')}"})


async def test_missing_token_is_rejected(oidc):
    with pytest.raises(Exception):
        await authenticate({})


async def test_subject_not_in_roster_is_rejected(oidc):
    with pytest.raises(Exception):
        await authenticate({"Authorization": f"Bearer {_token(sub='sub-stranger')}"})


async def test_dev_mode_maps_a_static_token(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    monkeypatch.setenv("EVE_AUTH_MODE", "dev")
    monkeypatch.setenv("EVE_DEV_TOKENS", '{"tok-noah": "sub-noah"}')
    from eve.settings import get_settings

    get_settings.cache_clear()

    user = await authenticate({"Authorization": "Bearer tok-noah"})
    assert user["identity"] == "sub-noah"
    get_settings.cache_clear()


async def test_dev_mode_rejects_an_unknown_static_token(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    monkeypatch.setenv("EVE_AUTH_MODE", "dev")
    monkeypatch.setenv("EVE_DEV_TOKENS", '{"tok-noah": "sub-noah"}')
    from eve.settings import get_settings

    get_settings.cache_clear()

    with pytest.raises(Exception):
        await authenticate({"Authorization": "Bearer tok-forged"})
    get_settings.cache_clear()
