"""eve-tools' own configuration - deliberately separate from eve.settings.
Settings: this is a different process, holding different (and more
sensitive) secrets, and the two must never share a settings object. The
`api_key` field's env var, EVE_TOOLS_API_KEY, is the one deliberate overlap
- the same literal name eve.settings.Settings.tools_api_key resolves to,
because it is one shared secret between two processes (design doc section
7.1's open item, resolved here as a shared bearer token).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ToolsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVE_TOOLS_", extra="ignore")

    api_key: str = ""
    home_assistant_url: str = ""
    home_assistant_token: str = ""
    gmail_credentials_json: str = ""
    caldav_credentials_json: str = ""
    # A Monarch session token, which is all the client actually needs -
    # email/password exist only to obtain one, and an account created
    # through Google sign-in has no password to give. Takes precedence.
    monarch_token: str = ""
    monarch_email: str = ""
    monarch_password: str = ""


@lru_cache(maxsize=1)
def get_tools_settings() -> ToolsSettings:
    return ToolsSettings()
