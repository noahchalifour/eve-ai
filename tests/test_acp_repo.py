"""Real git against a real temporary origin. Mocking git here would test
the mock: worktree semantics, `origin/HEAD`, and "did this branch actually
gain commits" are exactly the parts that would break in production while a
mocked test stayed green.

`gh` is the one thing faked, via a script on PATH - there is no GitHub in a
unit test, and the code's contract with `gh` is one line of stdout.
"""

import os
import subprocess
from pathlib import Path

import pytest

from eve_computer.acp import repo as repo_mod
from eve_computer.acp.repo import (
    GitError,
    add_worktree,
    ensure_clone,
    publish,
    remove_worktrees,
    slug,
)


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin(tmp_path):
    """A bare origin with one commit on `main`."""
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
    return bare


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A `gh` on PATH that prints a PR URL and records its argv."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {log}\n'
        'echo "https://github.com/acme/repo/pull/1"\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


@pytest.fixture(autouse=True)
def _settings(tmp_path, origin, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_CODE_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_GITHUB_OWNER", "acme")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    # `ensure_clone` builds an https URL from the repo name; point it at the
    # temporary bare repo instead so no network is involved.
    monkeypatch.setattr(repo_mod, "_clone_url", lambda name: str(origin))
    yield
    get_computer_settings.cache_clear()


def test_slug_is_branch_safe():
    assert slug("Fix the CalDAV client's 500!") == "fix-the-caldav-client-s-500"
    assert len(slug("x " * 200)) <= 40


async def test_ensure_clone_creates_the_clone_once_and_then_fetches(tmp_path):
    first = await ensure_clone("acme/repo")
    assert (first / ".git").exists()
    second = await ensure_clone("acme/repo")
    assert first == second


async def test_add_worktree_checks_out_a_new_branch_under_the_session(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    tree = await add_worktree("acme/repo", session_dir, "eve/fix-1")

    assert tree == session_dir / "repo"
    assert (tree / "README.md").read_text() == "hello\n"
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=tree, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == "eve/fix-1"


async def test_two_sessions_get_independent_worktrees_of_one_repo(tmp_path):
    a = await add_worktree("acme/repo", tmp_path / "sessions" / "a", "eve/a-1")
    b = await add_worktree("acme/repo", tmp_path / "sessions" / "b", "eve/b-1")

    (a / "only-a.txt").write_text("a")
    assert not (b / "only-a.txt").exists()


async def test_publish_opens_a_pr_for_a_repo_with_commits(tmp_path, fake_gh):
    session_dir = tmp_path / "sessions" / "s1"
    tree = await add_worktree("acme/repo", session_dir, "eve/fix-1")
    (tree / "new.txt").write_text("x")
    _run("git", "add", "new.txt", cwd=tree)
    _run("git", "-c", "user.email=e@x", "-c", "user.name=E", "commit", "-m", "add new", cwd=tree)

    results = await publish(session_dir, ["acme/repo"], "eve/fix-1")

    assert results == [
        {"repo": "acme/repo", "commits": 1, "pr_url": "https://github.com/acme/repo/pull/1"}
    ]
    assert "pr create" in fake_gh.read_text()


async def test_publish_opens_no_pr_for_a_repo_with_no_commits(tmp_path, fake_gh):
    session_dir = tmp_path / "sessions" / "s1"
    await add_worktree("acme/repo", session_dir, "eve/fix-1")

    results = await publish(session_dir, ["acme/repo"], "eve/fix-1")

    assert results == [{"repo": "acme/repo", "commits": 0, "pr_url": None}]
    assert not fake_gh.exists()


async def test_publish_reports_a_failed_pr_without_losing_the_other_repos(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions" / "s1"
    tree = await add_worktree("acme/repo", session_dir, "eve/fix-1")
    (tree / "new.txt").write_text("x")
    _run("git", "add", "new.txt", cwd=tree)
    _run("git", "-c", "user.email=e@x", "-c", "user.name=E", "commit", "-m", "add new", cwd=tree)
    # A `gh` that exits non-zero: the push succeeds, the PR does not. (A
    # PATH wipe here would also break `git` itself, which is not the
    # failure this test is about.)
    fail_gh = tmp_path / "bin-fail"
    fail_gh.mkdir()
    (fail_gh / "gh").write_text("#!/bin/sh\nexit 1\n")
    (fail_gh / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fail_gh}{os.pathsep}{os.environ['PATH']}")

    results = await publish(session_dir, ["acme/repo"], "eve/fix-1")

    assert results[0]["commits"] == 1
    assert results[0]["pr_url"] is None
    assert "error" in results[0]


async def test_remove_worktrees_leaves_the_branch_behind(tmp_path):
    session_dir = tmp_path / "sessions" / "s1"
    tree = await add_worktree("acme/repo", session_dir, "eve/fix-1")

    await remove_worktrees(session_dir, ["acme/repo"])

    assert not tree.exists()
    clone = await ensure_clone("acme/repo")
    branches = subprocess.run(
        ["git", "branch", "--list", "eve/fix-1"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout
    assert "eve/fix-1" in branches


async def test_a_failing_git_command_raises_with_its_stderr(tmp_path):
    with pytest.raises(GitError) as excinfo:
        await add_worktree("acme/repo", tmp_path / "sessions" / "s1", "refs/heads/")
    assert "git" in str(excinfo.value)
