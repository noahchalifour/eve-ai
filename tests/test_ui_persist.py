"""`custom` frames are streamed and never stored. This is what makes a card
survive a relaunch."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from eve.ui import protocol
from eve.ui.persist import persist_ui


def _create(surface_id: str) -> dict:
    return {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": surface_id,
            "catalogId": "column",
            "catalogVersion": "1",
            "components": [],
            "data": {},
            "localState": {},
        },
    }


def _tool_message(surface_id: str, call_id: str = "c1") -> ToolMessage:
    return ToolMessage(
        content="Surface shown.",
        tool_call_id=call_id,
        name="show_surface",
        artifact=_create(surface_id),
    )


async def test_the_turns_surface_is_appended_to_the_final_ai_message():
    state = {
        "messages": [
            HumanMessage(content="what's the weather?", id="h1"),
            AIMessage(content="", id="a1", tool_calls=[]),
            _tool_message("wx-1"),
            AIMessage(content="Nice out there.", id="a2"),
        ]
    }

    result = await persist_ui(state)

    message = result["messages"][0]
    assert message.id == "a2"
    assert message.content.startswith("Nice out there.\n<assistant-ui>\n")
    assert message.content.endswith("\n</assistant-ui>")
    assert json.loads(message.content.splitlines()[2])["op"] == "create"


async def test_a_turn_with_no_surface_changes_nothing():
    """The common case. Every non-weather turn must return an empty update, or
    every AI message in the product gets rewritten for nothing."""
    state = {
        "messages": [
            HumanMessage(content="hello", id="h1"),
            AIMessage(content="Hi Noah.", id="a1"),
        ]
    }

    assert await persist_ui(state) == {}


async def test_only_this_turns_surfaces_are_copied():
    """Scanning back to the last human message, not through the whole thread:
    a card from three turns ago is already in that turn's own AI message, and
    re-appending it would create a duplicate surface id on reopen."""
    state = {
        "messages": [
            HumanMessage(content="turn one", id="h1"),
            _tool_message("wx-old", call_id="c0"),
            AIMessage(content="Older.", id="a1"),
            HumanMessage(content="turn two", id="h2"),
            _tool_message("wx-new", call_id="c1"),
            AIMessage(content="Newer.", id="a2"),
        ]
    }

    result = await persist_ui(state)

    assert "wx-new" in result["messages"][0].content
    assert "wx-old" not in result["messages"][0].content


async def test_a_tool_artifact_that_is_not_an_operation_is_ignored():
    """A dynamically-materialized tool could set an artifact for its own
    reasons. Only an `assistant-ui/1.0` operation belongs in a frame."""
    state = {
        "messages": [
            HumanMessage(content="hi", id="h1"),
            ToolMessage(content="ok", tool_call_id="c1", name="other", artifact={"rows": 3}),
            AIMessage(content="Done.", id="a1"),
        ]
    }

    assert await persist_ui(state) == {}


async def test_no_more_than_eight_surfaces_are_written_into_one_turn():
    """The protocol's per-turn ceiling. The ninth create in one frame makes
    the client reject the WHOLE frame, taking the eight valid surfaces with
    it."""
    messages = [HumanMessage(content="hi", id="h1")]
    for index in range(10):
        messages.append(_tool_message(f"wx-{index}", call_id=f"c{index}"))
    messages.append(AIMessage(content="Done.", id="a1"))

    result = await persist_ui({"messages": messages})

    body = result["messages"][0].content
    assert body.count('"op":"create"') == protocol.MAX_SURFACES_PER_TURN
    # Which eight, not just how many: a reversal bug that kept the LAST
    # eight instead of the first eight would still pass the count assertion
    # above.
    assert "wx-0" in body and "wx-9" not in body


async def test_a_list_shaped_ai_content_gets_a_text_block_not_a_string():
    """Reasoning-capable models return `content` as a list of typed blocks.
    Concatenating a string onto that would corrupt the message."""
    state = {
        "messages": [
            HumanMessage(content="hi", id="h1"),
            _tool_message("wx-1"),
            AIMessage(content=[{"type": "text", "text": "Nice out."}], id="a1"),
        ]
    }

    result = await persist_ui(state)

    blocks = result["messages"][0].content
    assert isinstance(blocks, list)
    assert blocks[0] == {"type": "text", "text": "Nice out."}
    assert blocks[-1]["type"] == "text"
    assert "<assistant-ui>" in blocks[-1]["text"]
