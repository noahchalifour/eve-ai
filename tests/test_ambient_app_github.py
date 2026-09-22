"""The webhook route: signature, actor resolution, and the disabled path."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from eve.review import dispatch
from eve_ambient import app as app_module

SECRET = "hook-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _payload(login="chalifournoah"):
    return {
        "action": "labeled",
        "pull_request": {
            "number": 7, "head": {"sha": "abc123"}, "base": {"ref": "main"},
            "html_url": "https://github.com/acme/repo/pull/7",
        },
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": login},
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
    monkeypatch.setenv("EVE_AMBIENT_ENABLED", "true")
    monkeypatch.setenv("EVE_AMBIENT_TOKEN", "a" * 32)
    from eve.settings import get_settings

    get_settings.cache_clear()
    app_module._background.clear()
    app_module._in_flight.clear()
    yield
    get_settings.cache_clear()
    app_module._background.clear()
    app_module._in_flight.clear()


@pytest.fixture
def client():
    with TestClient(app_module.app) as test_client:
        yield test_client


def test_a_signed_webhook_is_accepted(client, monkeypatch):
    monkeypatch.setattr(
        dispatch, "start", AsyncMock(return_value="reviewing acme/repo#7 with claude/m")
    )
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 202


def test_an_unsigned_webhook_is_rejected(client):
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 401


def test_a_wrongly_signed_webhook_is_rejected(client):
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef",
                 "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 401


def test_an_unknown_actor_gets_no_review(client):
    """A valid signature proves GitHub sent it, not that the labeller may
    spend Eve's tokens."""
    body = json.dumps(_payload(login="a-stranger")).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 403


def test_a_ping_is_acknowledged_not_rejected(client):
    """Rejecting the hook-creation ping makes the hook look broken."""
    body = json.dumps({"zen": "hello"}).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "ping"},
    )

    assert response.status_code == 202


def test_an_unrelated_action_is_acknowledged_and_dropped(client, monkeypatch):
    monkeypatch.setattr(dispatch, "start", AsyncMock())
    payload = _payload()
    payload["action"] = "closed"
    body = json.dumps(payload).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 202
    dispatch.start.assert_not_awaited()


def test_review_disabled_serves_503(client, monkeypatch):
    monkeypatch.setenv("EVE_REVIEW_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 503


def test_a_labelled_pull_request_actually_calls_dispatch_start(client, monkeypatch):
    """The single most important property the Task 9 correction exists to
    guarantee: the route must actually call `dispatch.start`, not merely
    build a `Signal` and drop it into the notification pipeline, which has
    no way to act on it."""
    mock_start = AsyncMock(return_value="reviewing acme/repo#7 with claude/m")
    monkeypatch.setattr(dispatch, "start", mock_start)
    body = json.dumps(_payload()).encode()

    response = client.post(
        "/signals/github", content=body,
        headers={"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 202

    for _ in range(200):
        if mock_start.await_count:
            break
        time.sleep(0.005)

    mock_start.assert_awaited_once_with(
        repo="acme/repo",
        pr_number=7,
        head_sha="abc123",
        base_ref="main",
        member_sub="sub-noah",
    )
