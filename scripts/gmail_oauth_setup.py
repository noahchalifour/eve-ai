"""Run locally, once per family member, to obtain a Gmail OAuth refresh token.

The OAuth client itself - the "Desktop app" credential downloaded from
https://console.cloud.google.com/apis/credentials - is read from Vault rather
than from a file on disk, so there is no `client_secret.json` sitting in a
working copy waiting to be committed:

    vault kv patch kv/credentials/eve-tools \\
      gmail_oauth_client_json="$(cat ~/Downloads/client_secret_*.json)"

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

Prints the `authorized_user` JSON for that member. Merge it into the
`gmail_credentials_json` object at kv/credentials/eve-tools, keyed by the
member sub passed on the command line - see family.yaml for the sub values.
That object is what eve-tools reads at runtime, and it embeds the client id
and secret itself, which is why the OAuth client above is only ever needed
here and never by the deployment.
"""

from __future__ import annotations

import json
import subprocess
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
_VAULT_PATH = "kv/credentials/eve-tools"
_VAULT_PROPERTY = "gmail_oauth_client_json"


def load_client_config() -> dict:
    """The OAuth client, read from Vault through the operator's own login.

    Shelling out to the `vault` CLI rather than adding an HTTP client keeps
    this script free of a dependency the deployment itself has no use for,
    and it inherits whatever authentication the operator already has - a
    token helper, VAULT_TOKEN, or an OIDC login - instead of asking for a
    token again.
    """
    try:
        completed = subprocess.run(
            ["vault", "kv", "get", f"-field={_VAULT_PROPERTY}", _VAULT_PATH],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        sys.exit("the `vault` CLI is not on PATH")
    except subprocess.CalledProcessError as exc:
        sys.exit(
            f"could not read {_VAULT_PATH} field {_VAULT_PROPERTY}: "
            f"{exc.stderr.strip() or exc}"
        )

    try:
        config = json.loads(completed.stdout)
    except ValueError:
        sys.exit(f"{_VAULT_PATH} field {_VAULT_PROPERTY} is not valid JSON")

    # Google writes the downloaded client under one of these two keys
    # depending on the client type; anything else means the wrong JSON was
    # pasted into Vault, and the failure is much clearer here than inside
    # the OAuth flow.
    if not isinstance(config, dict) or not ({"installed", "web"} & config.keys()):
        sys.exit(
            f"{_VAULT_PATH} field {_VAULT_PROPERTY} does not look like a "
            'Google OAuth client (expected an "installed" or "web" key)'
        )
    return config


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    member_sub = sys.argv[1]

    flow = InstalledAppFlow.from_client_config(load_client_config(), _SCOPES)
    credentials = flow.run_local_server(port=0)
    print(f"\n--- credentials for {member_sub} ---")
    print(json.dumps(json.loads(credentials.to_json())))


if __name__ == "__main__":
    main()
