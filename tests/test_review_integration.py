"""Webhook to posted review, with only GitHub and the ACP subprocess faked.

Marked `integration`: it needs the real Postgres from
docker-compose.test.yml for the session row and the idempotence check.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from eve_ambient import app as app_module

pytestmark = pytest.mark.integration

SECRET = "hook-secret"


async def _stub_poll_forever() -> None:
    """The GitHub route under test never touches `poll_once`/`_poll_forever`
    (Task 9's correction: it calls `dispatch.start` directly), so the real
    ambient poll loop only adds noise here - and, with `EVE_AMBIENT_ENABLED`
    required to be true for the settings validator this app also runs
    under, starting it for real means a live `eve.memory.db` connection
    pool whose maintenance workers `TestClient`'s shutdown then has no clean
    way to join (the same reason `test_ambient_app.py`'s
    `test_the_webhook_accepts_and_queues_when_ambient_is_enabled` stubs it).
    """
    await asyncio.sleep(3600)


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload():
    return {
        "action": "labeled",
        "pull_request": {
            "number": 7, "head": {"sha": "abc123"}, "base": {"ref": "main"},
            "html_url": "https://github.com/acme/repo/pull/7",
        },
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": "chalifournoah"},
        "label": {"name": "eve-review"},
    }


@pytest.fixture(autouse=True)
def _settings(tmp_path, monkeypatch):
    roster = tmp_path / "family.yaml"
    roster.write_text(
        "members:\n"
        "  - sub: 'sub-noah'\n"
        "    name: 'Noah'\n"
        "    role: adult\n"
        "    timezone: 'America/Vancouver'\n"
        "    github_login: 'chalifournoah'\n"
        "    permissions: ['code.review']\n"
    )
    monkeypatch.setenv("EVE_FAMILY_FILE", str(roster))
    monkeypatch.setenv("EVE_REVIEW_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("EVE_REVIEW_REPOS", '["acme/repo"]')
    monkeypatch.setenv("EVE_AMBIENT_ENABLED", "true")
    # Required whenever EVE_AMBIENT_ENABLED=true (Settings validates length
    # >= 32); test_ambient_app_github.py's fixture sets the same thing.
    monkeypatch.setenv("EVE_AMBIENT_TOKEN", "a" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_a_labelled_pull_request_reaches_dispatch(monkeypatch):
    """The whole trigger path: signature, actor resolution, gate chain,
    dispatch. The box is the only thing faked."""
    from eve.review import dispatch

    started = AsyncMock(return_value="reviewing acme/repo#7 with claude/m")
    monkeypatch.setattr(dispatch, "start", started)
    monkeypatch.setattr(app_module, "_poll_forever", _stub_poll_forever)

    body = json.dumps(_payload()).encode()
    with TestClient(app_module.app) as client:
        response = client.post(
            "/signals/github", content=body,
            headers={"X-Hub-Signature-256": _sign(body),
                     "X-GitHub-Event": "pull_request"},
        )

        assert response.status_code == 202

        # `client` runs its ASGI app on a persistent background thread with
        # its own event loop; the route schedules `dispatch.start` via
        # `asyncio.create_task` and returns 202 immediately without awaiting
        # it, so the background task needs a chance to actually run before
        # we can assert on it - and it must be given that chance while the
        # TestClient (and its event loop) is still open, matching
        # test_ambient_app_github.py's `test_a_labelled_pull_request_
        # actually_calls_dispatch_start`.
        for _ in range(200):
            if started.await_count:
                break
            time.sleep(0.005)

        started.assert_awaited_once_with(
            repo="acme/repo", pr_number=7, head_sha="abc123",
            base_ref="main", member_sub="sub-noah",
        )


async def test_the_same_commit_labelled_twice_is_reviewed_once(monkeypatch):
    from eve.review import dispatch

    started = AsyncMock(return_value="ok")
    monkeypatch.setattr(dispatch, "start", started)
    monkeypatch.setattr(app_module, "_poll_forever", _stub_poll_forever)

    body = json.dumps(_payload()).encode()
    headers = {"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"}
    with TestClient(app_module.app) as client:
        client.post("/signals/github", content=body, headers=headers)
        # The route's `_in_flight` dedup check runs synchronously, before the
        # background task is even created, so the second identical webhook
        # is dropped at that check and `dispatch.start` is scheduled at most
        # once - true even before either background task has had a chance to
        # run.
        client.post("/signals/github", content=body, headers=headers)

        for _ in range(200):
            if started.await_count:
                break
            time.sleep(0.005)

        assert started.await_count <= 1
