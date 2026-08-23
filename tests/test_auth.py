import base64
import hashlib
import hmac
import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from eve.auth import AuthError, authenticate, deny_by_default, extract_bearer, stamp_thread_owner
from eve.family import Family, Member

NOAH = Member(
    sub="sub-noah",
    name="Noah",
    role="adult",
    timezone="America/Toronto",
    permissions=frozenset({"spend"}),
)
KID = Member(
    sub="sub-kid",
    name="Kid",
    role="child",
    timezone="America/Toronto",
    permissions=frozenset({"home.control"}),
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
    with pytest.raises(AuthError, match="Signature has expired"):
        await authenticate({"Authorization": f"Bearer {stale}"})


async def test_wrong_audience_is_rejected(oidc):
    with pytest.raises(AuthError, match="Audience doesn't match"):
        await authenticate({"Authorization": f"Bearer {_token(aud='other-app')}"})


async def test_missing_token_is_rejected(oidc):
    with pytest.raises(AuthError, match="missing Authorization header"):
        await authenticate({})


async def test_subject_not_in_roster_is_rejected(oidc):
    with pytest.raises(AuthError, match="no family member"):
        await authenticate({"Authorization": f"Bearer {_token(sub='sub-stranger')}"})


async def test_token_signed_with_a_different_key_is_rejected(oidc):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {
            "sub": "sub-noah",
            "iss": "https://authentik.test/application/o/eve/",
            "aud": "eve",
            "exp": int(time.time()) + 300,
        },
        other_key,
        algorithm="RS256",
    )
    with pytest.raises(AuthError, match="Signature verification failed"):
        await authenticate({"Authorization": f"Bearer {forged}"})


async def test_token_forging_alg_none_is_rejected(oidc):
    forged = jwt.encode(
        {
            "sub": "sub-noah",
            "iss": "https://authentik.test/application/o/eve/",
            "aud": "eve",
            "exp": int(time.time()) + 300,
        },
        key=None,
        algorithm="none",
    )
    with pytest.raises(AuthError, match="not allowed"):
        await authenticate({"Authorization": f"Bearer {forged}"})


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def test_token_forging_hs256_with_the_public_key_is_rejected(oidc):
    # The classic RS256/HS256 key-confusion attack: sign with HMAC using the
    # RSA public key's bytes as the shared secret, hoping a verifier that
    # trusts the token's own `alg` header will use that same public key to
    # check an HMAC signature. PyJWT's own `encode()` refuses to build this
    # token (it detects a PEM-format key passed as an HMAC secret), so the
    # header/payload/signature are assembled by hand to reproduce exactly
    # what a real attacker would send over the wire.
    public_pem = PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "sub": "sub-noah",
                "iss": "https://authentik.test/application/o/eve/",
                "aud": "eve",
                "exp": int(time.time()) + 300,
            }
        ).encode()
    )
    signature = _b64url(
        hmac.new(public_pem, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    )
    forged = f"{header}.{payload}.{signature}"
    with pytest.raises(AuthError, match="not allowed"):
        await authenticate({"Authorization": f"Bearer {forged}"})


async def test_wrong_issuer_is_rejected(oidc):
    with pytest.raises(AuthError, match="Invalid issuer"):
        await authenticate(
            {"Authorization": f"Bearer {_token(iss='https://evil.test/')}"}
        )


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

    with pytest.raises(AuthError, match="unrecognised development token"):
        await authenticate({"Authorization": "Bearer tok-forged"})
    get_settings.cache_clear()


def _ctx(resource="threads", identity="sub-noah"):
    return SimpleNamespace(resource=resource, user=SimpleNamespace(identity=identity))


async def test_thread_create_returns_an_owner_filter_not_just_a_stamp():
    """A create request replaying another member's thread_id must still be
    checked against the owner filter, not accepted outright.

    Note what this does and does not prove about the running server. In
    aegra-api 0.10.3 the return value asserted below is never consulted on
    thread creation: `api/threads.py:173` reads only a `"metadata"` key out of
    the handler's result, stamps `metadata["owner"]` itself unconditionally at
    `:200`, and blocks the `if_exists` replay with a `user_id` predicate at
    `:186-188`. The test is kept because the SDK's documented contract is that
    a returned filter *is* applied, and a future version may honour it again -
    but a passing assertion here is not evidence that the server does."""
    value = {}
    result = await stamp_thread_owner(_ctx(), value)
    assert value["metadata"]["owner"] == "sub-noah"
    assert result == {"owner": "sub-noah"}


async def test_deny_by_default_denies_runs_too():
    """No former carve-out here: aegra-api 0.10.3 never dispatches an auth
    event with resource="runs" (see the docstring on deny_by_default), so
    this resource name is unreachable in practice - covered anyway so a flat
    `False` stays correct if that ever changes and the SDK starts passing
    it through, which would then need its own handler rather than silently
    falling through here."""
    assert await deny_by_default(_ctx(resource="runs"), {}) is False


async def test_deny_by_default_still_denies_everything_else():
    assert await deny_by_default(_ctx(resource="crons"), {}) is False


AMBIENT_TOKEN = "a" * 40


def _ambient_settings(monkeypatch):
    monkeypatch.setenv("EVE_AUTH_MODE", "dev")
    monkeypatch.setenv("EVE_DEV_TOKENS", '{"tok-noah": "sub-noah"}')
    monkeypatch.setenv("EVE_AMBIENT_TOKEN", AMBIENT_TOKEN)
    from eve.settings import get_settings

    get_settings.cache_clear()


async def test_the_ambient_token_authenticates_as_the_named_member(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    _ambient_settings(monkeypatch)
    user = await authenticate(
        {
            "Authorization": f"Bearer {AMBIENT_TOKEN}",
            "x-eve-on-behalf-of": "sub-noah",
        }
    )
    assert user["identity"] == "sub-noah"
    assert user["is_authenticated"] is True


async def test_a_member_token_cannot_impersonate(monkeypatch):
    """The header is only meaningful alongside the ambient token. If an
    ordinary member could set it, every member could read every thread."""
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH, KID]))
    _ambient_settings(monkeypatch)
    user = await authenticate(
        {"Authorization": "Bearer tok-noah", "x-eve-on-behalf-of": "sub-kid"}
    )
    assert user["identity"] == "sub-noah"


