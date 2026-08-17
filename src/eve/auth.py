"""Authentication and per-member resource scoping.

Registered with Aegra via `aegra.json` -> `auth.path`. Raising from the
`@auth.authenticate` handler produces a 401; an authorization handler denying
a resource produces a 403.

Two modes. `oidc` validates an Authentik-issued JWT against its JWKS. `dev`
maps an opaque static token to a roster subject for local work, and is
unreachable in production because Settings refuses that combination at
startup (spec section 8.1).
"""

from __future__ import annotations

from functools import lru_cache

import jwt
from jwt import PyJWKClient
from langgraph_sdk import Auth

from eve.family import UnknownMemberError, get_family
from eve.settings import get_settings

auth = Auth()


class AuthError(Exception):
    """Raised for any failure to authenticate; Aegra turns this into a 401."""


def extract_bearer(headers: dict) -> str:
    """Pull the bearer token out of headers whose keys/values may be bytes."""
    for key, value in headers.items():
        name = key.decode() if isinstance(key, bytes) else key
        if name.lower() != "authorization":
            continue
        raw = value.decode() if isinstance(value, bytes) else value
        if not raw.lower().startswith("bearer "):
            raise AuthError("Authorization header is not a bearer token")
        return raw[7:].strip()
    raise AuthError("missing Authorization header")


@lru_cache(maxsize=1)
def _jwk_client() -> PyJWKClient:
    return PyJWKClient(get_settings().oidc_jwks_url, cache_keys=True)


def _signing_key_for(token: str):
    return _jwk_client().get_signing_key_from_jwt(token).key


def _subject_from_token(token: str) -> str:
    settings = get_settings()
    if settings.auth_mode == "dev":
        subject = settings.dev_tokens.get(token)
        if subject is None:
            raise AuthError("unrecognised development token")
        return subject
    try:
        claims = jwt.decode(
            token,
            _signing_key_for(token),
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc
    return claims["sub"]


@auth.authenticate
async def authenticate(headers: dict) -> dict:
    subject = _subject_from_token(extract_bearer(headers))
    try:
        member = get_family().get(subject)
    except UnknownMemberError as exc:
        raise AuthError(str(exc)) from exc
    return {
        "identity": member.sub,
        "display_name": member.name,
        "role": member.role,
        "permissions": sorted(member.permissions),
        "is_authenticated": True,
    }


# Resource scoping. A family member may not list, read, or resume another
# member's threads. Enforced from day one because Phase 2 starts writing
# personal memory immediately.


@auth.on.threads.create
async def stamp_thread_owner(ctx, value):
    value.setdefault("metadata", {})["owner"] = ctx.user.identity
    return value


@auth.on.threads.read
async def only_own_threads(ctx, value):
    return {"owner": ctx.user.identity}


@auth.on.threads.search
async def only_own_threads_in_search(ctx, value):
    return {"owner": ctx.user.identity}
