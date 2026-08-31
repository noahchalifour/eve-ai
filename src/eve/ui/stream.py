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


def supports(config: RunnableConfig | None, catalog_id: str) -> bool:
    """Fails CLOSED.

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
    return isinstance(ids, list) and catalog_id in ids


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
