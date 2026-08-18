"""Verifies the two assumptions the ChatGPT proxy carries (spec 7.1.1).

Skipped unless EVE_LIVE_TESTS=1, because it spends real subscription quota.
"""

import os

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from eve.models import Tier, get_model

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("EVE_LIVE_TESTS") != "1",
        reason="set EVE_LIVE_TESTS=1 to run against the real LiteLLM proxy",
    ),
]


@tool
def get_weather(city: str) -> str:
    """Return the current weather for a city."""
    return f"sunny in {city}"


def _text_of(message) -> str:
    """Flatten an AIMessage's content to plain text.

    The Responses API returns `content` as a list of content blocks, not a
    string - verified live on 2026-08-18. Eve's graph never touches `.content`
    (the `eve` node passes the whole message through), so this shape is fine
    for the app; anything that reads message text must handle both forms.
    """
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


async def test_voice_tier_responds_through_litellm():
    model = get_model(Tier.VOICE)
    reply = await model.ainvoke([HumanMessage("Reply with exactly: pong")])
    assert _text_of(reply).strip(), f"no text in reply: {reply.content!r}"


async def test_voice_tier_streams_tokens():
    model = get_model(Tier.VOICE)
    chunks = [c async for c in model.astream([HumanMessage("Count to five.")])]
    assert len(chunks) > 1, "no incremental streaming; SSE would deliver one blob"


async def test_voice_tier_emits_tool_calls():
    # Phase 3's entire topology depends on this working.
    model = get_model(Tier.VOICE).bind_tools([get_weather])
    reply = await model.ainvoke([HumanMessage("What is the weather in Toronto?")])
    assert reply.tool_calls, "proxy did not return tool calls"
    assert reply.tool_calls[0]["name"] == "get_weather"
