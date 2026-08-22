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
