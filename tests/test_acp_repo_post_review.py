"""Posting is the one place an LLM's output reaches Eve's GitHub identity.

`gh` is faked via a PATH script that records its argv, the same technique
test_acp_repo.py uses: there is no GitHub in a unit test, and the code's
contract with `gh` is an argv and a request body.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from eve_computer.acp.repo import post_review


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def worktree(tmp_path):
    """A real checkout with one changed file, so `changed_lines` has real
    diff output to parse."""
    tree = tmp_path / "tree"
    tree.mkdir()
    _run("git", "init", "--initial-branch=main", ".", cwd=tree)
    _run("git", "config", "user.email", "eve@example.com", cwd=tree)
    _run("git", "config", "user.name", "Eve", cwd=tree)
    (tree / "a.py").write_text("one\ntwo\nthree\n")
    _run("git", "add", "a.py", cwd=tree)
    _run("git", "commit", "-m", "base", cwd=tree)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True, text=True,
    ).stdout.strip()
    (tree / "a.py").write_text("one\nCHANGED\nthree\n")
    _run("git", "add", "a.py", cwd=tree)
    _run("git", "commit", "-m", "change", cwd=tree)
    return tree, base


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    """A `gh` on PATH recording argv and any --input file it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "gh-argv.log"
    body_log = tmp_path / "gh-body.json"
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {argv_log}\n'
        "prev=\"\"\n"
        "for arg in \"$@\"; do\n"
        f'  if [ "$prev" = "--input" ]; then cp "$arg" {body_log}; fi\n'
        "  prev=\"$arg\"\n"
        "done\n"
        'case "$*" in\n'
        f'  *"--paginate"*) echo "[]" ;;\n'
        '  *) echo "{}" ;;\n'
        "esac\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return argv_log, body_log


def _review():
    return {
        "summary": "One finding.",
        "skipped_sections": [],
        "findings": [{
            "severity": "critical", "axis": "security",
            "file": "a.py", "line": 2, "body": "unsafe",
        }],
    }


async def test_the_review_is_posted_as_a_comment(fake_gh, worktree):
    argv_log, body_log = fake_gh
    tree, base = worktree

    await post_review("acme/repo", 7, _review(), "claude", "m", tree, base)

    body = json.loads(body_log.read_text())
    assert body["event"] == "COMMENT"


async def test_approve_is_not_reachable(fake_gh, worktree):
    """Hardcoded, not configured: no input may produce merge authority."""
    argv_log, body_log = fake_gh
    tree, base = worktree

    await post_review("acme/repo", 7, _review(), "claude", "m", tree, base)

    posted = body_log.read_text()
    assert "APPROVE" not in posted
    assert "REQUEST_CHANGES" not in posted


async def test_the_repo_and_pr_come_from_the_caller_not_the_findings(
    fake_gh, worktree
):
    """The agent must not be able to redirect a review onto another repo."""
    argv_log, body_log = fake_gh
    tree, base = worktree
    hostile = _review()
    hostile["repo"] = "someone-else/private"
    hostile["pr_number"] = 999

    await post_review("acme/repo", 7, hostile, "claude", "m", tree, base)

    argv = argv_log.read_text()
    assert "acme/repo/pulls/7/reviews" in argv
    assert "someone-else" not in argv
    assert "999" not in argv


async def test_finding_text_cannot_become_a_shell_argument(fake_gh, worktree):
    """Findings reach gh as a JSON file body, never interpolated into a
    shell string."""
    argv_log, body_log = fake_gh
    tree, base = worktree
    hostile = _review()
    hostile["findings"][0]["body"] = "; rm -rf / #"

    await post_review("acme/repo", 7, hostile, "claude", "m", tree, base)

    assert "rm -rf" not in argv_log.read_text()
    assert "rm -rf" in body_log.read_text()


async def test_one_api_call_rather_than_one_per_finding(fake_gh, worktree):
    argv_log, body_log = fake_gh
    tree, base = worktree
    many = _review()
    many["findings"] = [
        {"severity": "nit", "axis": "readability", "file": "a.py",
         "line": 2, "body": f"finding {i}"}
        for i in range(10)
    ]

    await post_review("acme/repo", 7, many, "claude", "m", tree, base)

    posts = [l for l in argv_log.read_text().splitlines() if "reviews" in l and "--paginate" not in l]
    assert len(posts) == 1


async def test_a_gh_failure_is_reported_rather_than_raised(
    tmp_path, monkeypatch, worktree
):
    """The review took real time and real tokens; losing the findings
    because the post failed would waste all of it."""
    tree, base = worktree
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "gh"
    script.write_text("#!/bin/sh\necho 'boom' >&2\nexit 1\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = await post_review("acme/repo", 7, _review(), "claude", "m", tree, base)

    assert result["posted"] is False
    assert "error" in result


async def test_an_existing_review_for_the_same_commit_is_not_duplicated(
    tmp_path, monkeypatch, worktree
):
    tree, base = worktree
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    argv_log = tmp_path / "argv.log"
    script = bin_dir / "gh"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tree, capture_output=True, text=True,
    ).stdout.strip()
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {argv_log}\n'
        'case "$*" in\n'
        f'  *"--paginate"*) echo \'[{{"commit_id": "{head}", "user": {{"login": "eve"}}}}]\' ;;\n'
        '  *) echo "{}" ;;\n'
        "esac\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    result = await post_review("acme/repo", 7, _review(), "claude", "m", tree, base)

    assert result["skipped"] is True
    posts = [l for l in argv_log.read_text().splitlines() if "--input" in l]
    assert posts == []
