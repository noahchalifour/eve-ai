from langchain_core.messages import ToolMessage

from eve.skills.registry import Skill
from eve.skills.search import rank_skills, search_skills
from eve.skills.types import DynamicToolSpec
from tests.test_specialists_base import MEMBER


async def _fake_embed(text: str) -> list[float]:
    # A tiny deterministic embedding: closer strings get closer vectors by
    # counting shared words, so ranking is exercisable without a real model.
    words = set(text.lower().split())
    return [1.0 if w in words else 0.0 for w in ("greet", "warmly", "dice", "roll")]


async def test_rank_skills_prefers_the_closer_match(monkeypatch):
    monkeypatch.setattr("eve.skills.search.embed_query", _fake_embed)
    skills = [
        Skill(name="greet-warmly", description="greet warmly", kind="procedure", content="..."),
        Skill(name="roll-dice", description="roll dice", kind="procedure", content="..."),
    ]
    ranked = await rank_skills("please greet warmly", skills, top_k=1)
    assert ranked[0].name == "greet-warmly"


async def test_search_skills_returns_a_procedure_directly(monkeypatch):
    monkeypatch.setattr("eve.skills.search.embed_query", _fake_embed)
    monkeypatch.setattr(
        "eve.skills.search.load_skills",
        lambda mcp_tools=None: [
            Skill(
                name="greet-warmly",
                description="greet warmly",
                kind="procedure",
                content="Use their first name.",
            )
        ],
    )
    state = {
        "messages": [],
        "member": MEMBER,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
    }
    command = await search_skills.ainvoke(
        {
            "name": "search_skills",
            "args": {"query": "how should I greet warmly", "state": state},
            "id": "call-1",
            "type": "tool_call",
        }
    )
    message = command.update["messages"][0]
    assert isinstance(message, ToolMessage)
    assert "Use their first name." in message.content
    assert command.update.get("dynamic_tools", []) == []


async def test_search_skills_adds_an_mcp_match_to_dynamic_tools(monkeypatch):
    monkeypatch.setattr("eve.skills.search.embed_query", _fake_embed)
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "roll_dice",
        "description": "roll dice",
        "schema": {"properties": {}},
    }
    monkeypatch.setattr(
        "eve.skills.search.load_skills",
        lambda mcp_tools=None: [
            Skill(
                name="mock-server.roll_dice",
                description="roll dice",
                kind="mcp_tool",
                content="roll dice",
                spec=spec,
            )
        ],
    )
    state = {
        "messages": [],
        "member": MEMBER,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
    }
    command = await search_skills.ainvoke(
        {
            "name": "search_skills",
            "args": {"query": "roll dice for me", "state": state},
            "id": "call-1",
            "type": "tool_call",
        }
    )
    assert command.update["dynamic_tools"] == [spec]


async def test_search_skills_caps_dynamic_tools(monkeypatch):
    monkeypatch.setattr("eve.skills.search.embed_query", _fake_embed)
    monkeypatch.setattr("eve.skills.search.get_settings", lambda: type(
        "S", (), {"dynamic_tools_cap": 1}
    )())
    existing: DynamicToolSpec = {
        "server_id": "a", "tool_name": "x", "description": "roll dice", "schema": {}
    }
    new_spec: DynamicToolSpec = {
        "server_id": "b", "tool_name": "y", "description": "roll dice", "schema": {}
    }
    monkeypatch.setattr(
        "eve.skills.search.load_skills",
        lambda mcp_tools=None: [
            Skill(name="b.y", description="roll dice", kind="mcp_tool", content="", spec=new_spec)
        ],
    )
    state = {
        "messages": [],
        "member": MEMBER,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [existing],
    }
    command = await search_skills.ainvoke(
        {
            "name": "search_skills",
            "args": {"query": "roll dice", "state": state},
            "id": "call-1",
            "type": "tool_call",
        }
    )
    assert command.update["dynamic_tools"] == [new_spec]
