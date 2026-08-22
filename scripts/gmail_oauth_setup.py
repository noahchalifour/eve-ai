"""Run locally, once per family member, to obtain a Gmail OAuth refresh token.
Requires a Google Cloud OAuth client (Desktop app type) with the Gmail API
enabled - create one at https://console.cloud.google.com/apis/credentials
and download its client secret JSON alongside this script as
`client_secret.json` before running.

Usage: uv run python scripts/gmail_oauth_setup.py <member-sub>

Prints the authorized_user JSON for that member. Merge it into the
EVE_TOOLS_GMAIL_CREDENTIALS_JSON blob in Vault (kv/credentials/gmail),
keyed by the member sub passed on the command line - see family.yaml for
the sub values.
"""

from __future__ import annotations

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    member_sub = sys.argv[1]

    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", _SCOPES)
    credentials = flow.run_local_server(port=0)
    print(f"\n--- credentials for {member_sub} ---")
    print(json.dumps(json.loads(credentials.to_json())))


if __name__ == "__main__":
    main()
