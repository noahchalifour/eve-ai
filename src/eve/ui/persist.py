"""Copy this turn's surfaces into the AI message, so a card survives a
relaunch.

`custom`-mode frames are STREAMED, never stored: they are not part of
`messages`, and `LangGraphAgentService.loadHistory` rebuilds a reopened
session from `GET /threads/{id}/state` -> `values.messages` and nothing else.
The client's own surface cache does not save us either -
`_mergeCachedLocalState` restores only `localState`, and only onto a surface
the provider's history just returned. Without this node, asking about the
weather and relaunching the app shows the turn with no card in it.

The same operation written into the AI message as a portable
`<assistant-ui>` frame is stripped from what the member sees and from TTS on
every path, automatically, so it costs nothing visible.

It also does not double-render. LangGraph's `messages` stream mode carries
LLM token events only; a node returning an updated message emits nothing on
it. The live client renders the card once, from the `custom` frame, and this
text reaches it only through history.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from eve.state import EveState
from eve.ui import protocol

logger = logging.getLogger(__name__)


async def persist_ui(state: EveState) -> dict:
    """`{}` for every turn that emitted no surface - which is nearly all of
    them."""
    operations: list[dict] = []
    final: AIMessage | None = None
    # Backwards to the last human message: a card from an earlier turn is
    # already in that turn's own AI message, and re-appending it would
    # replay a duplicate surfaceId on reopen. Same "read back to the last
    # HumanMessage" idiom as `graph._tool_rounds_this_turn`, for the same
    # reason - the transcript already knows where the turn started.
    for message in reversed(state["messages"]):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage) and final is None:
            final = message
        if isinstance(message, ToolMessage) and _is_operation(message.artifact):
            operations.append(message.artifact)
    if not operations or final is None:
        return {}
    operations.reverse()
    if len(operations) > protocol.MAX_SURFACES_PER_TURN:
        # Never silently. The ninth create in one frame makes the client
        # reject the WHOLE frame, taking the eight valid surfaces with it, so
        # the cap has to be enforced here and said out loud.
        logger.warning(
            "assistant_ui surfaces dropped: %d over the per-turn limit",
            len(operations) - protocol.MAX_SURFACES_PER_TURN,
        )
        operations = operations[: protocol.MAX_SURFACES_PER_TURN]
    return {"messages": [_with_frame(final, operations)]}


def _is_operation(artifact: object) -> bool:
    """A dynamically-materialized tool may set an artifact for its own
    reasons; only a STRUCTURALLY VALID `assistant-ui/1.0` operation belongs
    in a frame. Checking just the protocol key would let one malformed
    operation - e.g. `{"protocol": "assistant-ui/1.0", "op": "nonsense"}` -
    into the frame, and the client rejects the WHOLE frame on one invalid
    operation, silently taking every valid surface in it down with it."""
    return protocol.validate_operation(artifact) is None


def _with_frame(message: AIMessage, operations: list[dict]) -> AIMessage:
    """`model_copy`, not a freshly-built `AIMessage`: `add_messages` replaces
    by id, it does not merge fields, so constructing a new message here
    would silently discard `response_metadata`, `usage_metadata`,
    `additional_kwargs`, `name` and `tool_calls` off the final message of
    every surface turn - the same id is not enough on its own.

    `protocol.append_frame` is the one builder a frame is ever produced with -
    `persist_ui` is its sole producer - so `strip_frames`/
    `strip_frames_from_content` only ever have one shape to invert."""
    return message.model_copy(
        update={"content": protocol.append_frame(message.content, operations)}
    )
