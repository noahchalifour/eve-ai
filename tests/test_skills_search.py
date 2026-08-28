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
        lambda mcp_tools=None, authored=None: [
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
        lambda mcp_tools=None, authored=None: [
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
        "S", (), {"dynamic_tools_cap": 1, "self_authoring_enabled": False}
    )())
    existing: DynamicToolSpec = {
        "server_id": "a", "tool_name": "x", "description": "roll dice", "schema": {}
    }
    new_spec: DynamicToolSpec = {
        "server_id": "b", "tool_name": "y", "description": "roll dice", "schema": {}
    }
    monkeypatch.setattr(
        "eve.skills.search.load_skills",
        lambda mcp_tools=None, authored=None: [
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


async def test_search_skills_returns_an_authored_procedure(monkeypatch, tmp_path):
    from datetime import UTC, datetime

    from eve.memory.types import Memory
    from eve.skills import search as search_mod
    from eve.skills.authoring import serialize_procedure

    now = datetime(2026, 8, 27, tzinfo=UTC)
    row = Memory(
        id="p1", layer="procedure", scope_kind="member", scope_id="sub-noah",
        kind="decision", subject="book-the-dog-sitter",
        content=serialize_procedure(
            "book-the-dog-sitter", "How to book the dog sitter.", "1. Text Sam."
        ),
        confidence=0.8, salience=0.5, created_at=now, last_seen_at=now,
    )

    async def load_procedures(sub):
        return [row]

    async def embed_query(text):
        return [1.0] + [0.0] * 1535

    monkeypatch.setattr(search_mod, "load_procedures", load_procedures)
    monkeypatch.setattr(search_mod, "embed_query", embed_query)
    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()

    state = {
        "messages": [],
        "member": MEMBER,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
    }
    command = await search_mod.search_skills.ainvoke(
        {
            "name": "search_skills",
            "args": {"query": "dog sitter", "state": state},
            "id": "c1",
            "type": "tool_call",
        }
    )
    content = command.update["messages"][0].content
    assert "book-the-dog-sitter" in content
    assert "1. Text Sam." in content


async def test_search_skills_omits_authored_procedures_when_disabled(
    monkeypatch, tmp_path
):
    """Mirrors test_load_always_on_omits_rules_by_default for the procedure
    layer: with EVE_SELF_AUTHORING_ENABLED off, rows written during an earlier
    enabled period must stop applying, not merely stop being written. The
    database is not even asked - Phase 4 never paid that round trip."""
    from datetime import UTC, datetime

    from eve.memory.types import Memory
    from eve.skills import search as search_mod
    from eve.skills.authoring import serialize_procedure

    now = datetime(2026, 8, 27, tzinfo=UTC)
    row = Memory(
        id="p1", layer="procedure", scope_kind="member", scope_id="sub-noah",
        kind="decision", subject="book-the-dog-sitter",
        content=serialize_procedure(
            "book-the-dog-sitter", "How to book the dog sitter.", "1. Text Sam."
        ),
        confidence=0.8, salience=0.5, created_at=now, last_seen_at=now,
    )
    calls = []

    async def load_procedures(sub):
        calls.append(sub)
        return [row]

    async def embed_query(text):
        return [1.0] + [0.0] * 1535

    monkeypatch.setattr(search_mod, "load_procedures", load_procedures)
    monkeypatch.setattr(search_mod, "embed_query", embed_query)
    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    state = {
        "messages": [],
        "member": MEMBER,
        "system_prompt": "",
        "memory": None,
        "dynamic_tools": [],
    }
    command = await search_mod.search_skills.ainvoke(
        {
            "name": "search_skills",
            "args": {"query": "dog sitter", "state": state},
            "id": "c1",
            "type": "tool_call",
        }
    )

    assert calls == []
    assert "1. Text Sam." not in command.update["messages"][0].content


def test_load_skills_parses_an_authored_row_like_a_file():
    from datetime import UTC, datetime

    from eve.memory.types import Memory
    from eve.skills.authoring import serialize_procedure
    from eve.skills.registry import load_skills

    now = datetime(2026, 8, 27, tzinfo=UTC)
    row = Memory(
        id="p1", layer="procedure", scope_kind="member", scope_id="sub-noah",
        kind="decision", subject="a-name",
        content=serialize_procedure("a-name", "A description.", "The body."),
        confidence=0.8, salience=0.5, created_at=now, last_seen_at=now,
    )
    skills = [s for s in load_skills(authored=[row]) if s.name == "a-name"]

    assert len(skills) == 1
    assert skills[0].kind == "procedure"
    assert skills[0].description == "A description."
    assert skills[0].content == "The body."
