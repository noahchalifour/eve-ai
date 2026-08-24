import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from eve_ambient.sources import mail
from eve_ambient.types import list_field, tool_result

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


def test_tool_result_does_not_unwrap_a_result_key_of_its_own():
    """A payload that legitimately owns a `result` key (unlikely here, but
    tool_result is shared code) must come back whole, not mangled."""
    assert tool_result(json.dumps({"result": "ok", "messages": []})) == {
        "result": "ok",
        "messages": [],
    }


async def test_a_non_dict_message_is_skipped_not_raised(monkeypatch):
    """A payload of {"messages": ["m1", "m2"]} is a plausible upstream shape
    change; a source may not let it raise."""
    monkeypatch.setattr(
        mail, "invoke", AsyncMock(return_value=json.dumps({"messages": ["m1", "m2"]}))
    )
    assert await mail.poll("sub-noah") == []


async def test_a_missing_internal_date_falls_back_to_now(monkeypatch):
    before = datetime.now(UTC)
    monkeypatch.setattr(
        mail,
        "invoke",
        AsyncMock(
            return_value=json.dumps(
                {"messages": [{"id": "m1", "from": "a@b.com", "subject": "Hi"}]}
            )
        ),
    )
    [signal] = await mail.poll("sub-noah")
    assert before <= signal.occurred_at <= datetime.now(UTC)


async def test_an_unparseable_internal_date_falls_back_to_now_without_raising(monkeypatch):
    """int(x)/1000 on a huge string overflows datetime.fromtimestamp with
    OverflowError/OSError, not the KeyError/TypeError/ValueError a narrower
    except clause would only catch."""
    monkeypatch.setattr(
        mail,
        "invoke",
        AsyncMock(
            return_value=json.dumps(
                {
                    "messages": [
                        {
                            "id": "m1",
                            "internalDate": "999999999999999999999",
                            "from": "a@b.com",
                            "subject": "Hi",
                        }
                    ]
                }
            )
        ),
    )
    [signal] = await mail.poll("sub-noah")
    assert signal.key == "m1"


async def test_a_missing_sender_and_subject_fall_back_rather_than_render_blank(monkeypatch):
    monkeypatch.setattr(
        mail,
        "invoke",
        AsyncMock(
            return_value=json.dumps(
                {"messages": [{"id": "m1", "internalDate": "1787500000000"}]}
            )
        ),
    )
    [signal] = await mail.poll("sub-noah")
    assert "unknown sender" in signal.summary
    assert "(no subject)" in signal.summary


async def test_a_present_but_empty_sender_and_subject_also_fall_back(monkeypatch):
    """gmail.py's hydration fills a missing header with "", present but
    falsy - not absent - so a plain dict default would not catch it."""
    monkeypatch.setattr(
        mail,
        "invoke",
        AsyncMock(
            return_value=json.dumps(
                {
                    "messages": [
                        {
                            "id": "m1",
                            "internalDate": "1787500000000",
                            "from": "",
                            "subject": "",
                        }
                    ]
                }
            )
        ),
    )
    [signal] = await mail.poll("sub-noah")
    assert "unknown sender" in signal.summary
    assert "(no subject)" in signal.summary


async def test_a_null_id_does_not_collide_with_a_missing_id(monkeypatch):
    monkeypatch.setattr(
        mail,
        "invoke",
        AsyncMock(return_value=json.dumps({"messages": [{"id": None}, {}]})),
    )
    assert await mail.poll("sub-noah") == []


def test_list_field_returns_the_list_when_the_shape_is_right():
    assert list_field({"messages": ["m1"]}, "messages") == ["m1"]


def test_list_field_returns_empty_for_a_missing_key():
    assert list_field({}, "messages") == []


def test_list_field_returns_empty_for_a_truthy_non_list():
    """The exact defect this closes: a truthy non-list value passes
    `or []` unscathed and would otherwise blow up the caller's `for`."""
    assert list_field({"messages": 5}, "messages") == []


async def test_a_non_list_messages_container_yields_no_signals(monkeypatch):
    """`{"messages": 5}` is truthy, so `or []` never fires; the `for`
    statement itself would raise TypeError out of the source without a
    type check on the container, not just on its members."""
    monkeypatch.setattr(
        mail, "invoke", AsyncMock(return_value=json.dumps({"messages": 5}))
    )
    assert await mail.poll("sub-noah") == []
