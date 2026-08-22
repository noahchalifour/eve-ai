"""Gmail client via the official googleapiclient, OAuth refreshed from a
stored token. One credential per family member: gmail_credentials_json
holds a JSON object keyed by member sub, each value the shape
google.oauth2.credentials.Credentials.to_authorized_user_info() produces
(obtained via scripts/gmail_oauth_setup.py, Task 17).

googleapiclient is synchronous; every call here runs in a thread via
asyncio.to_thread so it does not block eve-tools' event loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from eve_tools.settings import get_tools_settings

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _credentials_for(member_sub: str) -> Credentials:
    all_creds = json.loads(get_tools_settings().gmail_credentials_json or "{}")
    creds = Credentials.from_authorized_user_info(all_creds[member_sub], _SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _service(member_sub: str):
    return build("gmail", "v1", credentials=_credentials_for(member_sub))


async def list_messages(member_sub: str, query: str) -> dict:
    def _run():
        service = _service(member_sub)
        return service.users().messages().list(userId="me", q=query, maxResults=10).execute()

    return await asyncio.to_thread(_run)


async def get_thread(member_sub: str, thread_id: str) -> dict:
    def _run():
        service = _service(member_sub)
        return service.users().threads().get(userId="me", id=thread_id).execute()

    return await asyncio.to_thread(_run)


async def send_email(member_sub: str, to: str, subject: str, body: str) -> dict:
    def _run():
        service = _service(member_sub)
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {"sent": True, "id": sent["id"]}

    return await asyncio.to_thread(_run)
