import pytest
from fastapi.testclient import TestClient

PURE = "def run(arguments):\n    return {'n': arguments['a'] + 1}\n"


def _sha(source: str) -> str:
    import hashlib

    return hashlib.sha256(source.encode()).hexdigest()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EVE_SANDBOX_API_KEY", "k" * 32)
    from eve_sandbox.settings import get_sandbox_settings

    get_sandbox_settings.cache_clear()
    from eve_sandbox.app import app

    yield TestClient(app)
    get_sandbox_settings.cache_clear()


def test_healthz_needs_no_auth(client):
    assert client.get("/healthz").status_code == 200


def test_invoke_requires_the_bearer_token(client):
    response = client.post(
        "/invoke",
        json={"tool": "t", "arguments": {}, "source": PURE,
              "source_sha256": _sha(PURE)},
    )
    assert response.status_code == 401


def test_invoke_runs_the_tool(client):
    response = client.post(
        "/invoke",
        headers={"Authorization": "Bearer " + "k" * 32},
        json={"tool": "t", "arguments": {"a": 41}, "source": PURE,
              "source_sha256": _sha(PURE)},
    )
    assert response.status_code == 200
    assert response.json() == {"result": {"n": 42}}


def test_a_hash_mismatch_answers_with_an_error_body_not_a_500(client):
    response = client.post(
        "/invoke",
        headers={"Authorization": "Bearer " + "k" * 32},
        json={"tool": "t", "arguments": {}, "source": PURE,
              "source_sha256": "0" * 64},
    )
    assert response.status_code == 200
    assert "error" in response.json()
