"""The inbound half of the protocol.

A tap on a rendered surface is not a separate channel: the client re-runs the
turn with the user message's content REPLACED by an encoded action envelope
(`AssistantSessionUseCase.submitUiAction` -> `LangGraphAgentService.run`), so
it reaches the graph as an ordinary `HumanMessage` and something has to tell
it apart from a member typing.
"""

from __future__ import annotations

import json

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
