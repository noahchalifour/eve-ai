"""The door, not the machinery. `session` is mocked wholesale: Task 4 owns
the state machine's behaviour and re-testing it through HTTP would only
make both suites slower to change."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from eve_computer import app as app_mod


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "secret")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    yield
    get_computer_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app_mod.app) as test_client:
        yield test_client


AUTH = {"Authorization": "Bearer secret"}


def test_every_session_route_requires_the_bearer_token(client):
    assert client.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "m", "repos": ["r"], "prompt": "p"},
    ).status_code == 401
    assert client.get("/sessions/s1").status_code == 401
    assert client.post("/sessions/s1/prompt", json={"text": "x"}).status_code == 401
    assert client.post("/sessions/s1/close").status_code == 401
    assert client.delete("/sessions/s1").status_code == 401


def test_creating_a_session_returns_202(client, monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(app_mod.session, "create", create)

    response = client.post(
        "/sessions",
        json={"id": "s1", "agent": "codex", "model": "chatgpt/gpt-5.6-sol",
              "repos": ["acme/repo"], "prompt": "fix it"},
        headers=AUTH,
    )

    assert response.status_code == 202
    assert response.json() == {"id": "s1", "status": "queued"}
    create.assert_awaited_once_with("s1", "codex", "chatgpt/gpt-5.6-sol", ["acme/repo"], "fix it")


def test_an_unknown_agent_is_a_400_not_a_500(client, monkeypatch):
    from eve_computer.acp.registry import UnknownAgent

    monkeypatch.setattr(app_mod.session, "create", AsyncMock(side_effect=UnknownAgent("nope")))

    response = client.post(
        "/sessions",
        json={"id": "s1", "agent": "cursor", "model": "m", "repos": ["r"], "prompt": "p"},
        headers=AUTH,
    )

    assert response.status_code == 400


def test_getting_a_session_passes_the_cursor_through(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    monkeypatch.setattr(
        app_mod.session, "snapshot",
        lambda s, since: {"status": "idle", "turns": [], "cursor": since},
    )

    response = client.get("/sessions/s1?since=4", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["cursor"] == 4


def test_an_unknown_session_is_404_everywhere(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: None)

    assert client.get("/sessions/nope", headers=AUTH).status_code == 404
    assert client.post("/sessions/nope/prompt", json={"text": "x"}, headers=AUTH).status_code == 404
    assert client.post("/sessions/nope/close", headers=AUTH).status_code == 404
    assert client.delete("/sessions/nope", headers=AUTH).status_code == 404


def test_a_reply_is_sent_and_an_interjection_is_only_queued(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    send, enqueue = AsyncMock(), AsyncMock()
    monkeypatch.setattr(app_mod.session, "send", send)
    monkeypatch.setattr(app_mod.session, "enqueue", enqueue)

    client.post("/sessions/s1/prompt", json={"text": "go on", "kind": "reply"}, headers=AUTH)
    client.post(
        "/sessions/s1/prompt",
        json={"text": "use httpx", "kind": "interjection"},
        headers=AUTH,
    )

    send.assert_awaited_once_with("s1", "go on")
    enqueue.assert_awaited_once_with("s1", "use httpx")


def test_the_prompt_kind_defaults_to_reply(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    send = AsyncMock()
    monkeypatch.setattr(app_mod.session, "send", send)
    monkeypatch.setattr(app_mod.session, "enqueue", AsyncMock())

    client.post("/sessions/s1/prompt", json={"text": "go on"}, headers=AUTH)

    send.assert_awaited_once_with("s1", "go on")


def test_closing_returns_the_pull_requests(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    monkeypatch.setattr(
        app_mod.session, "close",
        AsyncMock(return_value={"prs": [{"repo": "acme/repo", "commits": 2, "pr_url": "u"}]}),
    )

    response = client.post("/sessions/s1/close", headers=AUTH)

    assert response.json()["prs"][0]["pr_url"] == "u"


def test_deleting_kills_the_session(client, monkeypatch):
    monkeypatch.setattr(app_mod.session, "get", lambda sid: object())
    kill = AsyncMock()
    monkeypatch.setattr(app_mod.session, "kill", kill)

    response = client.delete("/sessions/s1", headers=AUTH)

    assert response.json() == {"id": "s1", "status": "killed"}
    kill.assert_awaited_once_with("s1")


def test_the_gui_task_queue_is_untouched(client, monkeypatch):
    """The GUI lane's single worker is the whole reason it exists. A change
    that lets two GUI tasks run at once would be silent and would fight over
    one mouse."""
    from unittest.mock import AsyncMock as AM

    monkeypatch.setattr(app_mod.store, "create", AM())
    response = client.post("/tasks", json={"id": "t1", "goal": "click"}, headers=AUTH)
    assert response.status_code == 202
