"""One-time provisioning of a member's WHOOP or Oura credential.

    uv run python -m scripts.health_oauth_setup whoop <member_sub>

Runs the authorization-code flow against a loopback redirect, then writes the
first `eve_oauth_token` row. Run once per member per device. After that
`oauth_store` keeps the row current on its own - which for WHOOP means
rotating the refresh token on every refresh, the reason that table exists.

Mirrors `scripts/gmail_oauth_setup.py` in shape. It does NOT write to Vault:
the credential's home is Postgres, not a secret store, because it changes
without a human involved.

Requires EVE_TOOLS_DATABASE_URL and the provider's client id/secret in the
environment. Point the DSN at the same database the cluster uses, or run it
against a port-forward.
"""

from __future__ import annotations

import asyncio
import secrets
import sys
import webbrowser
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

REDIRECT_PORT = 8321
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"

_PROVIDERS = {
    "whoop": {
        "authorize": "https://api.prod.whoop.com/oauth/oauth2/auth",
        "token": "https://api.prod.whoop.com/oauth/oauth2/token",
        # `offline` is what makes WHOOP issue a refresh token at all. Omit it
        # and provisioning looks like it worked until the access token expires
        # an hour later with nothing to renew it.
        "scope": (
            "offline read:recovery read:sleep read:workout read:cycles "
            "read:profile"
        ),
    },
    "oura": {
        "authorize": "https://cloud.ouraring.com/oauth/authorize",
        "token": "https://api.ouraring.com/oauth/token",
        "scope": "daily heartrate personal",
    },
}


def authorize_url(provider: str, client_id: str, redirect_uri: str, state: str) -> str:
    config = _PROVIDERS.get(provider)
    if config is None:
        raise ValueError(
            f"unknown provider {provider!r}; expected one of "
            f"{', '.join(sorted(_PROVIDERS))}"
        )
    query = urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": config["scope"],
        "state": state,
    })
    return f"{config['authorize']}?{query}"


def expires_at(token_response: dict) -> datetime | None:
    """None when the provider states no expiry - an ordinary row whose
    refresh path is never entered."""
    expires_in = token_response.get("expires_in")
    if not expires_in:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(expires_in))


def _await_code(expected_state: str) -> str:
    """Serve exactly one loopback request and return its `code`."""
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib's casing
            params = parse_qs(urlparse(self.path).query)
            captured.update({k: v[0] for k, v in params.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab.")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("localhost", REDIRECT_PORT), Handler)
    server.handle_request()
    server.server_close()

    if captured.get("state") != expected_state:
        raise RuntimeError("state mismatch on the OAuth callback; start over")
    code = captured.get("code")
    if not code:
        raise RuntimeError(f"no code in the callback: {captured}")
    return code


async def _exchange(provider: str, code: str, client_id: str, secret: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            _PROVIDERS[provider]["token"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "client_secret": secret,
            },
        )
        response.raise_for_status()
        return response.json()


async def _main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in _PROVIDERS:
        print(__doc__)
        print(f"providers: {', '.join(sorted(_PROVIDERS))}")
        return 2
    provider, member_sub = sys.argv[1], sys.argv[2]

    from eve_tools import oauth_store
    from eve_tools.settings import get_tools_settings

    settings = get_tools_settings()
    client_id = getattr(settings, f"{provider}_client_id")
    secret = getattr(settings, f"{provider}_client_secret")
    if not client_id or not secret:
        print(
            f"set EVE_TOOLS_{provider.upper()}_CLIENT_ID and "
            f"EVE_TOOLS_{provider.upper()}_CLIENT_SECRET first",
            file=sys.stderr,
        )
        return 1

    state = secrets.token_urlsafe(16)
    url = authorize_url(provider, client_id, REDIRECT_URI, state)
    print(f"\nOpening {provider} authorization. If nothing opens, visit:\n{url}\n")
    webbrowser.open(url)

    code = await asyncio.to_thread(_await_code, state)
    tokens = await _exchange(provider, code, client_id, secret)

    if provider == "whoop" and not tokens.get("refresh_token"):
        print(
            "WHOOP returned no refresh_token - the `offline` scope was not "
            "granted. Auth will break in an hour. Re-run and approve every "
            "requested scope.",
            file=sys.stderr,
        )
        return 1

    try:
        await oauth_store.save(
            provider,
            member_sub,
            tokens["access_token"],
            tokens.get("refresh_token"),
            expires_at(tokens),
        )
    except Exception as exc:
        print(f"\ncould not store the credential: {exc}", file=sys.stderr)
        print("\n--- token response, store it by hand ---")
        print(tokens)
        return 1

    print(f"\nstored the {provider} credential for {member_sub}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
