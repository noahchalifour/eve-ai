"""Widget-mode validation. The case file is shared byte-for-byte with
open-assistant (test/fixtures/dynamic_ui/widget_surface_cases.json); if this
file changes, copy it there and run the Dart test too."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eve.ui import protocol

CASES = json.loads((Path(__file__).parent / "fixtures" / "widget_surface_cases.json").read_text())


def _op(components):
    return {"protocol": protocol.PROTOCOL, "op": "create", "surface": {
        "surfaceId": "widget:x", "catalogId": "column", "catalogVersion": "1",
        "components": components, "data": {}, "localState": {}}}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_widget_cases(case):
    error = protocol.validate_operation(_op(case["components"]), widget=True)
    assert (error is None) == case["valid"], error


def test_chat_mode_still_rejects_a_tappable_card():
    components = CASES[0]["components"]
    assert protocol.validate_operation(_op(components)) == "component-schema"


def test_chat_mode_still_rejects_a_namespaced_action():
    assert protocol.validate_operation(_op(CASES[1]["components"])) == "action-schema"
