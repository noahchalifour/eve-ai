"""Authentication and per-member resource scoping.

Registered with Aegra via `aegra.json` -> `auth.path`. Raising from the
`@auth.authenticate` handler produces a 401; an authorization handler denying
a resource produces a 403.

Two modes, plus one additional accepted credential. `oidc` validates an
Authentik-issued JWT against its JWKS. `dev` maps an opaque static token to a
roster subject for local work, and is unreachable in production because
Settings refuses that combination at startup (spec section 8.1). Independent
of the mode, a bearer that exactly matches `Settings.ambient_token` plus an
`x-eve-on-behalf-of: <sub>` header authenticates as that member - this is how
the unattended ambient service creates threads as a family member (spec
section 6.1). It is not a third `EVE_AUTH_MODE`: production runs `oidc` and
the ambient credential has to work there too, so it is checked before the
configured mode's own path rather than being an alternative to it.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from hmac import compare_digest

import jwt
from jwt import PyJWKClient
from langgraph_sdk import Auth

from eve.family import UnknownMemberError, get_family
from eve.settings import get_settings

logger = logging.getLogger(__name__)

auth = Auth()

_ON_BEHALF_OF = "x-eve-on-behalf-of"


class AuthError(Auth.exceptions.HTTPException):
    """Raised for any failure to authenticate. Subclasses the SDK's HTTPException
    so the 401 status is guaranteed rather than dependent on how the server maps
    an arbitrary exception."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=401, detail=detail)


def extract_bearer(headers: dict) -> str:
    """Pull the bearer token out of headers whose keys/values may be bytes."""
    for key, value in headers.items():
        name = key.decode() if isinstance(key, bytes) else key
        if name.lower() != "authorization":
            continue
        raw = value.decode() if isinstance(value, bytes) else value
        if not isinstance(raw, str):
            raise AuthError("Authorization header has an unsupported value type")
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
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc
    return claims["sub"]


def _header(headers: dict, name: str) -> str | None:
    """Same bytes-tolerance as extract_bearer, for one named header.

    `name` is lower-cased here too, so a caller that passes a mixed-case
    header name still matches instead of silently never firing. A resolved
    value that is neither `str` nor `bytes` is treated as absent rather than
    returned verbatim - it would otherwise reach `Family.get` and raise an
    unhandled `TypeError` instead of the intended 401.
    """
    target = name.lower()
    for key, value in headers.items():
        candidate = key.decode() if isinstance(key, bytes) else key
        if candidate.lower() != target:
            continue
        if isinstance(value, bytes):
            return value.decode()
        if isinstance(value, str):
            return value
        return None
    return None


def _ambient_subject(headers: dict, token: str) -> str | None:
    """The impersonation path (design section 6.1). Returns None - falling
    through to the configured auth mode - unless the presented bearer is
    exactly the configured ambient token.

    Deliberately not a third EVE_AUTH_MODE: production runs `oidc` and this
    credential has to work there too, so it is an additional accepted
    credential rather than an alternative mode. `compare_digest` because a
    timing-distinguishable comparison of an impersonation secret is worth
    avoiding for the cost of one import - and it runs on the `.encode()`d
    bytes rather than the `str` operands, because `compare_digest` raises
    `TypeError` on a `str` operand containing non-ASCII, which a hostile or
    merely malformed bearer can trigger; the bytes form never raises for this
    input class and is equivalent for the legitimate token. The configured
    value is also stripped, matching `extract_bearer`'s stripping of the
    presented token, so a trailing newline from an `.env` copy-paste doesn't
    silently make the credential impossible to present.

    Logging the impersonation is the caller's job, once the subject has
    actually resolved to a real member - this function only decides whether
    the ambient credential was presented, not whether it identifies anyone.
    """
    configured = get_settings().ambient_token.strip()
    if not configured or not compare_digest(token.encode(), configured.encode()):
        return None
    subject = _header(headers, _ON_BEHALF_OF)
    if not subject:
        raise AuthError(f"the ambient token requires an {_ON_BEHALF_OF} header")
    return subject


@auth.authenticate
async def authenticate(headers: dict) -> dict:
    token = extract_bearer(headers)
    subject = _ambient_subject(headers, token)
    impersonated = subject is not None
    if subject is None:
        subject = _subject_from_token(token)
    try:
        member = get_family().get(subject)
    except UnknownMemberError as exc:
        raise AuthError(str(exc)) from exc
    if impersonated:
        # Logged only after the roster lookup above succeeds, so the audit
        # trail never claims an impersonation happened for a subject that
        # was in fact refused.
        logger.info("ambient impersonation authenticated as %s", subject)
    return {
        "identity": member.sub,
        "display_name": member.name,
        "role": member.role,
        "permissions": sorted(member.permissions),
        "is_authenticated": True,
    }


# Resource scoping. A family member may not list, read, resume, update, or
# delete another member's threads. Enforced from day one because Phase 2
# starts writing personal memory immediately.
#
# The SDK dispatches to the most specific matching handler and, per its own
# documented request-processing flow, accepts any request with no matching
# handler at all if no global handler is registered. `deny_by_default` closes
# that gap: every resource/action needs an explicit handler below, so a
# forgotten action (e.g. `threads.create_run`, the resume path) fails closed
# instead of silently passing through unfiltered.


@auth.on
async def deny_by_default(ctx, value):
    """Fail closed for everything without an explicit handler below.

    Runs need no carve-out here: empirically (aegra-api 0.10.3's
    `core/auth_registry.py` `ROUTE_AUTH_MAP`, an exhaustive route -> resource
    map), no route ever authorizes under `resource="runs"` - every run route
    dispatches under `resource="threads"`: creation as `create_run`, a single
    run read as `read`, listing a thread's runs as `search`, cancel as
    `update`, and delete as `delete`. `only_own_threads` below scopes all five
    to the caller. If a future Aegra version starts dispatching a
    real `resource="runs"` event, this flat deny would 403 it, and
    `test_run_is_not_blocked_by_authorization` in
    tests/test_integration.py is the regression guard that will catch it."""
    return False


@auth.on.threads.create
async def stamp_thread_owner(ctx, value):
    metadata = value.get("metadata") or {}
    metadata["owner"] = ctx.user.identity
    value["metadata"] = metadata
    return {"owner": ctx.user.identity}


@auth.on.threads
async def only_own_threads(ctx, value):
    """Covers every thread action besides `create` above: read, search,
    update, delete, and create_run (resuming a thread with a new run)."""
    return {"owner": ctx.user.identity}


@auth.on.store
async def scope_store_to_member(ctx, value):
    """Phase 2 writes personal memory into the store; scope every operation
    to the caller's own namespace so one member's memory never leaks into
    another's."""
    namespace = tuple(value["namespace"]) if value.get("namespace") else ()
    if not namespace or namespace[0] != ctx.user.identity:
        namespace = (ctx.user.identity, *namespace)
    value["namespace"] = namespace


# Assistants are shared graph configuration (the "eve" graph from
# aegra.json), not per-member data, so any authenticated family member may
# look them up. `deny_by_default` would otherwise block the assistant lookup
# a LangGraph client needs to run a conversation.


@auth.on.assistants.read
async def allow_assistant_read(ctx, value):
    return None


@auth.on.assistants.search
async def allow_assistant_search(ctx, value):
    return None
