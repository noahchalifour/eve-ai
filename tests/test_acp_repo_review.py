"""Real git against a real temporary origin, mirroring test_acp_repo.py.

Mocking git here would test the mock: `refs/pull/<n>/head`, detached
worktrees, and merge-base computation are exactly the parts that would break
in production while a mocked test stayed green.
"""

from __future__ import annotations

import subprocess

import pytest

from eve_computer.acp import repo as repo_mod
from eve_computer.acp.repo import add_review_worktree


def _run(*args, cwd):
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path):
    """A bare origin with `main`, plus a PR branch published at
    `refs/pull/7/head` the way GitHub publishes one."""
    bare = tmp_path / "origin" / "acme.git"
    bare.mkdir(parents=True)
    _run("git", "init", "--bare", "--initial-branch=main", ".", cwd=bare)

    seed = tmp_path / "seed"
    seed.mkdir()
    _run("git", "init", "--initial-branch=main", ".", cwd=seed)
    _run("git", "config", "user.email", "eve@example.com", cwd=seed)
    _run("git", "config", "user.name", "Eve", cwd=seed)
    (seed / "README.md").write_text("hello\n")
    _run("git", "add", "README.md", cwd=seed)
    _run("git", "commit", "-m", "seed", cwd=seed)
    _run("git", "remote", "add", "origin", str(bare), cwd=seed)
    _run("git", "push", "-u", "origin", "main", cwd=seed)

    # The pull request: one commit off main, published where GitHub puts it.
    _run("git", "checkout", "-b", "feature", cwd=seed)
    (seed / "feature.py").write_text("def f():\n    return 1\n")
    _run("git", "add", "feature.py", cwd=seed)
    _run("git", "commit", "-m", "add feature", cwd=seed)
    _run("git", "push", "origin", "feature:refs/pull/7/head", cwd=seed)

    # main moves on afterwards, so a two-dot diff would show this commit as
    # part of the pull request and a three-dot diff would not.
    _run("git", "checkout", "main", cwd=seed)
    (seed / "unrelated.py").write_text("x = 1\n")
    _run("git", "add", "unrelated.py", cwd=seed)
    _run("git", "commit", "-m", "unrelated work on main", cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)
    return bare


@pytest.fixture(autouse=True)
def _settings(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_CODE_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_GITHUB_OWNER", "acme")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    monkeypatch.setattr(repo_mod, "_clone_url", lambda name: str(origin))
    yield
    get_computer_settings.cache_clear()


async def test_the_worktree_holds_the_pull_requests_code(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    result = await add_review_worktree("acme", session_dir, 7, "main")

    assert (result["path"] / "feature.py").exists()


async def test_the_checkout_is_detached_so_no_branch_is_created(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    result = await add_review_worktree("acme", session_dir, 7, "main")

    head = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=result["path"], capture_output=True, text=True,
    )
    assert head.returncode != 0, "HEAD should be detached, not on a branch"


async def test_the_merge_base_excludes_work_main_gained_afterwards(tmp_path):
    """Three-dot semantics. A pull request opened against a branch that has
    since moved must not show that branch's later commits as findings."""
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    result = await add_review_worktree("acme", session_dir, 7, "main")

    files = subprocess.run(
        ["git", "diff", "--name-only", f"{result['merge_base']}...HEAD"],
        cwd=result["path"], capture_output=True, text=True, check=True,
    ).stdout.split()
    assert files == ["feature.py"]
    assert "unrelated.py" not in files


async def test_the_head_sha_identifies_the_reviewed_commit(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    session_dir.mkdir(parents=True)

    result = await add_review_worktree("acme", session_dir, 7, "main")

    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=result["path"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert result["head_sha"] == actual
