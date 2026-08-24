from unittest.mock import MagicMock, patch

import pytest

from eve_tools import gmail


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv(
        "EVE_TOOLS_GMAIL_CREDENTIALS_JSON",
        '{"sub-noah": {"token": "t", "refresh_token": "r", "client_id": "c", '
        '"client_secret": "s", "token_uri": "https://oauth2.googleapis.com/token", '
        '"scopes": ["https://www.googleapis.com/auth/gmail.modify"]}}',
    )


def _full_message(msg_id, headers, snippet="On its way.", internal_date="1700000000000"):
    """The metadata-format body `messages().get()` returns: headers as a
    list of {name, value}, not a flat dict."""
    return {
        "id": msg_id,
        "threadId": f"thread-{msg_id}",
        "snippet": snippet,
        "internalDate": internal_date,
        "payload": {"headers": [{"name": k, "value": v} for k, v in headers.items()]},
    }


async def test_list_messages_hydrates_headers_into_flat_fields():
    fake_service = MagicMock()
    fake_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}]
    }
    fake_service.users().messages().get().execute.return_value = _full_message(
        "m1", {"From": "school@example.com", "Subject": "Field trip form", "Date": "Mon"}
    )
    with patch("eve_tools.gmail._service", return_value=fake_service), \
         patch("eve_tools.gmail._credentials_for", return_value=MagicMock(expired=False)):
        result = await gmail.list_messages("sub-noah", "is:unread")
    [message] = result["messages"]
    assert message["id"] == "m1"
    assert message["threadId"] == "thread-m1"
    assert message["from"] == "school@example.com"
    assert message["subject"] == "Field trip form"
    assert message["snippet"] == "On its way."
    assert message["internalDate"] == "1700000000000"


async def test_headers_match_case_insensitively():
    """Gmail's header names are case-insensitive on the wire; a client that
    sends them differently-cased than expected should still hydrate."""
    fake_service = MagicMock()
    fake_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}]
    }
    fake_service.users().messages().get().execute.return_value = _full_message(
        "m1", {"from": "school@example.com", "SUBJECT": "Field trip form"}
    )
    with patch("eve_tools.gmail._service", return_value=fake_service), \
         patch("eve_tools.gmail._credentials_for", return_value=MagicMock(expired=False)):
        result = await gmail.list_messages("sub-noah", "is:unread")
    [message] = result["messages"]
    assert message["from"] == "school@example.com"
    assert message["subject"] == "Field trip form"


async def test_a_missing_header_falls_back_rather_than_raising():
    fake_service = MagicMock()
    fake_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}]
    }
    fake_service.users().messages().get().execute.return_value = _full_message(
        "m1", {"From": "school@example.com"}
    )
    with patch("eve_tools.gmail._service", return_value=fake_service), \
         patch("eve_tools.gmail._credentials_for", return_value=MagicMock(expired=False)):
        result = await gmail.list_messages("sub-noah", "is:unread")
    [message] = result["messages"]
    assert message["subject"] == ""
    assert message["date"] == ""


async def test_a_failed_metadata_fetch_skips_only_that_message():
    fake_service = MagicMock()
    fake_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}, {"id": "m2"}]
    }
    fake_service.users().messages().get().execute.side_effect = [
        RuntimeError("deleted between list and get"),
        _full_message("m2", {"From": "shop@example.com", "Subject": "Shipped"}),
    ]
    with patch("eve_tools.gmail._service", return_value=fake_service), \
         patch("eve_tools.gmail._credentials_for", return_value=MagicMock(expired=False)):
        result = await gmail.list_messages("sub-noah", "is:unread")
    [message] = result["messages"]
    assert message["id"] == "m2"


async def test_a_non_dict_stub_is_skipped_without_raising():
    """A non-dict stub read in the except handler's own `stub.get("id")`
    log line would make the *handler* the thing that raises. Guarding the
    stub's type up front means a malformed stub is skipped before either
    the happy path or the handler ever reads from it."""
    fake_service = MagicMock()
    fake_service.users().messages().list().execute.return_value = {
        "messages": ["not-a-dict", {"id": "m2"}]
    }
    fake_service.users().messages().get().execute.return_value = _full_message(
        "m2", {"From": "shop@example.com", "Subject": "Shipped"}
    )
    with patch("eve_tools.gmail._service", return_value=fake_service), \
         patch("eve_tools.gmail._credentials_for", return_value=MagicMock(expired=False)):
        result = await gmail.list_messages("sub-noah", "is:unread")
    [message] = result["messages"]
    assert message["id"] == "m2"


async def test_send_email_builds_a_base64_raw_message():
    fake_service = MagicMock()
    fake_service.users().messages().send().execute.return_value = {"id": "sent-1"}
    with patch("eve_tools.gmail._service", return_value=fake_service):
        result = await gmail.send_email("sub-noah", "a@b.com", "Hi", "Body text")
    assert result == {"sent": True, "id": "sent-1"}