async def test_the_ambient_token_without_the_header_is_refused(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    _ambient_settings(monkeypatch)
    with pytest.raises(AuthError, match="on-behalf-of"):
        await authenticate({"Authorization": f"Bearer {AMBIENT_TOKEN}"})


async def test_an_unknown_subject_is_refused(monkeypatch):
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    _ambient_settings(monkeypatch)
    with pytest.raises(AuthError, match="sub-stranger"):
        await authenticate(
            {
                "Authorization": f"Bearer {AMBIENT_TOKEN}",
                "x-eve-on-behalf-of": "sub-stranger",
            }
        )


async def test_the_ambient_path_is_inert_when_no_token_is_configured(monkeypatch):
    """An empty configured token must never match an empty presented one."""
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    monkeypatch.setenv("EVE_AUTH_MODE", "dev")
    monkeypatch.setenv("EVE_DEV_TOKENS", '{"tok-noah": "sub-noah"}')
    monkeypatch.delenv("EVE_AMBIENT_TOKEN", raising=False)
    from eve.settings import get_settings

    get_settings.cache_clear()
    with pytest.raises(AuthError):
        await authenticate({"Authorization": "Bearer ", "x-eve-on-behalf-of": "sub-noah"})


async def test_bytes_headers_are_handled_on_the_ambient_path(monkeypatch):
    """Aegra hands headers through as bytes in some paths; extract_bearer
    already copes and the new header must too."""
    monkeypatch.setattr("eve.auth.get_family", lambda: Family([NOAH]))
    _ambient_settings(monkeypatch)
    user = await authenticate(
        {
            b"authorization": f"Bearer {AMBIENT_TOKEN}".encode(),
            b"x-eve-on-behalf-of": b"sub-noah",
        }
    )
    assert user["identity"] == "sub-noah"
