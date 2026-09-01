"""Tests for a specialist's scoped skills search."""

from unittest.mock import AsyncMock

from eve.skills import specialist_search


def _write_skill(root, folder, name, description, specialist=None):
    (root / folder).mkdir()
    lines = ["---", f"name: {name}", f"description: {description}"]
    if specialist:
        lines.append(f"specialist: {specialist}")
    lines += ["---", f"Body of {name}."]
    (root / folder / "SKILL.md").write_text("\n".join(lines))


def _skills_dir(tmp_path, monkeypatch):
    from eve.settings import get_settings

    monkeypatch.setenv("EVE_SKILLS_DIR", str(tmp_path))
    get_settings.cache_clear()


async def test_a_specialist_sees_only_its_own_skills(tmp_path, monkeypatch):
    _write_skill(tmp_path, "dress", "dress-for-the-day", "outfits", "stylist")
    _write_skill(tmp_path, "greet", "greet-warmly", "greeting")
    _write_skill(tmp_path, "triage", "triage-mail", "inbox", "mail")
    _skills_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "eve.skills.search.embed_query", AsyncMock(return_value=[1.0, 0.0])
    )

    tool = specialist_search.build_skills_search("stylist")
    result = await tool.ainvoke({"query": "what should I wear"})

    assert "dress-for-the-day" in result
    assert "Body of dress-for-the-day." in result
    assert "greet-warmly" not in result
    assert "triage-mail" not in result


async def test_a_specialist_with_no_skills_gets_a_clean_answer(tmp_path, monkeypatch):
    _write_skill(tmp_path, "greet", "greet-warmly", "greeting")
    _skills_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "eve.skills.search.embed_query", AsyncMock(return_value=[1.0, 0.0])
    )

    tool = specialist_search.build_skills_search("finances")
    result = await tool.ainvoke({"query": "anything"})

    assert result == specialist_search.NO_MATCH


async def test_the_tool_returns_a_string_not_a_command(tmp_path, monkeypatch):
    """A specialist's loop is create_agent's own message state, not EveState.

    There is no dynamic_tools channel to update and no rebinding step to
    receive a spec, so this side is knowledge only.
    """
    _write_skill(tmp_path, "dress", "dress-for-the-day", "outfits", "stylist")
    _skills_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "eve.skills.search.embed_query", AsyncMock(return_value=[1.0, 0.0])
    )

    tool = specialist_search.build_skills_search("stylist")
    result = await tool.ainvoke({"query": "outfit"})

    assert isinstance(result, str)


def _raise_runtime_error(*_args):
    raise RuntimeError("skills dir missing")


async def test_a_failing_corpus_returns_an_error_string(monkeypatch):
    """Same contract as every specialist tool: a filesystem failure returns
    an error string, it does not fail the specialist's turn."""
    monkeypatch.setattr(specialist_search, "load_skills", _raise_runtime_error)

    tool = specialist_search.build_skills_search("stylist")
    result = await tool.ainvoke({"query": "outfit"})

    assert result.startswith("error:")


async def test_a_failing_ranker_returns_an_error_string(monkeypatch):
    monkeypatch.setattr(
        "eve.skills.search.embed_query",
        AsyncMock(side_effect=RuntimeError("embedder down")),
    )

    tool = specialist_search.build_skills_search("stylist")
    result = await tool.ainvoke({"query": "outfit"})

    assert result.startswith("error:")
