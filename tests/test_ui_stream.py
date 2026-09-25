"""The capability handshake in, and `custom`-mode frames out."""

from __future__ import annotations

from eve.ui import protocol, stream

CAPABILITIES = {
    "protocol": "assistant-ui/1.0",
    "catalogVersion": "1",
    "catalogIds": ["text", "card", "column"],
}


def _config(**overrides) -> dict:
    declared = {**CAPABILITIES, **overrides}
    return {"configurable": {"assistant_ui": declared}}


def _create(surface_id: str = "sf-1") -> dict:
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
    assert stream.supports(_config(), {"text"}) is True
    assert stream.supports(_config(), {"segmentedSelection"}) is False


def test_supports_fails_closed():
    """A client that declared nothing cannot render anything. Emitting at it
    would put an unreadable frame in the transcript forever, since history is
    replayed from the AI message text."""
    assert stream.supports(None, {"text"}) is False
    assert stream.supports(_config(protocol="assistant-ui/2.0"), {"text"}) is False
    assert stream.supports(_config(catalogVersion="2"), {"text"}) is False
    assert stream.supports(_config(catalogIds="text"), {"text"}) is False


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


def test_supports_requires_every_requested_type():
    assert stream.supports(_config(catalogIds=["card", "text"]), {"card", "text"}) is True
    assert stream.supports(_config(catalogIds=["card", "text"]), {"card", "numberField"}) is False


def test_an_older_client_still_gets_the_types_it_declared():
    """Per-type gating rather than a version check is what keeps a phone on
    an old build useful: it can genuinely render a text/card summary, and is
    refused only the trees containing inputs."""
    old = _config(catalogIds=["column", "row", "card", "text", "badge"])
    assert stream.supports(old, {"card", "text"}) is True
    assert stream.supports(old, {"card", "textField"}) is False


def test_an_empty_request_is_supported_by_any_declaring_client():
    """A tree of zero components is degenerate but not a capability
    failure - `validate_operation` is what rejects it, with a diagnostic."""
    assert stream.supports(_config(catalogIds=["card"]), set()) is True


def test_an_undeclared_client_supports_nothing():
    assert stream.supports(None, {"card"}) is False
    assert stream.supports({}, set()) is False


# --- tool_labels -------------------------------------------------------
#
# The client re-validates every pair and drops what fails SILENTLY, falling
# back to the sentence-cased raw tool name. That fallback is indistinguishable
# from not having shipped labels at all, so every rule below is checked here
# rather than discovered by nobody in production.


def test_emit_tool_labels_writes_under_the_tool_labels_key(monkeypatch):
    written = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: written.append)

    assert stream.emit_tool_labels({"get_forecast": "Checking the forecast"}) is True
    assert written == [{"tool_labels": {"get_forecast": "Checking the forecast"}}]


def test_tool_labels_drop_non_string_pairs_one_at_a_time():
    """Pair by pair, not all-or-nothing: one bad label must not cost the
    others."""
    assert stream.sanitise_tool_labels(
        {
            "get_forecast": "Checking the forecast",
            "create_event": None,
            7: "Counting",
            "list_mail": ["Reading your mail"],
        }
    ) == {"get_forecast": "Checking the forecast"}


def test_tool_labels_drop_blank_names_and_blank_labels():
    assert stream.sanitise_tool_labels(
        {"": "Checking the forecast", "get_forecast": "   ", "ask_mail": "\t\n"}
    ) == {}


def test_tool_labels_are_trimmed_on_both_sides():
    """Both are trimmed before the blank and length checks, so a label that
    is only too long because of padding still ships."""
    assert stream.sanitise_tool_labels(
        {"  get_forecast  ": "  Checking the forecast  "}
    ) == {"get_forecast": "Checking the forecast"}


def test_the_label_ceiling_is_sixty_characters_inclusive():
    """The boundary from both sides. 60 is the client's hard ceiling and it
    rejects outright rather than truncating, so an off-by-one here means the
    label silently never appears."""
    assert stream.MAX_TOOL_LABEL == 60
    assert stream.sanitise_tool_labels({"t": "x" * 60}) == {"t": "x" * 60}
    assert stream.sanitise_tool_labels({"t": "x" * 61}) == {}


def test_emit_tool_labels_writes_nothing_when_every_pair_is_invalid(monkeypatch):
    """An empty frame is one the client walks and discards - cost with no
    rendering difference."""
    written = []
    monkeypatch.setattr(stream, "get_stream_writer", lambda: written.append)

    assert stream.emit_tool_labels({}) is False
    assert stream.emit_tool_labels({"get_forecast": "x" * 61}) is False
    assert written == []


def test_emit_tool_labels_tolerates_a_non_dict():
    """Takes `object` for the same reason `eve.suggest.clean` does: a caller
    handing this the wrong shape produces no labels, not an AttributeError
    inside a graph node."""
    assert stream.sanitise_tool_labels(None) == {}
    assert stream.sanitise_tool_labels(["get_forecast"]) == {}
    assert stream.sanitise_tool_labels("get_forecast") == {}


def test_emit_tool_labels_returns_false_rather_than_raising_outside_a_run():
    """`get_stream_writer()` raises outside a runnable context. A label is a
    nicety; nothing about it may cost a member an answer."""
    assert stream.emit_tool_labels({"get_forecast": "Checking the forecast"}) is False


def test_emit_tool_labels_survives_a_writer_that_raises(monkeypatch, caplog):
    """A closed Aegra queue must not fail the turn, and the diagnostic is a
    count - never the labels, never member text."""

    def _explode(_frame):
        raise RuntimeError("queue closed")

    monkeypatch.setattr(stream, "get_stream_writer", lambda: _explode)

    with caplog.at_level("WARNING"):
        assert stream.emit_tool_labels({"ask_mail": "Reading your mail"}) is False

    assert "tool_labels write failed" in caplog.text
    assert "Reading your mail" not in caplog.text


def test_catalog_versions_defaults_to_the_baseline():
    assert stream.catalog_versions({}) == frozenset({"1"})
    assert stream.catalog_versions({"configurable": {"catalog_versions": ["1", "2"]}}) == {"1", "2"}
    # Unknown versions are ignored rather than trusted; junk is ignored.
    assert stream.catalog_versions({"configurable": {"catalog_versions": ["2", "9"]}}) == {"1", "2"}
    assert stream.catalog_versions({"configurable": {"catalog_versions": "2"}}) == {"1"}
