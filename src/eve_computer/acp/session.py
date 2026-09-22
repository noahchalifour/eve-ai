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

from eve_computer.acp import repo, review
from eve_computer.acp.client import SessionClient
from eve_computer.acp.registry import build
from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

# Enough for "what is it doing right now", not a second transcript.
_ACTIVITY_MAX = 20

# JSON-RPC's own code. An agent that refuses an optional field answers with
# this, and the distinction from a transport failure is what makes retrying
# safe rather than a blind second attempt at anything that went wrong.
_INVALID_PARAMS = -32602

_SYSTEM_HINT = (
    "You are working in a git worktree on behalf of an assistant named Eve, "
    "who is relaying a request from a family member. Commit your work on the "
    "current branch; do not push and do not open a pull request - that is "
    "handled for you. Never use bare `git stash`: several worktrees share one "
    "stash stack here and you would pop someone else's work. Make a WIP commit "
    "on your own branch instead."
)


def review_hint(repos: list[str], pr_number: int, merge_base: str) -> str:
    """The standing instruction for a review session.

    Deliberately not `_SYSTEM_HINT` with a clause bolted on: a reviewer and
    an implementer are told opposite things about editing files, and one
    string trying to say both would be the design going wrong.
    """
    return (
        "You are reviewing a pull request on behalf of an assistant named Eve. "
        f"The repositories are {', '.join(repos)} and the pull request is "
        f"#{pr_number}. The changes under review are `git diff {merge_base}...HEAD`.\n\n"
        "You are REVIEWING, not fixing. Do not edit, create, or delete any "
        "source file, and do not commit anything.\n\n"
        "The review rubric is in `prompts/code-review/` in this repository's "
        "checkout, or at /app/prompts/code-review/ if it is not: SKILL.md is "
        "the five axes and the severity vocabulary, security-checklist.md and "
        "performance-checklist.md are the detailed axes.\n\n"
        "Apply ONLY the rubric sections that match this repository's stack. A "
        "Python service gets no Core Web Vitals findings. Record every section "
        "you skipped, and why, in `skipped_sections`.\n\n"
        "When you are done, write your findings to `review.json` in the "
        "session root directory, with this exact shape:\n"
        '{"summary": "...", "skipped_sections": ["..."], "findings": '
        '[{"severity": "critical|required|optional|nit|fyi", '
        '"axis": "correctness|readability|architecture|security|performance", '
        '"file": "path/from/repo/root.py", "line": 12, "body": "..."}]}\n\n'
        "`line` must be a line the diff actually touches on the right-hand "
        "side, or the finding cannot be anchored. Clean code is a legitimate "
        "outcome: an empty findings list is a valid review."
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
    # EVE-27. `kind` is what `close` branches on; the rest are the pull
    # request under review and the two commits that bound its diff.
    kind: str = "code"
    pr_number: int | None = None
    merge_base: str = ""
    head_sha: str = ""


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


async def _new_session(conn, session: Session):
    """`session/new`, with the extra roots as a preference rather than a
    requirement.

    `additionalDirectories` is optional in ACP v1 and the DeepSeek harness
    (EVE-24) refuses it outright. Declining it costs the agent nothing that
    matters here: every worktree is created UNDER the session directory
    that is already the cwd, so the second attempt reaches the same files
    by a shorter path. Failing the session over a field the protocol calls
    optional would be this box deciding which compliant agents it will
    talk to.
    """
    extra = [
        str(repo.worktree_path(session.directory, name)) for name in session.repos
    ]
    try:
        return await conn.new_session(
            cwd=str(session.directory), additional_directories=extra
        )
    except acp.RequestError as exc:
        if exc.code != _INVALID_PARAMS:
            raise
        logger.info(
            "%s refused additional directories; retrying with the session root alone",
            session.agent,
        )
        return await conn.new_session(cwd=str(session.directory))


async def create(
    session_id: str,
    agent: str,
    model: str,
    repos: list[str],
    prompt: str,
    kind: str = "code",
    pr_number: int | None = None,
    base_ref: str = "main",
) -> Session:
    settings = get_computer_settings()
    # Raises UnknownAgent before anything is created, so a bad agent name
    # never leaves a half-built session or an orphaned worktree behind.
    argv, env = build(agent, model)

    branch = "" if kind == "review" else f"eve/{repo.slug(prompt)}-{uuid.uuid4().hex[:8]}"
    directory = Path(settings.sessions_dir) / session_id
    directory.mkdir(parents=True, exist_ok=True)

    session = Session(
        id=session_id, agent=agent, model=model, repos=list(repos),
        branch=branch, directory=directory, kind=kind, pr_number=pr_number,
    )
    async with _lock:
        _SESSIONS[session_id] = session

    if kind == "review":
        if pr_number is None:
            raise ValueError("a review session needs a pull request number")
        for name in repos:
            checkout = await repo.add_review_worktree(
                name, directory, pr_number, base_ref
            )
            # One pull request lives in one repository, so the last (and
            # only) checkout's commits are the session's.
            session.merge_base = checkout["merge_base"]
            session.head_sha = checkout["head_sha"]
        hint = review_hint(list(repos), pr_number, session.merge_base)
    else:
        for name in repos:
            await repo.add_worktree(name, directory, branch)
        hint = _SYSTEM_HINT

    await session.prompts.put(prompt)
    session.turns.append(Turn(role="user", text=prompt))
    session.driver = asyncio.create_task(_drive(session, argv, env, hint))
    return session


async def _drive(
    session: Session, argv: list[str], env: dict[str, str], hint: str = _SYSTEM_HINT
) -> None:
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
            acp_session_id = (await _new_session(conn, session)).session_id
            await conn.prompt(
                session_id=acp_session_id, prompt=[text_block(hint)]
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


async def close_review(session_id: str) -> dict:
    """A review's ending: read the findings, post them, tear down.

    Raises `InvalidFindings` rather than degrading to an empty review. A
    review that silently becomes "no findings" is worse than no review,
    because it looks like one.

    `post_review` needs the worktree still on disk (it computes
    `changed_lines` from it), so `remove_worktrees` runs in the OUTER
    `finally` - after the post, not before it - while still firing on the
    missing-findings path, where there is no post to wait for.
    """
    session = _SESSIONS[session_id]
    try:
        try:
            findings = review.load(session.directory)
        except review.InvalidFindings:
            _fail(session, "the review produced no usable review.json")
            raise
        finally:
            if session.driver:
                await session.prompts.put(None)
                session.driver.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await session.driver
            with contextlib.suppress(Exception):
                await session.conn.close_session(session_id=session_id)  # type: ignore[union-attr]

        posted = await repo.post_review(
            session.repos[0],
            session.pr_number,
            findings,
            session.agent,
            session.model,
            repo.worktree_path(session.directory, session.repos[0]),
            session.merge_base,
        )
    finally:
        await repo.remove_worktrees(session.directory, session.repos)

    session.status = "finished"
    return {
        "posted": posted.get("posted", False),
        "skipped": posted.get("skipped", False),
        "error": posted.get("error"),
        "url": posted.get("url"),
        "findings": len(findings["findings"]),
        "counts": _severity_counts(findings),
        "head_sha": session.head_sha,
    }


def _severity_counts(findings: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings["findings"]:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    return counts


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
