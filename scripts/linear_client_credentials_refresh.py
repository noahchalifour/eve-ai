"""Mint a fresh Linear `app`-actor token via the client_credentials grant and
store it where eve-tools reads it.

    uv run python scripts/linear_client_credentials_refresh.py

A client_credentials token has no refresh token and is valid 30 days
(design doc: docs/superpowers/specs/2026-09-21-eve-linear-agent-design.md).
When eve-tools also has `linear_client_id` and `linear_client_secret`,
`linear_client.py` mints a fresh token in-process on a 401 and retries once
(EVE-41), so an expired token here no longer breaks emission. Without them,
re-run this before the token expires, or every emission fails with a 401.
Either way this script is what keeps the stored token current, so a restart
does not start from an expired one.

Reads `linear_client_id` and `linear_client_secret` from
kv/credentials/eve-tools (the OAuth application's own credentials, from
https://linear.app/settings/api/applications - store them once with:

    vault kv patch kv/credentials/eve-tools \\
      linear_client_id="..." linear_client_secret="..."

The app must have "client credentials tokens" toggled on in its settings, or
Linear's token endpoint answers with an unsupported_grant_type error). The
resulting access token is written back to `linear_api_token` at the same
path - the property `eve_tools.settings.ToolsSettings.linear_api_token`
resolves via the ExternalSecret, same as every other eve-tools credential
(ADR 0006: this is the only service that ever holds it).

Mirrors scripts/gmail_oauth_setup.py's Vault plumbing (shell out to the
operator's own `vault` login rather than add an HTTP client the deployment
has no other use for) but not its shape otherwise: there is no browser
consent step and no per-member entry to merge, because a client_credentials
grant is a single workspace-wide token, not one obtained per person.
"""

from __future__ import annotations

import json
import subprocess
import sys

import httpx

_VAULT_PATH = "kv/credentials/eve-tools"
_CLIENT_ID_PROPERTY = "linear_client_id"
_CLIENT_SECRET_PROPERTY = "linear_client_secret"
_TOKEN_PROPERTY = "linear_api_token"
_TOKEN_URL = "https://api.linear.app/oauth/token"

# read/write cover the agentActivityCreate and issueUpdate mutations
# linear_client.py makes; app:assignable is what lets `set_delegate`
# (issueUpdate's delegateId) succeed once it is wired up (see
# docs/architecture.md's Linear section for why that call is not made yet).
_SCOPE = "read,write,app:assignable"


def _vault(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    """Every Vault call goes through the operator's own `vault` login.

    Shelling out to the CLI rather than adding an HTTP client keeps a
    dependency the deployment has no use for out of the tree, and inherits
    whatever authentication the operator already has - a token helper,
    VAULT_TOKEN, or an OIDC login - instead of asking for a token again.
    """
    try:
        return subprocess.run(
            ["vault", *args], input=stdin, capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        sys.exit("the `vault` CLI is not on PATH")


def read_client_credentials() -> tuple[str, str, int]:
    """The OAuth application's client id/secret, plus the secret's version.

    The version is what makes the write at the end a compare-and-set: if
    anything else touches this secret between the read and the write, the
    write fails loudly instead of silently clobbering it.
    """
    try:
        completed = _vault(["kv", "get", "-format=json", _VAULT_PATH])
    except subprocess.CalledProcessError as exc:
        sys.exit(f"could not read {_VAULT_PATH}: {exc.stderr.strip() or exc}")
    body = json.loads(completed.stdout)["data"]
    fields, version = body["data"], body["metadata"]["version"]

    client_id = fields.get(_CLIENT_ID_PROPERTY)
    client_secret = fields.get(_CLIENT_SECRET_PROPERTY)
    if not client_id or not client_secret:
        sys.exit(
            f"{_VAULT_PATH} is missing {_CLIENT_ID_PROPERTY} or "
            f"{_CLIENT_SECRET_PROPERTY}. From the app's page at "
            "https://linear.app/settings/api/applications, with client "
            "credentials tokens enabled, store both with:\n"
            f'  vault kv patch {_VAULT_PATH} {_CLIENT_ID_PROPERTY}="..." '
            f'{_CLIENT_SECRET_PROPERTY}="..."'
        )
    return client_id, client_secret, version


def request_token(client_id: str, client_secret: str) -> dict:
    """One client_credentials grant. No refresh_token comes back - the whole
    reason this script exists is to be re-run before the access token's own
    30-day expiry, not to keep a refresh cycle going."""
    response = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "scope": _SCOPE,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        body = response.text
        if "unsupported_grant_type" in body:
            sys.exit(
                "Linear rejected the client_credentials grant. Enable "
                "\"client credentials tokens\" on the app at "
                "https://linear.app/settings/api/applications, then retry."
            )
        sys.exit(f"Linear's token endpoint answered {response.status_code}: {body}")
    return response.json()


def write_token(token: str, version: int) -> None:
    """Store the new token, failing rather than clobbering a concurrent write.

    The value goes in on stdin (`property=-`) so the token never appears in
    this process's argv or shell history.
    """
    try:
        _vault(
            ["kv", "patch", f"-cas={version}", _VAULT_PATH, f"{_TOKEN_PROPERTY}=-"],
            stdin=token,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or str(exc)) from exc


def main() -> None:
    client_id, client_secret, version = read_client_credentials()

    tokens = request_token(client_id, client_secret)
    access_token = tokens.get("access_token")
    if not access_token:
        sys.exit(f"no access_token in Linear's response: {tokens}")

    try:
        write_token(access_token, version)
    except RuntimeError as exc:
        # The token is already minted - never let it die with the process
        # over a Vault write conflict. Print it and let the operator store
        # it by hand.
        print(f"\ncould not store the token: {exc}", file=sys.stderr)
        print(f"\n--- token, store this by hand at {_VAULT_PATH} {_TOKEN_PROPERTY} ---")
        print(access_token)
        sys.exit(1)

    expires_in = tokens.get("expires_in")
    expires_note = f" (expires in {int(expires_in) // 86400} days)" if expires_in else ""
    print(f"\nstored a fresh Linear token at {_VAULT_PATH} {_TOKEN_PROPERTY}{expires_note}")
    print(
        "\neve-tools picks this up on its next hourly refresh. To apply it now:\n"
        "  kubectl annotate externalsecret eve-tools-secrets -n eve-tools "
        "force-sync=$(date +%s) --overwrite\n"
        "  kubectl rollout restart deployment/eve-tools -n eve-tools"
    )


if __name__ == "__main__":
    main()
