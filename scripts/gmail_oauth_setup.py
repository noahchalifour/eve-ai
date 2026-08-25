"""Run locally, once per family member, to obtain a Gmail OAuth refresh token
and store it where eve-tools reads it.

Both halves of the credential live at kv/credentials/eve-tools. The OAuth
client - the "Desktop app" credential downloaded from
https://console.cloud.google.com/apis/credentials - is read from
`gmail_oauth_client_json`, so there is no `client_secret.json` sitting in a
working copy waiting to be committed:

    vault kv patch kv/credentials/eve-tools \\
      gmail_oauth_client_json="$(cat ~/Downloads/client_secret_*.json)"

The result is merged into `gmail_credentials_json`, the object eve-tools reads
at runtime, keyed by the member sub passed on the command line - see
family.yaml for the sub values. Running this again for the same member
replaces just that member's entry.

Three things about that Google Cloud project, each of which fails in a way
that looks like a bug in this script rather than a console setting:

  - The Gmail API must be enabled on it, or consent succeeds and every later
    API call answers 403.
  - The client must be of type "Desktop app". `run_local_server` below needs
    a loopback redirect, which only that type has.
  - The publishing status must be "In production". While an External app sits
    in "Testing", Google expires its refresh tokens after seven days, so
    Eve's mail source would break every week with a 401.

Usage: uv run python scripts/gmail_oauth_setup.py <member-sub>
"""

from __future__ import annotations

import json
import subprocess
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
_VAULT_PATH = "kv/credentials/eve-tools"
_CLIENT_PROPERTY = "gmail_oauth_client_json"
_CREDENTIALS_PROPERTY = "gmail_credentials_json"


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


def read_secret() -> tuple[dict, int]:
    """Every field at `_VAULT_PATH`, plus the secret's version.

    One round trip for both properties, and the version is what makes the
    write at the end a compare-and-set: if anything else touches this secret
    while the browser consent is in progress, the write fails loudly instead
    of silently clobbering the other family member's credentials.
    """
    try:
        completed = _vault(["kv", "get", "-format=json", _VAULT_PATH])
    except subprocess.CalledProcessError as exc:
        sys.exit(f"could not read {_VAULT_PATH}: {exc.stderr.strip() or exc}")
    body = json.loads(completed.stdout)["data"]
    return body["data"], body["metadata"]["version"]


def client_config(fields: dict) -> dict:
    """The OAuth client, validated before the browser opens rather than after."""
    raw = fields.get(_CLIENT_PROPERTY)
    if not raw:
        sys.exit(
            f"{_VAULT_PATH} has no {_CLIENT_PROPERTY}. Download the Desktop-app "
            "OAuth client from the Google Cloud console and store it with:\n"
            f'  vault kv patch {_VAULT_PATH} {_CLIENT_PROPERTY}="$(cat client_secret_*.json)"'
        )
    try:
        config = json.loads(raw)
    except ValueError:
        sys.exit(f"{_VAULT_PATH} field {_CLIENT_PROPERTY} is not valid JSON")
    # Google writes the downloaded client under one of these two keys
    # depending on the client type; anything else means the wrong JSON was
    # pasted into Vault, and saying so here is far clearer than whatever the
    # OAuth flow would raise three steps later.
    if not isinstance(config, dict) or not ({"installed", "web"} & config.keys()):
        sys.exit(
            f"{_VAULT_PATH} field {_CLIENT_PROPERTY} does not look like a Google "
            'OAuth client (expected an "installed" or "web" key)'
        )
    return config


def merge_member(existing_raw: str | None, member_sub: str, credentials: dict) -> dict:
    """This member's entry, added to whatever is already there.

    Deliberately a read-modify-write on one JSON blob rather than a Vault
    property per member: `gmail_credentials_json` is the shape eve-tools
    reads, and it is keyed by sub. The whole reason the caller pairs this
    with a compare-and-set is that a careless write here would take the
    other member's refresh token with it.
    """
    if not existing_raw or not existing_raw.strip():
        blob: dict = {}
    else:
        try:
            blob = json.loads(existing_raw)
        except ValueError:
            raise ValueError(
                f"{_VAULT_PATH} field {_CREDENTIALS_PROPERTY} is not valid JSON; "
                "refusing to overwrite it"
            ) from None
    if not isinstance(blob, dict):
        raise ValueError(
            f"{_VAULT_PATH} field {_CREDENTIALS_PROPERTY} is not a JSON object "
            "keyed by member sub; refusing to overwrite it"
        )
    return {**blob, member_sub: credentials}


def write_credentials(blob: dict, version: int) -> None:
    """Store the merged object, failing rather than clobbering a concurrent write.

    The value goes in on stdin (`property=-`) so a live refresh token never
    appears in this process's argv.
    """
    try:
        _vault(
            ["kv", "patch", f"-cas={version}", _VAULT_PATH, f"{_CREDENTIALS_PROPERTY}=-"],
            stdin=json.dumps(blob),
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or str(exc)) from exc


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    member_sub = sys.argv[1]

    fields, version = read_secret()
    config = client_config(fields)

    flow = InstalledAppFlow.from_client_config(config, _SCOPES)
    credentials = json.loads(flow.run_local_server(port=0).to_json())

    try:
        blob = merge_member(fields.get(_CREDENTIALS_PROPERTY), member_sub, credentials)
        write_credentials(blob, version)
    except (ValueError, RuntimeError) as exc:
        # The consent is already spent, so never let the credential die with
        # the process - print it and let the operator store it by hand.
        print(f"\ncould not store the credential: {exc}", file=sys.stderr)
        print(f"\n--- credentials for {member_sub}, store these by hand ---")
        print(json.dumps(credentials))
        sys.exit(1)

    others = sorted(set(blob) - {member_sub})
    print(f"\nstored credentials for {member_sub} at {_VAULT_PATH}")
    print(f"{_CREDENTIALS_PROPERTY} now holds {len(blob)} member(s)"
          + (f"; also {', '.join(others)}" if others else ""))
    print(
        "\neve-tools picks this up on its next hourly refresh. To apply it now:\n"
        "  kubectl annotate externalsecret eve-tools-secrets -n eve-tools "
        "force-sync=$(date +%s) --overwrite\n"
        "  kubectl rollout restart deployment/eve-tools -n eve-tools"
    )


if __name__ == "__main__":
    main()
