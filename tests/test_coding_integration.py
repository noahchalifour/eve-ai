"""The full session lifecycle against the real HTTP surface, the real ACP
protocol, and a real git repo - with a STUB ACP agent rather than a model.

The stub is the point, not a compromise. It makes the protocol handshake,
the worktree, the branch, the commit, and the push assertable
deterministically, which no test driving a real model can be. Whether a
real agent reaches LiteLLM is tests/test_coding_live.py's job, and that is
the only file entitled to claim it.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

AUTH = {"Authorization": "Bearer secret"}

# A minimal ACP agent: on each prompt it commits one file into the worktree
# and ends its turn. Written to disk and spawned as a real subprocess, so
# the stdio JSON-RPC transport is exercised rather than mocked. Response
# constructors take camelCase kwargs (the wire protocol's field names) -
# verified against the installed SDK: the handler methods receive
# snake_case args but the schema objects do not.
STUB_AGENT = r'''
import asyncio, pathlib, subprocess, sys
import acp
from acp import Agent
from acp.schema import InitializeResponse, NewSessionResponse, PromptResponse


class Stub(Agent):
    def __init__(self, conn):
        self._conn = conn
        self._cwd = None
        self._n = 0

    async def initialize(self, protocol_version, **kwargs):
        return InitializeResponse(protocolVersion=acp.PROTOCOL_VERSION)

    async def new_session(self, cwd, **kwargs):
        self._cwd = cwd
        return NewSessionResponse(sessionId="stub-1")

    async def prompt(self, session_id, prompt, **kwargs):
        text = prompt[0].text
        # session.py sends the operator hint first; it is not a task.
        if not text.startswith("You are working in a git worktree"):
            self._n += 1
            tree = pathlib.Path(self._cwd) / "repo"
            (tree / ("stub-%d.txt" % self._n)).write_text(text)
            subprocess.run(["git", "add", "-A"], cwd=tree, check=True)
            subprocess.run(
                ["git", "-c", "user.email=e@x", "-c", "user.name=E",
                 "commit", "-m", "stub: " + text[:30]],
                cwd=tree, check=True,
            )
        await self._conn.session_update(
            session_id=session_id,
            update=acp.update_agent_message_text("did: " + text[:40]),
        )
        return PromptResponse(stopReason="end_turn")


# run_agent builds its own asyncio stdio streams when given none - passing
# raw sys.stdin/sys.stdout fails its StreamWriter/StreamReader transport
# check.
asyncio.run(acp.run_agent(Stub))
'''


def _run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin(tmp_path):
    bare = tmp_path / "origin" / "repo.git"
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
def box(tmp_path, origin, monkeypatch):
    """eve-computer, wired to a stub agent and a local origin."""
    stub = tmp_path / "stub_agent.py"
    stub.write_text(STUB_AGENT)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text('#!/bin/sh\necho "https://github.com/acme/repo/pull/1"\n')
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "secret")
    monkeypatch.setenv("EVE_COMPUTER_CODE_DIR", str(tmp_path / "code"))
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_GITHUB_OWNER", "acme")

    from eve_computer import app as app_mod
    from eve_computer.acp import repo, session
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    session._SESSIONS.clear()
    session._semaphore = None

    # The stub is registered by monkeypatching `build`, not by adding a
    # fourth registry entry: the registry has three entries because there
    # are three agents, and a test-only fourth in production code would be
    # exactly the plugin system the design refused.
    monkeypatch.setattr(session, "build", lambda agent, model: ([sys.executable, str(stub)], {}))
    monkeypatch.setattr(repo, "_clone_url", lambda name: str(origin))

    with TestClient(app_mod.app) as client:
        yield client

    get_computer_settings.cache_clear()
    session._SESSIONS.clear()


def _wait_for(client, session_id, status, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/sessions/{session_id}", headers=AUTH).json()
        if body["status"] == status:
            return body
        if body["status"] in ("failed", "killed"):
            pytest.fail(f"session ended as {body['status']}: {body.get('error')}")
        time.sleep(0.2)
    pytest.fail(f"session never reached {status}")


def test_a_session_runs_a_full_lifecycle(box):
    """Create, turn, idle, reply, close - ending in a real branch with real
    commits pushed to a real origin."""
    created = box.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "m",
              "repos": ["acme/repo"], "prompt": "write the first file"},
        headers=AUTH,
    )
    assert created.status_code == 202

    first = _wait_for(box, "s1", "idle")
    assert any("did: write the first file" in t["text"] for t in first["turns"])

    box.post("/sessions/s1/prompt", json={"text": "write a second file"}, headers=AUTH)
    _wait_for(box, "s1", "idle")

    # The cursor returns only what is new.
    delta = box.get(f"/sessions/s1?since={first['cursor']}", headers=AUTH).json()
    assert all("first file" not in t["text"] for t in delta["turns"])

    closed = box.post("/sessions/s1/close", headers=AUTH).json()
    assert closed["prs"][0]["commits"] == 2
    assert closed["prs"][0]["pr_url"] == "https://github.com/acme/repo/pull/1"


def test_two_sessions_share_a_repo_without_colliding(box):
    for session_id in ("s1", "s2"):
        box.post(
            "/sessions",
            json={"id": session_id, "agent": "codex", "model": "m",
                  "repos": ["acme/repo"], "prompt": f"work for {session_id}"},
            headers=AUTH,
        )

    a = _wait_for(box, "s1", "idle")
    b = _wait_for(box, "s2", "idle")

    assert a["branch"] != b["branch"]
    assert box.post("/sessions/s1/close", headers=AUTH).json()["prs"][0]["commits"] == 1
    assert box.post("/sessions/s2/close", headers=AUTH).json()["prs"][0]["commits"] == 1


def test_an_interjection_is_recorded_and_not_delivered_by_the_box(box):
    box.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "m",
              "repos": ["acme/repo"], "prompt": "go"},
        headers=AUTH,
    )
    _wait_for(box, "s1", "idle")

    box.post(
        "/sessions/s1/prompt",
        json={"text": "use httpx instead", "kind": "interjection"},
        headers=AUTH,
    )

    body = box.get("/sessions/s1", headers=AUTH).json()
    assert body["pending"] == ["use httpx instead"]
    assert body["status"] == "idle"  # the box did NOT act on it


def test_a_session_with_no_commits_produces_no_pull_request(box):
    box.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "m",
              "repos": ["acme/repo"], "prompt": "go"},
        headers=AUTH,
    )
    _wait_for(box, "s1", "idle")

    # Undo the stub's commit so the branch matches its base.
    from eve_computer.acp import repo as repo_mod

    tree = repo_mod.worktree_path(
        Path(os.environ["EVE_COMPUTER_SESSIONS_DIR"]) / "s1", "acme/repo"
    )
    _run("git", "reset", "--hard", "origin/main", cwd=tree)

    closed = box.post("/sessions/s1/close", headers=AUTH).json()

    assert closed["prs"][0] == {"repo": "acme/repo", "commits": 0, "pr_url": None}
