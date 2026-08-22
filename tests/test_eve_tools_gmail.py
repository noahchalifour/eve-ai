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


async def test_list_messages_calls_the_gmail_api():
    fake_service = MagicMock()
    fake_service.users().messages().list().execute.return_value = {
        "messages": [{"id": "m1"}]
    }
    with patch("eve_tools.gmail._service", return_value=fake_service), \
         patch("eve_tools.gmail._credentials_for", return_value=MagicMock(expired=False)):
        result = await gmail.list_messages("sub-noah", "is:unread")
    assert result == {"messages": [{"id": "m1"}]}


async def test_send_email_builds_a_base64_raw_message():
    fake_service = MagicMock()
    fake_service.users().messages().send().execute.return_value = {"id": "sent-1"}
    with patch("eve_tools.gmail._service", return_value=fake_service):
        result = await gmail.send_email("sub-noah", "a@b.com", "Hi", "Body text")
    assert result == {"sent": True, "id": "sent-1"}
