"""The capability handshake in, and `custom`-mode frames out."""

from __future__ import annotations

from eve.ui import protocol, stream

CAPABILITIES = {
    "protocol": "assistant-ui/1.0",
    "catalogVersion": "1",
    "catalogIds": ["weather", "text", "card"],
}


def _config(**overrides) -> dict:
    declared = {**CAPABILITIES, **overrides}
    return {"configurable": {"assistant_ui": declared}}


def _create(surface_id: str = "wx-1") -> dict:
    return {
        "protocol": protocol.PROTOCOL,
        "op": "create",
        "surface": {
            "surfaceId": surface_id,
            "catalogId": "weather",
            "catalogVersion": "1",
            "components": [],
            "data": {},
            "localState": {},
        },
    }


def test_capabilities_are_read_from_configurable_not_metadata():
    """LangGraph indexes run metadata and rejects a non-scalar value there,
    so the client sends the declaration under `config.configurable`
    (langgraph_agent_service.dart:318-321). Reading `metadata` would find
    nothing, forever, with no error to say so."""
    assert stream.capabilities(_config()) == CAPABILITIES
    assert stream.capabilities({"metadata": {"assistant_ui": CAPABILITIES}}) is None


def test_capabilities_tolerate_a_config_that_declares_nothing():
    assert stream.capabilities(None) is None
    assert stream.capabilities({}) is None
    assert stream.capabilities({"configurable": {}}) is None
    assert stream.capabilities({"configurable": {"assistant_ui": "yes"}}) is None


def test_supports_is_true_only_for_a_declared_catalog_id():
    assert stream.supports(_config(), "weather") is True
    assert stream.supports(_config(), "segmentedSelection") is False


def test_supports_fails_closed():
    """A client that declared nothing cannot render anything. Emitting at it
    would put an unreadable frame in the transcript forever, since history is
    replayed from the AI message text."""
    assert stream.supports(None, "weather") is False
    assert stream.supports(_config(protocol="assistant-ui/2.0"), "weather") is False
    assert stream.supports(_config(catalogVersion="2"), "weather") is False
    assert stream.supports(_config(catalogIds="weather"), "weather") is False


def test_emit_writes_the_operation_under_the_assistant_ui_key(monkeypatch):
    written = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: written.append)

    assert stream.emit(_create()) is True
    assert written == [{"assistant_ui": _create()}]


def test_emit_refuses_an_operation_the_client_would_reject(monkeypatch):
    written = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: written.append)
    operation = _create()
    operation["surface"]["catalogId"] = "thermostat"

    assert stream.emit(operation) is False
    assert written == []


def test_emit_returns_false_rather_than_raising_outside_a_run():
    """`get_stream_writer()` raises outside a runnable context. Every caller
    is a tool or a node whose failure must degrade to ordinary Eve prose -
    the same posture as eve.tools_client.invoke."""
    assert stream.emit(_create()) is False


def test_emit_logs_only_structural_diagnostics(caplog):
    """Privacy-safe logging: the diagnostic code and the operation name, never
    `data`, never member text."""
    operation = _create()
    operation["surface"]["data"] = {"location": "17 Privacy Lane"}
    operation["surface"]["catalogId"] = "thermostat"

    with caplog.at_level("WARNING"):
        stream.emit(operation)

    assert "catalog" in caplog.text
    assert "Privacy Lane" not in caplog.text
