import pytest

from eve_tools import linear_client


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_LINEAR_API_TOKEN", "lin_api_token")
    from eve_tools.settings import get_tools_settings

    get_tools_settings.cache_clear()
    yield
    get_tools_settings.cache_clear()


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


async def test_create_activity_posts_the_mutation_with_the_token(monkeypatch):
    captured = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResponse(
                {"data": {"agentActivityCreate": {"success": True}}}
            )

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    result = await linear_client.create_activity(
        "sess-1", {"type": "thought", "body": "On it."}
    )

    assert result["success"] is True
    assert captured["headers"]["Authorization"] == "Bearer lin_api_token"
    assert captured["json"]["variables"]["input"]["agentSessionId"] == "sess-1"
    assert captured["json"]["variables"]["input"]["content"]["type"] == "thought"


async def test_a_graphql_error_body_is_raised_not_silently_treated_as_success(
    monkeypatch,
):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            # GraphQL answers 200 with an errors array. Treating that as
            # success would report a lost activity as delivered.
            return _FakeResponse({"errors": [{"message": "bad session id"}]})

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(linear_client.LinearError, match="bad session id"):
        await linear_client.create_activity("sess-1", {"type": "thought", "body": "x"})


async def test_a_missing_token_raises_rather_than_calling_linear(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_LINEAR_API_TOKEN", "")
    from eve_tools.settings import get_tools_settings

    get_tools_settings.cache_clear()

    with pytest.raises(linear_client.LinearError, match="not configured"):
        await linear_client.create_activity("sess-1", {"type": "thought", "body": "x"})


async def test_move_issue_to_started_picks_the_lowest_position_started_state(
    monkeypatch,
):
    calls = []

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            calls.append(json)
            if "states" in json["query"]:
                return _FakeResponse(
                    {
                        "data": {
                            "team": {
                                "states": {
                                    "nodes": [
                                        {"id": "s2", "name": "Started", "position": 2.0},
                                        {"id": "s1", "name": "Todo", "position": 1.0},
                                    ]
                                }
                            }
                        }
                    }
                )
            return _FakeResponse({"data": {"issueUpdate": {"success": True}}})

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    await linear_client.move_issue_to_started("issue-1", "team-1")

    assert calls[1]["variables"]["stateId"] == "s1"


async def test_move_issue_to_started_is_a_no_op_when_no_started_state_exists(
    monkeypatch,
):
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            return _FakeResponse({"data": {"team": {"states": {"nodes": []}}}})

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    result = await linear_client.move_issue_to_started("issue-1", "team-1")
    assert result == {"success": False, "reason": "no started state"}
