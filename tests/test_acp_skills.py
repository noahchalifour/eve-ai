"""EVE-45: every ACP coding session gets Noah's agent-skills.

The four agents read skills from two flat directories under $HOME between
them (dsh and Codex: ~/.agents/skills; Claude Code: ~/.claude/skills;
OpenCode: both). `skills.sync()` keeps a clone of the repository on the PVC
and links each skill into both, the same shape as the symlinks on Noah's
laptop. Each test here is one way that could go wrong on a pod start.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from eve_computer.acp import skills


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write_skill(origin, domain, name):
    target = origin / "skills" / domain / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n")


def _commit(origin, message="update"):
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", message)


@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _write_skill(repo, "engineering", "foo")
    _write_skill(repo, "writing", "bar")
    _commit(repo, "initial")
    return repo


@pytest.fixture(autouse=True)
def _settings(monkeypatch, tmp_path, origin):
    monkeypatch.setenv("EVE_COMPUTER_AGENT_SKILLS_REPO", str(origin))
    monkeypatch.setenv("EVE_COMPUTER_AGENT_SKILLS_DIR", str(tmp_path / "clone"))
    monkeypatch.setenv(
        "EVE_COMPUTER_AGENT_SKILLS_LINK_DIRS",
        f'["{tmp_path / "agents"}", "{tmp_path / "claude"}"]',
    )
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    yield
    get_computer_settings.cache_clear()


def _link_dirs(tmp_path):
    return [tmp_path / "agents", tmp_path / "claude"]


def test_first_sync_links_every_skill_into_every_agent_directory(tmp_path):
    assert sorted(skills.sync()) == ["bar", "foo"]

    for link_dir in _link_dirs(tmp_path):
        foo = link_dir / "foo"
        assert foo.is_symlink()
        assert foo.resolve() == (tmp_path / "clone" / "skills" / "engineering" / "foo").resolve()
        assert "name: foo" in (foo / "SKILL.md").read_text()


def test_a_later_sync_adds_new_skills_and_prunes_removed_ones(tmp_path, origin):
    skills.sync()
    shutil.rmtree(origin / "skills" / "writing" / "bar")
    _write_skill(origin, "writing", "baz")
    _commit(origin)

    assert sorted(skills.sync()) == ["baz", "foo"]
    for link_dir in _link_dirs(tmp_path):
        assert (link_dir / "baz" / "SKILL.md").exists()
        assert not (link_dir / "bar").is_symlink()


def test_an_unreachable_repo_with_no_clone_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_AGENT_SKILLS_REPO", str(tmp_path / "missing"))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()

    assert skills.sync() == []
    assert not (tmp_path / "clone").exists()


def test_an_unreachable_repo_keeps_the_last_good_clone(tmp_path, origin):
    skills.sync()
    shutil.rmtree(origin)

    assert sorted(skills.sync()) == ["bar", "foo"]
    assert (tmp_path / "agents" / "foo" / "SKILL.md").exists()


def test_a_hand_installed_skill_is_never_replaced(tmp_path):
    own = tmp_path / "agents" / "foo"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("mine")

    skills.sync()

    assert not own.is_symlink()
    assert (own / "SKILL.md").read_text() == "mine"
    assert (tmp_path / "agents" / "bar").is_symlink()
    assert (tmp_path / "claude" / "foo").is_symlink()


def test_a_symlink_to_somewhere_else_is_left_alone(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "foo").symlink_to(elsewhere)

    skills.sync()

    assert (tmp_path / "agents" / "foo").resolve() == elsewhere.resolve()


def test_a_directory_without_skill_md_is_not_a_skill(tmp_path, origin):
    (origin / "skills" / "engineering" / "notes").mkdir()
    (origin / "skills" / "engineering" / "notes" / "README.md").write_text("x")
    _commit(origin)

    assert "notes" not in skills.sync()


def test_an_empty_repo_setting_disables_the_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_AGENT_SKILLS_REPO", "")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()

    assert skills.sync() == []
    assert not (tmp_path / "agents").exists()
