import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer " + "k" * 32}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_API_KEY", "k" * 32)
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    from eve_computer import app as app_module
    from eve_computer import store

    store._tasks.clear()
    with TestClient(app_module.app) as c:
        yield c, app_module
    get_computer_settings.cache_clear()


def test_healthz_needs_no_auth(client):
    c, _ = client
    assert c.get("/healthz").status_code == 200


def test_tasks_requires_the_bearer_token(client):
    c, _ = client
    assert c.post("/tasks", json={"id": "t1", "goal": "do it"}).status_code == 401


def test_dispatching_a_task_returns_202_and_queued(client):
    c, _ = client
    response = c.post("/tasks", headers=AUTH, json={"id": "t1", "goal": "do it"})
    assert response.status_code == 202
    assert response.json()["status"] == "queued"


def test_a_dispatched_task_eventually_finishes(client):
    c, app_module = client
    app_module.run_task = AsyncMock(return_value={"summary": "done"})

    c.post("/tasks", headers=AUTH, json={"id": "t1", "goal": "do it"})
    deadline = time.time() + 2
    status = None
    while time.time() < deadline:
        status = c.get("/tasks/t1", headers=AUTH).json()
        if status["status"] not in ("queued", "running"):
            break
        time.sleep(0.02)

    assert status["status"] == "finished"
    assert status["result"] == {"summary": "done"}


def test_a_task_whose_result_carries_an_error_is_marked_failed(client):
    c, app_module = client
    app_module.run_task = AsyncMock(return_value={"error": "RuntimeError: boom"})

    c.post("/tasks", headers=AUTH, json={"id": "t1", "goal": "do it"})
    deadline = time.time() + 2
    status = None
    while time.time() < deadline:
        status = c.get("/tasks/t1", headers=AUTH).json()
        if status["status"] not in ("queued", "running"):
            break
        time.sleep(0.02)

    assert status["status"] == "failed"


def test_an_unknown_task_is_404(client):
    c, _ = client
    assert c.get("/tasks/nope", headers=AUTH).status_code == 404
    assert c.delete("/tasks/nope", headers=AUTH).status_code == 404


def test_deleting_a_queued_task_marks_it_killed(client):
    c, app_module = client
    app_module.run_task = AsyncMock(return_value={"summary": "should not run"})

    c.post("/tasks", headers=AUTH, json={"id": "t1", "goal": "do it"})
    response = c.delete("/tasks/t1", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "killed"


def test_an_artifact_path_cannot_escape_the_tasks_directory(client, tmp_path, monkeypatch):
    c, app_module = client
    from eve_computer.settings import get_computer_settings

    monkeypatch.setenv("EVE_COMPUTER_TASKS_DIR", str(tmp_path))
    get_computer_settings.cache_clear()
    (tmp_path / "secret.txt").write_text("outside")

    response = c.get("/tasks/t1/artifacts/../../secret.txt", headers=AUTH)
    assert response.status_code == 404
