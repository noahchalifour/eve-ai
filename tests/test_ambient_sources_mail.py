import json
from unittest.mock import AsyncMock

from eve_ambient.sources import mail
from eve_ambient.types import tool_result

MESSAGES = {
    "messages": [
        {
            "id": "m1",
            "internalDate": "1787500000000",
            "subject": "Field trip form due Friday",
            "from": "school@example.com",
            "snippet": "Please return the signed form.",
        },
        {
            "id": "m2",
            "internalDate": "1787500600000",
            "subject": "Your package shipped",
            "from": "shop@example.com",
            "snippet": "On its way.",
        },
    ]
}


async def test_each_unread_message_becomes_one_signal(monkeypatch):
    monkeypatch.setattr(mail, "invoke", AsyncMock(return_value=json.dumps(MESSAGES)))
    signals = await mail.poll("sub-noah")
    assert [s.key for s in signals] == ["m1", "m2"]
    assert all(s.source == "mail" for s in signals)
    assert all(s.member_sub == "sub-noah" for s in signals)


async def test_the_summary_names_the_sender_and_subject(monkeypatch):
    """The filter reads `summary` and nothing else, so the one line has to
    carry enough to judge relevance."""
    monkeypatch.setattr(mail, "invoke", AsyncMock(return_value=json.dumps(MESSAGES)))
    first = (await mail.poll("sub-noah"))[0]
    assert "school@example.com" in first.summary
    assert "Field trip form due Friday" in first.summary


async def test_the_query_asks_only_for_recent_unread_mail(monkeypatch):
    invoke = AsyncMock(return_value=json.dumps({"messages": []}))
    monkeypatch.setattr(mail, "invoke", invoke)
    await mail.poll("sub-noah")
    _tool, args = invoke.await_args.args
    assert args["member_sub"] == "sub-noah"
    assert "is:unread" in args["query"]
    assert "newer_than:1d" in args["query"]


async def test_an_eve_tools_error_yields_no_signals(monkeypatch):
    """eve-tools returns error strings rather than raising. A source that let
    that through would report a signal whose summary was an error message."""
    monkeypatch.setattr(
        mail, "invoke", AsyncMock(return_value="error: eve-tools unavailable")
    )
    assert await mail.poll("sub-noah") == []


async def test_malformed_json_yields_no_signals(monkeypatch):
    monkeypatch.setattr(mail, "invoke", AsyncMock(return_value="{not json"))
    assert await mail.poll("sub-noah") == []


def test_tool_result_parses_what_tools_client_returns():
    """tools_client.invoke hands back the already-unwrapped result as JSON."""
    assert tool_result(json.dumps({"messages": []})) == {"messages": []}


def test_tool_result_rejects_an_error_string():
    assert tool_result("error: whatever") is None
