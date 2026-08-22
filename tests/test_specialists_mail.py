"""tests/test_specialists_mail.py"""
import importlib
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage

import eve.specialists.mail as mail_module
from tests.conftest import FakeToolCallingModel
from tests.test_specialists_base import CONFIG, MEMBER, STATE


def _reload_with_model(monkeypatch, *ai_messages):
    monkeypatch.setattr(
        "eve.specialists.mail._model_for_test",
        lambda: FakeToolCallingModel(messages=iter(ai_messages)),
    )
    importlib.reload(mail_module)
    monkeypatch.setattr(
        "eve.specialists.mail._model_for_test",
        lambda: FakeToolCallingModel(messages=iter(ai_messages)),
    )
    return mail_module


async def test_send_email_is_denied_without_mail_send(monkeypatch):
    tool_call = {
        "name": "send_email",
        "args": {"to": "a@b.com", "subject": "hi", "body": "hi"},
        "id": "call-1",
        "type": "tool_call",
    }
    module = _reload_with_model(
        monkeypatch,
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Permission denied: this action requires mail.send."),
    )
    monkeypatch.setattr(module, "invoke", AsyncMock(return_value="sent"))
    read_only_member = {**MEMBER, "permissions": ["mail.read"]}
    state = {**STATE, "member": read_only_member}
    result = await module.ask_mail.ainvoke(
        {"request": "email a@b.com saying hi", "state": state, "config": CONFIG}
    )
    assert "Permission denied" in result
    module.invoke.assert_not_awaited()


async def test_send_email_succeeds_with_mail_send(monkeypatch):
    tool_call = {
        "name": "send_email",
        "args": {"to": "a@b.com", "subject": "hi", "body": "hi there"},
        "id": "call-1",
        "type": "tool_call",
    }
    module = _reload_with_model(
        monkeypatch,
        AIMessage(content="", tool_calls=[tool_call]),
        AIMessage(content="Sent."),
    )
    mock_invoke = AsyncMock(return_value='{"sent": true}')
    monkeypatch.setattr(module, "invoke", mock_invoke)
    sender_member = {**MEMBER, "permissions": ["mail.send"]}
    state = {**STATE, "member": sender_member}
    result = await module.ask_mail.ainvoke(
        {"request": "email a@b.com saying hi", "state": state, "config": CONFIG}
    )
    assert result == "Sent."
    mock_invoke.assert_awaited_once_with(
        "mail.send_email",
        {"member_sub": "sub-noah", "to": "a@b.com", "subject": "hi", "body": "hi there"},
    )
