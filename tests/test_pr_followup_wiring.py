"""The Eve-side wiring for EVE-31 and EVE-32: the debouncer, the webhook
parsers, the webhook route, the supervisor's address branch, and how an
address session is reported."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from eve.coding import supervisor
from eve_ambient import app as app_module
from eve_ambient.debounce import Debouncer
from eve_ambient.sources import coding, github

SECRET = "hook-secret"


# --- the debouncer --------------------------------------------------------


async def test_a_burst_runs_once_with_the_newest_action():
    debouncer = Debouncer()
    ran: list[int] = []

    for n in range(5):
        async def action(n=n):
            ran.append(n)
        debouncer.schedule("pr-7", 0.02, action)
    await asyncio.sleep(0.08)

    assert ran == [4]
    assert "pr-7" not in debouncer


async def test_keys_are_independent():
    debouncer = Debouncer()
    ran: list[str] = []
    for key in ("pr-7", "pr-8"):
        async def action(key=key):
            ran.append(key)
        debouncer.schedule(key, 0.01, action)
    await asyncio.sleep(0.05)

    assert sorted(ran) == ["pr-7", "pr-8"]


async def test_a_failing_action_does_not_escape():
    debouncer = Debouncer()

    async def boom():
        raise RuntimeError("no")

    debouncer.schedule("k", 0, boom)
    await asyncio.sleep(0.01)
    assert len(debouncer) == 0


async def test_cancel_all_drops_pending_actions():
    debouncer = Debouncer()
    ran = AsyncMock()
    debouncer.schedule("k", 0.05, ran)

    await debouncer.cancel_all()
    await asyncio.sleep(0.08)

    ran.assert_not_awaited()


# --- parsers ----------------------------------------------------------------


def _synchronize():
    return {
        "action": "synchronize",
        "pull_request": {"number": 7, "head": {"sha": "new"}, "base": {"ref": "main"}},
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": "someone"},
    }


def test_synchronize_names_the_new_head():
    assert github.from_synchronize(_synchronize()) == {
        "repo": "acme/repo", "pr_number": 7, "head_sha": "new", "base_ref": "main",
    }


def test_labeled_is_not_a_synchronize():
    with pytest.raises(ValueError):
        github.from_synchronize({**_synchronize(), "action": "labeled"})


def _review(login="chalifournoah"):
    return {
        "action": "submitted",
        "review": {"user": {"login": login}, "state": "changes_requested"},
        "pull_request": {"number": 7, "html_url": "https://github.com/acme/repo/pull/7"},
        "repository": {"full_name": "acme/repo"},
    }


def test_a_review_is_feedback():
    feedback = github.feedback_from_webhook("pull_request_review", _review())

    assert feedback["author"] == "chalifournoah"
    assert feedback["pr_url"] == "https://github.com/acme/repo/pull/7"


def test_a_comment_on_an_issue_is_not_pr_feedback():
    payload = {
        "action": "created",
        "issue": {"number": 3, "html_url": "https://github.com/acme/repo/issues/3"},
        "comment": {"user": {"login": "chalifournoah"}},
        "repository": {"full_name": "acme/repo"},
    }
    with pytest.raises(ValueError):
        github.feedback_from_webhook("issue_comment", payload)


def test_a_pr_conversation_comment_is_feedback():
    payload = {
        "action": "created",
        "issue": {"number": 7, "pull_request": {
            "html_url": "https://github.com/acme/repo/pull/7"}},
        "comment": {"user": {"login": "chalifournoah"}},
        "repository": {"full_name": "acme/repo"},
    }
    assert github.feedback_from_webhook("issue_comment", payload)["pr_number"] == 7


def test_an_edited_comment_is_not_new_feedback():
    with pytest.raises(ValueError):
        github.feedback_from_webhook("pull_request_review", {**_review(), "action": "edited"})


# --- the route --------------------------------------------------------------


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(tmp_path, monkeypatch):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: ['code.review', 'code.delegate']\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(roster))
    monkeypatch.setenv("EVE_REVIEW_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("EVE_REVIEW_REPOS", '["acme/repo"]')
    monkeypatch.setenv("EVE_PR_FOLLOWUP_ENABLED", "true")
    monkeypatch.setenv("EVE_GITHUB_LOGIN", "eve-bot")
    monkeypatch.setenv("EVE_AMBIENT_TOKEN", "a" * 32)
    from eve.family import get_family
    from eve.settings import get_settings

    get_settings.cache_clear()
    get_family.cache_clear()
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        app_module._debounce, "schedule",
        lambda key, delay, action: scheduled.append((key, delay, action)),
    )
    with TestClient(app_module.app) as test_client:
        test_client.scheduled = scheduled
        yield test_client
    get_settings.cache_clear()
    get_family.cache_clear()


def _post(client, event, payload):
    body = json.dumps(payload).encode()
    return client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": event},
    )


def test_a_push_schedules_a_debounced_rereview(client):
    response = _post(client, "pull_request", _synchronize())

    assert response.status_code == 202
    assert response.json()["accepted"] == "acme/repo#7@new"
    key, delay, _action = client.scheduled[0]
    assert key == ("review", "acme/repo", 7)
    assert delay == 300


def test_a_push_to_an_unlisted_repo_schedules_nothing(client):
    payload = {**_synchronize(), "repository": {"full_name": "other/repo"}}

    assert _post(client, "pull_request", payload).json()["accepted"] is None
    assert client.scheduled == []


def test_the_scheduled_rereview_asks_dispatch(client, monkeypatch):
    from eve.review import dispatch

    restart = AsyncMock(return_value="reviewing")
    monkeypatch.setattr(dispatch, "restart_on_push", restart)
    _post(client, "pull_request", _synchronize())

    asyncio.run(client.scheduled[0][2]())

    assert restart.await_args.kwargs["head_sha"] == "new"


def test_a_members_review_schedules_a_follow_up(client, monkeypatch):
    from eve.coding import followup

    start = AsyncMock(return_value="addressing")
    monkeypatch.setattr(followup, "start", start)

    response = _post(client, "pull_request_review", _review())

    assert response.json()["accepted"] == "acme/repo#7"
    key, delay, action = client.scheduled[0]
    assert key == ("followup", "acme/repo", 7)
    assert delay == 180
    asyncio.run(action())
    start.assert_awaited_once_with("acme/repo", 7, "https://github.com/acme/repo/pull/7")


@pytest.mark.parametrize("login", ["eve-bot", "a-stranger"])
def test_eves_own_and_strangers_feedback_schedules_nothing(client, login):
    assert _post(client, "pull_request_review", _review(login)).json()["accepted"] is None
    assert client.scheduled == []


def test_follow_ups_are_off_by_default(client, monkeypatch):
    monkeypatch.setenv("EVE_PR_FOLLOWUP_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    assert _post(client, "pull_request_review", _review()).json()["accepted"] is None


# --- supervisor and reporting ---------------------------------------------


def _row(kind="address"):
    return {
        "id": "s1", "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "address feedback on acme/repo#7", "repos": ["acme/repo"],
        "context": "", "status": "running", "cursor": 0, "kind": kind,
        "pr_number": 7, "head_sha": None, "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC), "supervisor_turns": 0,
    }


@pytest.fixture
def supervised(monkeypatch):
    monkeypatch.setattr(supervisor.store, "advance_cursor", AsyncMock())
    monkeypatch.setattr(supervisor.store, "bump_supervisor_turns", AsyncMock(return_value=1))
    monkeypatch.setattr(supervisor.store, "mark_resolved", AsyncMock())
    monkeypatch.setattr(supervisor, "close_coding_session", AsyncMock())
    monkeypatch.setattr(supervisor, "get_coding_session", AsyncMock(return_value={
        "status": "idle", "cursor": 1, "pending": [],
        "turns": [{"role": "agent", "text": "followup.json written"}],
    }))
    monkeypatch.setattr(supervisor, "decide", AsyncMock(
        return_value=supervisor.Decision(action="done", text="addressed")
    ))
    return type("S", (), {
        "coding_session_timeout_seconds": 99999, "coding_max_supervisor_turns": 30,
    })()


async def test_a_finished_address_session_closes_as_an_address(monkeypatch, supervised):
    closed = AsyncMock(return_value={"commits": 1, "replies": 2, "url": "u"})
    monkeypatch.setattr(supervisor, "close_address_session", closed)

    result = await supervisor._advance(
        _row(), datetime.now(UTC), supervisor.timedelta(minutes=120), supervised
    )

    closed.assert_awaited_once_with("s1")
    supervisor.close_coding_session.assert_not_awaited()
    assert result["status"] == "finished"
    assert result["result"]["replies"] == 2


async def test_an_address_session_that_could_not_close_fails(monkeypatch, supervised):
    monkeypatch.setattr(supervisor, "close_address_session", AsyncMock(return_value=None))

    result = await supervisor._advance(
        _row(), datetime.now(UTC), supervisor.timedelta(minutes=120), supervised
    )

    assert result["status"] == "failed"


def test_the_address_prompt_defines_done_and_protects_push_back():
    prompt = supervisor.system_prompt_for("address")

    assert "followup.json" in prompt
    assert "wrong" in prompt
    assert supervisor.system_prompt_for("code") == supervisor._SYSTEM


async def test_an_address_session_is_reported_on_the_original_thread(monkeypatch):
    row = {**_row(), "status": "finished", "finished_at": datetime.now(UTC),
           "result": {"commits": 2, "replies": 3, "url": "https://github.com/acme/repo/pull/7"}}
    monkeypatch.setattr(coding.supervisor, "tick", AsyncMock(return_value=[row]))
    monkeypatch.setattr(
        coding.coding_store, "recently_resolved_sessions", AsyncMock(return_value=[])
    )

    signals = await coding.poll("")

    assert signals[0].payload["thread_id"] == "t1"
    assert "pushed 2 commit(s)" in signals[0].summary
    assert "3 thread(s)" in signals[0].summary
    assert "Pull requests" not in signals[0].summary
