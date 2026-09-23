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
import json
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


def review_hint(
    repos: list[str], pr_number: int, merge_base: str, since: str = ""
) -> str:
    """The standing instruction for a review session.

    Deliberately not `_SYSTEM_HINT` with a clause bolted on: a reviewer and
    an implementer are told opposite things about editing files, and one
    string trying to say both would be the design going wrong.

    `since` is the commit an earlier review covered (EVE-32). A re-review
    focuses on what changed after it, because that is what the author just
    pushed and what the previous findings were about; the whole pull request
    stays available as context, since a fix can break something it did not
    touch.
    """
    scope = (
        f"The changes under review are `git diff {merge_base}...HEAD`.\n\n"
        if not since else
        f"This is a RE-REVIEW. Eve already reviewed this pull request at "
        f"{since}; the author has pushed since. Focus on `git diff {since}..HEAD`, "
        "which is what changed after the last review, and check whether it "
        "resolves the earlier review's findings (visible with `gh pr view "
        f"{pr_number} --comments`). The whole pull request is "
        f"`git diff {merge_base}...HEAD`; read it for context, but do not "
        "repeat findings on unchanged code that the earlier review already "
        "raised.\n\n"
    )
    return (
        "You are reviewing a pull request on behalf of an assistant named Eve. "
        f"The repositories are {', '.join(repos)} and the pull request is "
        f"#{pr_number}. {scope}"
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


_FEEDBACK_FILE = "feedback.json"


def address_hint(repos: list[str], pr_number: int, branch: str) -> str:
    """The standing instruction for an address session (EVE-31): Eve's own
    pull request has feedback, and this agent answers it.

    An implementer's hint, not a reviewer's: it edits and commits. What it
    adds is the discipline for deciding WHAT to change, which is the
    vendored receiving-code-review skill, and the contract for replying,
    which is `followup.json`.
    """
    return (
        "You are working in a git worktree on behalf of an assistant named Eve. "
        f"Eve opened pull request #{pr_number} in {', '.join(repos)} and people "
        f"have left feedback on it. You are on its branch, `{branch}`.\n\n"
        f"The feedback is in `{_FEEDBACK_FILE}` in the session root directory: "
        "every review, inline comment, and conversation comment from people "
        "Eve works for, oldest first, each with an `id`.\n\n"
        "BEFORE CHANGING ANYTHING, read and follow the receiving-code-review "
        "skill: `prompts/receiving-code-review/SKILL.md` in this repository's "
        "checkout, or /app/prompts/receiving-code-review/SKILL.md if it is not "
        "there. In that skill, 'your human partner' is the family member who "
        "asked Eve for this change, and the reviewer is whoever left the "
        "comment. Verify each item against the code, fix what is right, and "
        "push back with technical reasoning on what is wrong. No performative "
        "agreement, and do not implement a suggestion you have not verified.\n\n"
        "Some feedback may already be addressed by later commits or earlier "
        "replies; check the git log and the threads, and do not redo it.\n\n"
        "Commit your fixes on the current branch. Do not push and do not open "
        "a pull request: that is handled for you. Never use bare `git stash`.\n\n"
        "When you are done, write `followup.json` in the session root "
        "directory with this exact shape:\n"
        '{"summary": "...", "replies": [{"comment_id": 123, "body": "..."}]}\n\n'
        "`replies` answers inline review comments in their own threads: "
        "`comment_id` must be the `id` of an item whose `kind` is "
        "`review_comment`. Say what you changed, or why you did not. "
        "`summary` is posted once as a conversation comment on the pull "
        "request and covers everything else, including reviews and "
        "conversation comments. Feedback that needs no reply gets none."
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
    # EVE-32. The commit an earlier review covered, when this is a re-review
    # and that commit is still an ancestor of the head.
    since: str = ""
    # EVE-31. The feedback an address session was given, which is also the
    # set of ids its replies may name.
    feedback: list[dict] = field(default_factory=list)


_SESSIONS: dict[str, Session] = {}
_lock = asyncio.Lock()
_semaphore: asyncio.Semaphore | None = None
_review_semaphore: asyncio.Semaphore | None = None


def _limiter() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_computer_settings().max_concurrent_sessions)
    return _semaphore


def _review_limiter() -> asyncio.Semaphore:
    global _review_semaphore
    if _review_semaphore is None:
        _review_semaphore = asyncio.Semaphore(get_computer_settings().max_concurrent_reviews)
    return _review_semaphore


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
    since_sha: str | None = None,
    trusted_authors: list[str] | None = None,
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

    if kind in ("review", "address") and (pr_number is None or len(repos) != 1):
        async with _lock:
            _SESSIONS.pop(session_id, None)
        raise ValueError(f"a {kind} session needs one repository and a pull request number")

    if kind == "review":
        checkout = await repo.add_review_worktree(
            repos[0], directory, pr_number, base_ref, since_sha=since_sha
        )
        session.merge_base = checkout["merge_base"]
        session.head_sha = checkout["head_sha"]
        session.since = checkout.get("since", "")
        hint = review_hint(list(repos), pr_number, session.merge_base, session.since)
    elif kind == "address":
        checkout = await repo.add_pr_worktree(repos[0], directory, pr_number)
        session.branch = checkout["branch"]
        session.head_sha = checkout["head_sha"]
        session.feedback = await repo.fetch_feedback(
            repos[0], pr_number, checkout["path"], trusted_authors or []
        )
        (directory / _FEEDBACK_FILE).write_text(json.dumps(session.feedback, indent=2))
        hint = address_hint(list(repos), pr_number, session.branch)
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
    # An address session writes code, so it competes with coding sessions
    # for their slots rather than with reviews for theirs.
    limiter = _review_limiter() if session.kind == "review" else _limiter()
    manager = None
    try:
        async with limiter:
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
                turn_timeout = (
                    settings.review_session_timeout_seconds
                    if session.kind == "review"
                    else settings.session_turn_timeout_seconds
                )
                response = await asyncio.wait_for(
                    conn.prompt(session_id=acp_session_id, prompt=[text_block(text)]),
                    timeout=turn_timeout,
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
        "since": session.since,
    }


async def close_address(session_id: str) -> dict:
    """An address session's ending (EVE-31): validate the replies, push the
    commits, post the replies, tear down.

    The replies are read BEFORE the push. An agent that finished without
    saying what it did gets nothing pushed: unexplained commits appearing on
    a reviewer's pull request are worse than none, and the commits are not
    lost, they stay on the branch in the box's clone.
    """
    session = _SESSIONS[session_id]
    name = session.repos[0]
    tree = repo.worktree_path(session.directory, name)
    try:
        try:
            followup = review.load_followup(session.directory, session.feedback)
        except review.InvalidFindings:
            _fail(session, "the follow-up produced no usable followup.json")
            raise
        finally:
            await _stop(session)

        pushed = await repo.push_followup(session.directory, name, session.branch)
        if pushed.get("error"):
            # Do not reply "fixed" in threads when the fix never reached the
            # pull request.
            posted = {"replies": 0, "commented": False, "error": None,
                      "url": f"https://github.com/{repo._qualified(name)}/pull/{session.pr_number}"}
        else:
            posted = await repo.post_followup(
                name, session.pr_number, followup["replies"], followup["summary"], tree
            )
    finally:
        await repo.remove_worktrees(session.directory, session.repos)

    session.status = "finished"
    return {
        # The `prs` shape a coding session's close returns, so
        # `store.implementer_of` finds this session as the author of the
        # commit it pushed, and a re-review of it picks a different model.
        "prs": [pushed],
        "commits": pushed.get("commits", 0),
        "replies": posted.get("replies", 0),
        "commented": posted.get("commented", False),
        "url": posted.get("url"),
        "summary_text": followup["summary"],
        "error": pushed.get("error") or posted.get("error"),
    }


async def _stop(session: Session) -> None:
    if session.driver:
        await session.prompts.put(None)
        session.driver.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await session.driver
    with contextlib.suppress(Exception):
        await session.conn.close_session(session_id=session.id)  # type: ignore[union-attr]


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
