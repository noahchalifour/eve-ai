"""The inbound half of the protocol.

A tap on a rendered surface is not a separate channel: the client re-runs the
turn with the user message's content REPLACED by an encoded action envelope
(`AssistantSessionUseCase.submitUiAction` -> `LangGraphAgentService.run`), so
it reaches the graph as an ordinary `HumanMessage` and something has to tell
it apart from a member typing.
"""

from __future__ import annotations

import json

from langchain_core.messages import HumanMessage

from eve.state import EveState
from eve.ui import protocol

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


def readable_submission(state: object) -> str:
    """What the raw envelope is replaced with in the transcript.

    A reopened session renders this as the member's own words, and it is also
    what the VOICE model reads as the turn it has to answer - so it has to be
    a sentence, never JSON and never empty. Anthropic's Messages API (the
    proxy's fallback tier) rejects an empty non-final message outright.

    Frame markers are stripped from typed text. A member typing one is
    self-only blast radius, but `strip_frames` inverts exactly what
    `append_frame` produces, and a forged marker at the true end of a message
    is the one shape it would act on.
    """
    if not isinstance(state, dict) or not state:
        return "I submitted the form with nothing filled in."
    parts = []
    for key, value in state.items():
        label = str(key).replace("_", " ").strip().capitalize()
        parts.append(f"{label}: {_clean(value)}")
    return "I filled in the form — " + " · ".join(parts)


def _clean(value: object) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace(protocol.OPENING_MARKER.strip(), "")
        .replace(protocol.CLOSING_MARKER.strip(), "")
        .strip()
    )


async def ui_submit(state: EveState) -> dict:
    """Turn a Save tap into an ordinary turn, then get out of the way.

    No model call here, and no frame: unlike the weather tap this replaces,
    a submit has no predetermined answer. `_route_after_context` sends this
    on to `recall` rather than END, so Eve reads the values as a sentence and
    decides herself where they go - memory, a skill, a tool.

    The envelope's `state` IS trusted, which inverts ADR 0014's rule that the
    envelope is never trusted. There is nothing to re-read: the member's
    typed values are the source of truth. `validate_json_value` still capped
    every string at 2,048 characters on the way in.
    """
    last = state["messages"][-1]
    envelope = parse_action(last.content)
    if envelope is None:
        # Unreachable through `_route_after_context`, which only routes here
        # for an envelope this same function parsed. Defensive, because the
        # alternative is corrupting a real member message.
        return {}
    return {
        "messages": [
            HumanMessage(content=readable_submission(envelope.get("state")), id=last.id)
        ]
    }
