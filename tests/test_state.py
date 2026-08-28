from datetime import UTC, datetime

from eve.skills.types import DynamicToolSpec
from eve.state import EveState


def test_eve_state_carries_dynamic_tools():
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "do_thing",
        "description": "Does a thing.",
        "schema": {"properties": {}},
    }
    state: EveState = {
        "messages": [],
        "member": {
            "sub": "sub-noah",
            "name": "Noah",
            "role": "adult",
            "timezone": "America/Vancouver",
            "permissions": [],
            "local_time": "2026-08-21 09:00 PDT",
        },
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [spec],
    }
    assert state["dynamic_tools"][0]["tool_name"] == "do_thing"


def test_operation_accepts_a_rule_layer():
    from eve.memory.types import Operation

    op = Operation(op="add", layer="rule", kind="preference", content="Lead with the number.")
    assert op.layer == "rule"
    assert op.shared is False


def test_operation_shared_defaults_false():
    """A rule is member-scoped unless the model explicitly asks for a
    household one, which _resolve_scope then permission-checks."""
    from eve.memory.types import Operation

    assert Operation(op="add", layer="rule", content="x.").shared is False


def test_memory_carries_optional_source_thread_and_run():
    from eve.memory.types import Memory

    now = datetime(2026, 8, 27, tzinfo=UTC)
    mem = Memory(
        id="m1", layer="rule", scope_kind="member", scope_id="sub-noah",
        kind="preference", subject=None, content="x.", confidence=0.7,
        salience=0.5, created_at=now, last_seen_at=now,
    )
    assert mem.source_thread is None
    assert mem.source_run is None


def test_ambient_marker_round_trips():
    """One owner for the string. If the guard and the prefix ever decouple,
    an ambient turn silently becomes an authoring turn."""
    from eve.state import ambient_marker, is_ambient_text

    assert is_ambient_text(ambient_marker("Noah") + "\nA package arrived.")


def test_is_ambient_text_is_false_for_a_member_turn():
    from eve.state import is_ambient_text

    assert not is_ambient_text("What's left in the grocery budget?")


def test_is_ambient_text_tolerates_leading_whitespace():
    from eve.state import ambient_marker, is_ambient_text

    assert is_ambient_text("\n  " + ambient_marker("Kendra"))


def test_is_ambient_text_handles_an_empty_string():
    from eve.state import is_ambient_text

    assert not is_ambient_text("")
