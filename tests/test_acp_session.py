"""The state machine, with the agent subprocess faked at the `spawn` seam.

The point of every test here is the same: the box records what happened and
NEVER classifies it. An `idle` session with the agent asking a question and
an `idle` session with the work finished are the same state on this side of
the boundary - Eve's container is what tells them apart, because it is the
only side that knows anything about the family.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from eve_computer.acp import session as session_mod
from eve_computer.acp.session import close, create, get, kill, send, snapshot


class FakeConn:
    """Stands in for acp.ClientSideConnection."""

    def __init__(self, stop_reasons=None, reply="ok"):
        self.prompts: list[str] = []
        self.cancelled = False
        self.closed = False
        self._stop_reasons = list(stop_reasons or [])
        self._reply = reply
        self.client = None

    async def initialize(self, **kwargs):
        return type("R", (), {"agent_capabilities": None})()

    async def new_session(self, **kwargs):
        return type("R", (), {"session_id": "acp-1"})()

    async def prompt(self, session_id, prompt, **kwargs):
        text = prompt[0].text
        if text.startswith("You are working in a git worktree"):
            # The operator hint is not a task: it consumes no seeded stop
            # reason and is not recorded, exactly as the integration stub
            # (tests/test_coding_integration.py) treats it.
            return type("R", (), {"stop_reason": "end_turn"})()
        self.prompts.append(text)
        if self.client is not None:
            self.client._on_update(
                type("U", (), {"session_update": "agent_message_chunk",
                               "content": type("C", (), {"type": "text", "text": self._reply})()})()
            )
        reason = self._stop_reasons.pop(0) if self._stop_reasons else "end_turn"
        return type("R", (), {"stop_reason": reason})()

    async def cancel(self, session_id, **kwargs):
        self.cancelled = True

    async def close_session(self, session_id, **kwargs):
        self.closed = True


@pytest.fixture
def fake_spawn(monkeypatch):
    conns: list[FakeConn] = []

    class NullManager:
        """Stands in for the spawn_agent_process context manager, which
        session.py closes in its `finally`."""

        async def __aexit__(self, *exc):
            return False

    def _install(conn: FakeConn):
        async def _spawn(client, argv, env, cwd):
            conn.client = client
            conns.append(conn)
            return conn, NullManager()
        monkeypatch.setattr(session_mod, "_spawn", _spawn)
        return conn

    return _install


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("EVE_COMPUTER_SESSION_MAX_TURNS", "3")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()

    async def _add_worktree(repo, session_dir, branch):
        tree = Path(session_dir) / repo.split("/")[-1]
        tree.mkdir(parents=True, exist_ok=True)
        return tree

    monkeypatch.setattr(session_mod.repo, "add_worktree", _add_worktree)
    monkeypatch.setattr(
        session_mod.repo, "publish",
        AsyncMock(return_value=[{"repo": "acme/repo", "commits": 1, "pr_url": "u"}]),
    )
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())
    yield
    get_computer_settings.cache_clear()
    session_mod._SESSIONS.clear()


async def _settle():
    """Let the session's background task run to its next await point."""
    for _ in range(50):
        await asyncio.sleep(0)


async def test_a_finished_turn_leaves_the_session_idle_not_finished(fake_spawn):
    fake_spawn(FakeConn(reply="Which auth library do you want?"))

    await create("s1", "codex", "chatgpt/gpt-5.6-sol", ["acme/repo"], "add auth")
    await _settle()

    assert get("s1").status == "idle"


async def test_the_agents_reply_is_recorded_as_a_turn(fake_spawn):
    fake_spawn(FakeConn(reply="opened a PR"))

    await create("s1", "codex", "chatgpt/gpt-5.6-sol", ["acme/repo"], "add auth")
    await _settle()

    turns = snapshot(get("s1"), since=0)["turns"]
    assert turns[0]["role"] == "user" and turns[0]["text"] == "add auth"
    assert turns[1]["role"] == "agent" and "opened a PR" in turns[1]["text"]


async def test_the_cursor_returns_only_new_turns(fake_spawn):
    conn = fake_spawn(FakeConn())

    await create("s1", "codex", "m", ["acme/repo"], "first")
    await _settle()
    first = snapshot(get("s1"), since=0)
    await send("s1", "second")
    await _settle()

    later = snapshot(get("s1"), since=first["cursor"])
    assert [t["text"] for t in later["turns"] if t["role"] == "user"] == ["second"]
    assert conn.prompts == ["first", "second"]


async def test_activity_is_a_rolling_window_not_a_transcript(fake_spawn):
    fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()
    session = get("s1")

    for i in range(50):
        session_mod._record_activity(session, f"tool call {i}")

    assert len(session.activity) <= session_mod._ACTIVITY_MAX
    assert session.activity[-1] == "tool call 49"


async def test_a_pending_message_is_reported_and_not_auto_sent(fake_spawn):
    conn = fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()

    await session_mod.enqueue("s1", "use httpx instead")

    assert snapshot(get("s1"), since=0)["pending"] == ["use httpx instead"]
    # The BOX does not decide to deliver it - Eve composes the next prompt.
    assert conn.prompts == ["go"]


async def test_exceeding_max_turns_fails_the_session(fake_spawn):
    fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()
    for _ in range(2):
        await send("s1", "again")
        await _settle()

    await send("s1", "once more")
    await _settle()

    session = get("s1")
    assert session.status == "failed"
    assert "max turns" in session.error


async def test_a_refusal_stop_reason_fails_the_session(fake_spawn):
    fake_spawn(FakeConn(stop_reasons=["refusal"]))

    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()

    assert get("s1").status == "failed"
    assert "refusal" in get("s1").error


async def test_close_publishes_and_reports_the_prs(fake_spawn):
    conn = fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()

    result = await close("s1")

    assert result["prs"] == [{"repo": "acme/repo", "commits": 1, "pr_url": "u"}]
    assert get("s1").status == "finished"
    assert conn.closed


async def test_kill_cancels_the_agent_and_keeps_the_worktrees(fake_spawn):
    conn = fake_spawn(FakeConn())
    await create("s1", "codex", "m", ["acme/repo"], "go")
    await _settle()

    await kill("s1")
    await _settle()

    assert get("s1").status == "killed"
    assert conn.cancelled


async def test_an_unknown_agent_is_refused_at_create(fake_spawn):
    from eve_computer.acp.registry import UnknownAgent

    with pytest.raises(UnknownAgent):
        await create("s1", "cursor", "m", ["acme/repo"], "go")


async def test_the_branch_name_is_shared_across_every_repo(fake_spawn):
    fake_spawn(FakeConn())

    await create("s1", "codex", "m", ["acme/one", "acme/two"], "cross-repo change")
    await _settle()

    session = get("s1")
    assert session.branch.startswith("eve/cross-repo-change-")
    assert session.repos == ["acme/one", "acme/two"]
