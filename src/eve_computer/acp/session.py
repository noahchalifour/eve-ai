"""One live ACP session: subprocess, worktrees, turn log, bounds.

THE ONE RULE IN THIS FILE. A turn that ends is `idle`, never `finished`.
The agent's last message might be "done, opened a PR", might be "which auth
library?", might be a stall - and ACP gives no signal distinguishing them,
because `session/prompt` returns a `stop_reason` and nothing else. Deciding
which it is needs the goal, the thread, the member, and the household, none
of which exist on this box and none of which are going to. So the box
records and Eve classifies. Anything that adds judgement to this file is
the design going wrong.

WHY THE CONNECTION LIVES IN A BACKGROUND TASK. `acp.spawn_agent_process` is
an async context manager that closes the connection on exit, so the session
has to hold that context open for its whole life - which can be hours,
across many HTTP requests. `_drive` is that holder: it owns the context,
consumes prompts off a queue, and only returns when the session ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import acp
from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.schema import ClientCapabilities, FileSystemCapabilities, Implementation

from eve_computer.acp import repo
from eve_computer.acp.client import SessionClient
from eve_computer.acp.registry import build
from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

# Enough for "what is it doing right now", not a second transcript.
_ACTIVITY_MAX = 20

_SYSTEM_HINT = (
    "You are working in a git worktree on behalf of an assistant named Eve, "
    "who is relaying a request from a family member. Commit your work on the "
    "current branch; do not push and do not open a pull request - that is "
    "handled for you. Never use bare `git stash`: several worktrees share one "
    "stash stack here and you would pop someone else's work. Make a WIP commit "
    "on your own branch instead."
)


@dataclass
class Turn:
    role: str  # "user" | "agent"
    text: str
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Session:
    id: str
    agent: str
    model: str
    repos: list[str]
    branch: str
    directory: Path
    status: str = "queued"  # queued -> running -> idle -> finished|failed|killed
    turns: list[Turn] = field(default_factory=list)
    activity: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    error: str = ""
    prs: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    prompts: asyncio.Queue = field(default_factory=asyncio.Queue)
    driver: asyncio.Task | None = None
    conn: object | None = None


_SESSIONS: dict[str, Session] = {}
_lock = asyncio.Lock()
_semaphore: asyncio.Semaphore | None = None


def _limiter() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_computer_settings().max_concurrent_sessions)
    return _semaphore


def _record_activity(session: Session, line: str) -> None:
    session.activity.append(line)
    del session.activity[:-_ACTIVITY_MAX]


def _describe(update: object) -> str | None:
    """One human line for the rolling activity window, or None to ignore."""
    kind = getattr(update, "session_update", None)
    if kind == "tool_call":
        return f"tool: {getattr(update, 'title', '')}"
    if kind == "tool_call_update":
        return f"tool: {getattr(update, 'title', '') or ''} ({getattr(update, 'status', '')})"
    if kind == "agent_message_chunk":
        content = getattr(update, "content", None)
        text = getattr(content, "text", None)
        return f"saying: {text[:80]}" if text else None
    return None


def _chunk_text(update: object) -> str | None:
    if getattr(update, "session_update", None) != "agent_message_chunk":
        return None
    return getattr(getattr(update, "content", None), "text", None)


async def _spawn(client: SessionClient, argv: list[str], env: dict[str, str], cwd: Path):
    """Seam. Returns (connection, closer). Faked wholesale in unit tests -
    a real ACP subprocess in a unit test would be an integration test
    wearing the wrong marker."""
    manager = spawn_agent_process(client, argv[0], *argv[1:], env=env, cwd=str(cwd))
    conn, _process = await manager.__aenter__()
    return conn, manager


async def create(
    session_id: str, agent: str, model: str, repos: list[str], prompt: str
) -> Session:
    settings = get_computer_settings()
    # Raises UnknownAgent before anything is created, so a bad agent name
    # never leaves a half-built session or an orphaned worktree behind.
    argv, env = build(agent, model)

    branch = f"eve/{repo.slug(prompt)}-{uuid.uuid4().hex[:8]}"
    directory = Path(settings.sessions_dir) / session_id
    directory.mkdir(parents=True, exist_ok=True)

    session = Session(
        id=session_id, agent=agent, model=model, repos=list(repos),
        branch=branch, directory=directory,
    )
    async with _lock:
        _SESSIONS[session_id] = session

    for name in repos:
        await repo.add_worktree(name, directory, branch)

    await session.prompts.put(prompt)
    session.turns.append(Turn(role="user", text=prompt))
    session.driver = asyncio.create_task(_drive(session, argv, env))
    return session


async def _drive(session: Session, argv: list[str], env: dict[str, str]) -> None:
    settings = get_computer_settings()
    manager = None
    try:
        async with _limiter():
            client = SessionClient(
                root=session.directory,
                on_update=lambda update: _on_update(session, update),
            )
            conn, manager = await _spawn(client, argv, env, session.directory)
            session.conn = conn

            await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(
                    fs=FileSystemCapabilities(read_text_file=True, write_text_file=True)
                ),
                client_info=Implementation(name="eve-computer", version="1"),
            )
            created = await conn.new_session(
                cwd=str(session.directory),
                additional_directories=[
                    str(repo.worktree_path(session.directory, name))
                    for name in session.repos
                ],
            )
            acp_session_id = created.session_id
            await conn.prompt(
                session_id=acp_session_id, prompt=[text_block(_SYSTEM_HINT)]
            )

            turns = 0
            while True:
                text = await session.prompts.get()
                if text is None:
                    return
                turns += 1
                if turns > settings.session_max_turns:
                    _fail(session, f"max turns ({settings.session_max_turns}) exceeded")
                    return
                session.status = "running"
                session._chunks = []  # type: ignore[attr-defined]
                response = await asyncio.wait_for(
                    conn.prompt(session_id=acp_session_id, prompt=[text_block(text)]),
                    timeout=settings.session_turn_timeout_seconds,
                )
                reply = "".join(getattr(session, "_chunks", []))
                if reply:
                    session.turns.append(Turn(role="agent", text=reply))
                if response.stop_reason in ("refusal", "max_tokens", "max_turn_requests"):
                    _fail(session, f"agent stopped: {response.stop_reason}")
                    return
                if response.stop_reason == "cancelled":
                    session.status = "killed"
                    return
                session.status = "idle"
    except asyncio.CancelledError:
        session.status = "killed"
        raise
    except TimeoutError:
        _fail(session, f"turn exceeded {get_computer_settings().session_turn_timeout_seconds}s")
    except Exception as exc:
        logger.warning("session %s failed", session.id, exc_info=True)
        _fail(session, f"{exc.__class__.__name__}: {exc}")
    finally:
        if manager is not None:
            with contextlib.suppress(Exception):
                await manager.__aexit__(None, None, None)


def _on_update(session: Session, update: object) -> None:
    line = _describe(update)
    if line:
        _record_activity(session, line)
    chunk = _chunk_text(update)
    if chunk:
        session._chunks = [*getattr(session, "_chunks", []), chunk]  # type: ignore[attr-defined]


def _fail(session: Session, message: str) -> None:
    session.status = "failed"
    session.error = message


def get(session_id: str) -> Session | None:
    return _SESSIONS.get(session_id)


async def send(session_id: str, text: str) -> None:
    session = _SESSIONS[session_id]
    session.turns.append(Turn(role="user", text=text))
    session.pending.clear()
    await session.prompts.put(text)


async def enqueue(session_id: str, message: str) -> None:
    """A family member's interjection. Recorded, never delivered by the box:
    Eve composes the next prompt so a correction and the agent's own open
    question are answered together instead of racing."""
    _SESSIONS[session_id].pending.append(message)


async def close(session_id: str) -> dict:
    session = _SESSIONS[session_id]
    session.prs = await repo.publish(session.directory, session.repos, session.branch)
    if session.driver:
        await session.prompts.put(None)
        session.driver.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await session.driver
    with contextlib.suppress(Exception):
        await session.conn.close_session(session_id=session_id)  # type: ignore[union-attr]
    await repo.remove_worktrees(session.directory, session.repos)
    session.status = "finished"
    return {"prs": session.prs}


async def kill(session_id: str) -> None:
    session = _SESSIONS[session_id]
    with contextlib.suppress(Exception):
        await session.conn.cancel(session_id=session_id)  # type: ignore[union-attr]
    if session.driver:
        session.driver.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await session.driver
    session.status = "killed"


def snapshot(session: Session, since: int = 0) -> dict:
    return {
        "status": session.status,
        "agent": session.agent,
        "model": session.model,
        "repos": session.repos,
        "branch": session.branch,
        "activity": list(session.activity),
        "turns": [
            {"role": t.role, "text": t.text, "at": t.at.isoformat()}
            for t in session.turns[since:]
        ],
        "cursor": len(session.turns),
        "pending": list(session.pending),
        "error": session.error,
        "prs": session.prs,
    }
