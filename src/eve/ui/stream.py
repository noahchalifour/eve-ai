"""The capability handshake in, `custom`-mode frames out. The only module in
this package that touches LangGraph's runtime.
"""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from eve.ui import protocol

logger = logging.getLogger(__name__)


def capabilities(config: RunnableConfig | None) -> dict | None:
    """The client's `DynamicUiCapabilities.toJson()`, or None.

    `config.configurable`, NOT run metadata. LangGraph indexes run metadata
    and rejects anything but a scalar there ("metadata value for key
    'assistant_ui' must be str/int/float/bool, got dict"), so the client
    sends the object under `config.configurable.assistant_ui` instead -
    `langgraph_agent_service.dart:318-321` carries the same comment. Aegra
    merges request config into the graph config verbatim
    (`aegra_api/services/run_preparation.py`), so it arrives here unchanged.
    EVE-12's own description says `metadata.assistant_ui`; that is the
    OpenClaw gateway's shape, not LangGraph's.
    """
    configurable = (config or {}).get("configurable") or {}
    declared = configurable.get("assistant_ui")
    return declared if isinstance(declared, dict) else None


def supports(
    config: RunnableConfig | None, catalog_ids: set[str] | frozenset[str]
) -> bool:
    """Whether the client declared EVERY id in `catalog_ids`. Fails CLOSED.

    A set rather than one id, because a model-authored tree uses many types
    at once and the gate has to be all-or-nothing: a surface is emitted whole
    or not at all, and there is no partial render to fall back to.

    A client that declared nothing cannot render anything, and a surface is
    not free to emit at it: `eve.ui.persist` writes the same operation into
    the AI message so the card survives a reopen, which would leave an
    unreadable frame in that thread's transcript permanently. Silence is the
    correct answer, and Eve still has words.
    """
    declared = capabilities(config)
    if declared is None:
        return False
    if declared.get("protocol") != protocol.PROTOCOL:
        return False
    if declared.get("catalogVersion") != protocol.CATALOG_VERSION:
        return False
    ids = declared.get("catalogIds")
    if not isinstance(ids, list):
        return False
    return set(catalog_ids).issubset(ids)


def catalog_versions(config: RunnableConfig | None) -> frozenset[str]:
    """Which surface catalog versions the connected client renders (EVE-21,
    spec 4.2). A client from before versioning sends nothing and means "1".
    Unknown versions are dropped: advertising "9" must not make this server
    stamp something it cannot validate."""
    declared = ((config or {}).get("configurable") or {}).get("catalog_versions")
    known = set(protocol.CATALOG_VERSIONS)
    advertised = set(declared) & known if isinstance(declared, list) else set()
    return frozenset(advertised | {protocol.CATALOG_VERSION})


def emit(operation: dict) -> bool:
    """Validate, then write one operation to the `custom` stream.

    `{"assistant_ui": <one operation>}` is the exact envelope
    `LangGraphAgentService._handleCustom` unwraps; anything else on the
    `custom` channel is ignored by the client rather than erroring.

    Returns False and never raises. Every caller is a tool or a node whose
    failure has to degrade to ordinary Eve prose - the same posture as
    `eve.tools_client.invoke` - and `get_stream_writer()` itself raises
    `RuntimeError` outside a runnable context.
    """
    error = protocol.validate_operation(operation)
    if error is not None:
        # Structural diagnostics only. The client's own logging holds the
        # same line for the same reason.
        logger.warning(
            "assistant_ui operation rejected: %s (op=%r)",
            error,
            operation.get("op") if isinstance(operation, dict) else None,
        )
        return False
    try:
        get_stream_writer()({"assistant_ui": operation})
    except Exception:
        logger.warning("assistant_ui write failed (op=%r)", operation.get("op"))
        return False
    return True


# The client rejects a longer label outright rather than truncating it, so a
# label over this ceiling does not appear at all. One line on a phone.
MAX_TOOL_LABEL = 60


def sanitise_tool_labels(labels: object) -> dict[str, str]:
    """The client's own validation, applied before the write rather than
    after it.

    The client re-validates every pair and drops what fails SILENTLY - a
    too-long label degrades to the sentence-cased raw tool name, which is
    exactly what today's behaviour looks like. So a violation here would be
    invisible in production and indistinguishable from not having shipped
    this at all. Validating server-side turns it into a test failure instead;
    `tests/test_graph.py` runs the whole label table through this function
    for that reason.

    Pair by pair, not all-or-nothing: one bad label must not cost every other
    label in the map. Takes `object` rather than `dict[str, str]` for the
    same reason `eve.suggest.clean` does - a caller handing this something
    else should produce no labels, not an `AttributeError` in a graph node.
    """
    if not isinstance(labels, dict):
        return {}
    clean: dict[str, str] = {}
    for name, label in labels.items():
        if not isinstance(name, str) or not isinstance(label, str):
            continue
        name, label = name.strip(), label.strip()
        if not name or not label or len(label) > MAX_TOOL_LABEL:
            continue
        clean[name] = label
    return clean


def emit_tool_labels(labels: dict[str, str]) -> bool:
    """Write one `tool_labels` frame to the `custom` stream.

    `{"tool_labels": {<raw tool name>: <activity phrase>}}` - the raw name as
    it appears in `tool_call_chunks[].name` and `ToolMessage.name`, never the
    per-invocation call id. The client maps name to id itself, and applies a
    label that arrives before, during or after the call it names, so the only
    thing timing costs is which of those three paths runs.

    Coexists with `assistant_ui` and `suggestions`: the client reads one key
    per frame and ignores the rest, so these need no coordination.

    Returns False and never raises, the same posture as `emit` above. A label
    is a nicety - without one the client sentence-cases the raw tool name and
    the turn is unaffected - so nothing here may cost a member an answer.
    """
    clean = sanitise_tool_labels(labels)
    if not clean:
        # Nothing to say. Emitting `{}` would be a frame the client walks and
        # discards, which is cost without a rendering difference.
        return False
    try:
        writer = get_stream_writer()
    except RuntimeError:
        # No runnable context - a direct call in a test, not a bug. Same
        # split as `eve.suggest._emit`: this one is expected and quiet, the
        # one below means delivery itself broke.
        logger.debug("no runnable context to emit the tool_labels frame")
        return False
    try:
        writer({"tool_labels": clean})
    except Exception:
        # Structural diagnostics only, as in `emit`: a count, never the
        # labels themselves and never member text.
        logger.warning("tool_labels write failed (%d labels)", len(clean))
        return False
    return True
