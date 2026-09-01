from pathlib import Path

from eve.skills import mcp_registry
from eve.skills.registry import load_skills
from eve.skills.types import DynamicToolSpec


def test_a_skill_without_a_specialist_key_belongs_to_eve():
    from eve.skills.registry import parse_skill_text

    name, description, body, specialist = parse_skill_text(
        "---\nname: greet\ndescription: how to greet\n---\nUse their name.", "fallback"
    )

    assert name == "greet"
    assert description == "how to greet"
    assert body == "Use their name."
    assert specialist is None


def test_a_specialist_key_is_parsed():
    from eve.skills.registry import parse_skill_text

    _, _, _, specialist = parse_skill_text(
        "---\nname: dress\ndescription: d\nspecialist: stylist\n---\nBody.", "fallback"
    )

    assert specialist == "stylist"


def test_load_skills_carries_the_specialist_through(tmp_path, monkeypatch):
    from eve.settings import get_settings
    from eve.skills.registry import load_skills

    (tmp_path / "dress-for-the-day").mkdir()
    (tmp_path / "dress-for-the-day" / "SKILL.md").write_text(
        "---\nname: dress-for-the-day\ndescription: outfits\nspecialist: stylist\n---\nBody."
    )
    (tmp_path / "greet-warmly").mkdir()
    (tmp_path / "greet-warmly" / "SKILL.md").write_text(
        "---\nname: greet-warmly\ndescription: greeting\n---\nBody."
    )
    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    get_settings.cache_clear()

    by_name = {s.name: s for s in load_skills()}

    assert by_name["dress-for-the-day"].specialist == "stylist"
    assert by_name["greet-warmly"].specialist is None


def test_loads_a_skill_md_with_frontmatter(tmp_path, monkeypatch):
    skill_dir = tmp_path / "greet-warmly"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: greet-warmly\n"
        "description: How to greet a family member warmly.\n"
        "---\n"
        "Use their first name and ask about their day.\n"
    )
    monkeypatch.setattr(
        "eve.settings.get_settings",
        lambda: type("S", (), {"skills_dir": tmp_path})(),
    )
    import eve.skills.registry as registry_module

    monkeypatch.setattr(registry_module, "get_settings", lambda: type(
        "S", (), {"skills_dir": tmp_path}
    )())
    skills = load_skills()
    assert len(skills) == 1
    assert skills[0].name == "greet-warmly"
    assert skills[0].kind == "procedure"
    assert "first name" in skills[0].content


def test_missing_skills_dir_yields_no_procedures(tmp_path, monkeypatch):
    import eve.skills.registry as registry_module

    monkeypatch.setattr(
        registry_module, "get_settings", lambda: type("S", (), {"skills_dir": tmp_path / "nope"})()
    )
    assert load_skills() == []


def test_registered_mcp_tools_become_skills(tmp_path, monkeypatch):
    import eve.skills.registry as registry_module

    monkeypatch.setattr(
        registry_module, "get_settings", lambda: type("S", (), {"skills_dir": tmp_path})()
    )
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "roll_dice",
        "description": "Roll a die and return the result.",
        "schema": {"properties": {}},
    }
    skills = load_skills(mcp_tools=[spec])
    assert len(skills) == 1
    assert skills[0].kind == "mcp_tool"
    assert skills[0].spec == spec


def test_mcp_registry_round_trips():
    spec: DynamicToolSpec = {
        "server_id": "mock-server",
        "tool_name": "roll_dice",
        "description": "Roll a die.",
        "schema": {"properties": {}},
    }
    mcp_registry.register(spec)
    assert spec in mcp_registry.registered_mcp_tools()
