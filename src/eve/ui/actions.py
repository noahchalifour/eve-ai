"""The inbound half of the protocol.

A tap on a rendered surface is not a separate channel: the client re-runs the
turn with the user message's content REPLACED by an encoded action envelope
(`AssistantSessionUseCase.submitUiAction` -> `LangGraphAgentService.run`), so
it reaches the graph as an ordinary `HumanMessage` and something has to tell
it apart from a member typing.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from eve.state import EveState
from eve.tools_client import invoke
from eve.ui import protocol, stream, weather

_OPENING = "<assistant-ui-action>"
_CLOSING = "</assistant-ui-action>"


def parse_action(text: object) -> dict | None:
    """The decoded envelope, or None when `text` is ordinary member speech.

    Never raises and never guesses: anything that is not a complete,
    current-protocol envelope naming a V1 action is None, and a member who
    types the marker by hand gets a normal Eve turn rather than a UI patch.

    Both the wrapped and the bare form are accepted.
    `DynamicSurfaceProtocol.encodeAction` wraps on EVERY provider - the
    provider guide's "on the portable path" describes intent, the Dart code
    has no branch - and the bare form is tolerated so a future native action
    channel needs no change here.

    The envelope's `data` (the surface's current contents) is deliberately not
    trusted or used by any caller: it arrives from the client and the
    requested range is re-read from Home Assistant instead.
    """
    if not isinstance(text, str):
        return None
    body = text.strip()
    if body.startswith(_OPENING) and body.endswith(_CLOSING):
        body = body[len(_OPENING) : -len(_CLOSING)].strip()
    if not body.startswith("{"):
        return None
    try:
        envelope = json.loads(body)
    except ValueError:
        return None
    if not isinstance(envelope, dict):
        return None
    if envelope.get("protocol") != protocol.PROTOCOL:
        return None
    if envelope.get("actionId") not in protocol.ACTION_IDS:
        return None
    surface_id = envelope.get("surfaceId")
    if not isinstance(surface_id, str) or not surface_id:
        return None
    return envelope


class UiActionError(RuntimeError):
    """A UI action that could not be answered.

    Raised, not returned - the single deliberate exception to Eve's "every
    external call degrades to a returned string" rule, and only in this
    branch. There is no model here to explain anything in prose, and the
    protocol's own failure contract IS an error event: Aegra turns a node
    exception into an SSE `error` frame, `LangGraphAgentService._handleFrame`
    emits `AgentError`, and the client's `_failPendingActionSurface` puts the
    surface into `error` with its last VALID data retained and an Ink retry
    control offered. Returning quietly instead would leave the card spinning
    on "Loading forecast" with nothing to say why.
    """


# What the raw envelope is replaced with in the transcript. One owner for the
# literal: a reopened session renders these as the member's own words.
ACTION_LABELS = {
    "hourly": "Show the hourly forecast.",
    "daily": "Show the 7-day forecast.",
}


async def ui_action(state: EveState, config: RunnableConfig) -> dict:
    """Answer one `weather.rangeChanged` tap with one patch. No model call.

    Re-reads Home Assistant rather than trusting the envelope's `data`, which
    arrives from the client.
    """
    last = state["messages"][-1]
    envelope = parse_action(last.content)
    if envelope is None:
        # Unreachable through `_route_after_context`, which only routes here
        # for an envelope this same function parsed. Defensive, because the
        # alternative is a KeyError from a node with no model to fall back on.
        raise UiActionError("not a UI action")

    value = envelope.get("value")
    if value not in weather.RANGES:
        raise UiActionError("unsupported weather range")

    forecast = weather.decode_forecast(await invoke("home.weather", {}))
    if forecast is None:
        raise UiActionError("the weather could not be read")

    operation = weather.build_range_patch(
        envelope["surfaceId"], value, forecast, state["member"]["timezone"]
    )
    if operation is None:
        raise UiActionError("no forecast for that range")
    if not stream.emit(operation):
        raise UiActionError("the patch was rejected")

    return {
        "messages": [
            # Same id, so `add_messages` REPLACES rather than appends: the raw
            # envelope would otherwise show up as a user bubble full of JSON
            # on reopen, since `loadHistory` renders every non-empty human
            # message it finds.
            HumanMessage(content=ACTION_LABELS[value], id=last.id),
            # The patch, again, as a portable frame. `custom` frames are
            # streamed and never stored, and the client replays a reopened
            # session from `values.messages` alone. A node's message update is
            # not an LLM stream event, so this text is never emitted on
            # `messages` mode - the live client renders from the `custom`
            # frame and only ever sees this through history, where it is
            # stripped from the visible text and from TTS.
            AIMessage(content=protocol.frame([operation])),
        ]
    }
