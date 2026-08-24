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
import logging
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from eve_tools.settings import get_tools_settings

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
_METADATA_HEADERS = ["From", "Subject", "Date"]


def _credentials_for(member_sub: str) -> Credentials:
    all_creds = json.loads(get_tools_settings().gmail_credentials_json or "{}")
    creds = Credentials.from_authorized_user_info(all_creds[member_sub], _SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _service(member_sub: str):
    return build("gmail", "v1", credentials=_credentials_for(member_sub))


def _flatten(raw: dict) -> dict:
    """`messages().list()` returns only `id` and `threadId`; every field the
    ambient mail source (and any future summarizer) reads by name has to be
    hydrated from a per-message `get`. Header names are case-insensitive on
    the wire, so match them that way rather than trusting Gmail's casing."""
    headers = {}
    for header in (raw.get("payload") or {}).get("headers") or []:
        name = str(header.get("name", "")).lower()
        if name in ("from", "subject", "date"):
            headers[name] = header.get("value", "")
    return {
        "id": raw.get("id"),
        "threadId": raw.get("threadId"),
        "snippet": raw.get("snippet", ""),
        "internalDate": raw.get("internalDate"),
        "from": headers.get("from", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
    }


async def list_messages(member_sub: str, query: str) -> dict:
    def _run():
        service = _service(member_sub)
        listing = (
            service.users().messages().list(userId="me", q=query, maxResults=10).execute()
        )
        messages = []
        for stub in listing.get("messages") or []:
            if not isinstance(stub, dict):
                # Guard the read here too, not just in the happy path: the
                # except below reads stub.get("id") for its log line, and a
                # non-dict stub would make the *handler* the thing that
                # raises and takes the whole call down.
                logger.warning("gmail message stub was not a dict, skipping: %r", stub)
                continue
            try:
                full = (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=stub["id"],
                        format="metadata",
                        metadataHeaders=_METADATA_HEADERS,
                    )
                    .execute()
                )
            except Exception:
                # A message deleted between list and get, or a transient API
                # error, should drop that one message rather than the batch.
                logger.warning(
                    "gmail metadata fetch failed for message %s", stub.get("id"),
                    exc_info=True,
                )
                continue
            messages.append(_flatten(full))
        return {**listing, "messages": messages}

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
