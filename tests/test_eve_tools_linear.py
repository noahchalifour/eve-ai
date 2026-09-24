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


async def test_a_well_formed_false_success_is_raised_not_silently_treated_as_delivered(
    monkeypatch,
):
    """(fix round 5, item 2) Linear can answer 200 with no `errors` array at
    all and a payload shaped `{"success": false}`: a rejected mutation with
    no error detail. `_call` only raises on the `errors`-array shape, so
    without this check `create_activity` would return this dict as a normal
    success, and the caller would stamp a heartbeat for an activity Linear
    never recorded."""
    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json, headers):
            return _FakeResponse({"data": {"agentActivityCreate": {"success": False}}})

    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _FakeClient)

    with pytest.raises(linear_client.LinearError, match="success: false"):
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


def _review_client(calls, states, attach_success=True):
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
                    {"data": {"issue": {"team": {"states": {"nodes": states}}}}}
                )
            if "attachmentLinkURL" in json["query"]:
                return _FakeResponse(
                    {"data": {"attachmentLinkURL": {"success": attach_success}}}
                )
            return _FakeResponse({"data": {"issueUpdate": {"success": True}}})

    return _FakeClient


async def test_move_issue_to_review_picks_the_in_review_state_by_name(monkeypatch):
    # The team has two `started` states, so position cannot tell them apart.
    calls = []
    states = [
        {"id": "s1", "name": "In Progress", "position": 1.0},
        {"id": "s2", "name": "in review", "position": 2.0},
    ]
    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _review_client(calls, states))

    result = await linear_client.move_issue_to_review("issue-1", ["https://pr/1"])

    assert result["success"] is True
    # The team is resolved from the issue itself; no team id is needed.
    assert calls[0]["variables"] == {"issueId": "issue-1"}
    assert calls[1]["variables"] == {"id": "issue-1", "stateId": "s2"}
    assert calls[2]["variables"]["issueId"] == "issue-1"
    assert calls[2]["variables"]["url"] == "https://pr/1"


async def test_move_issue_to_review_is_a_no_op_when_no_in_review_state_exists(
    monkeypatch,
):
    calls = []
    states = [{"id": "s1", "name": "In Progress", "position": 1.0}]
    monkeypatch.setattr(linear_client.httpx, "AsyncClient", _review_client(calls, states))

    result = await linear_client.move_issue_to_review("issue-1", ["https://pr/1"])

    assert result == {"success": False, "reason": "no In Review state"}
    assert not any("issueUpdate" in c["query"] for c in calls)


async def test_a_failed_attachment_does_not_undo_the_state_move(monkeypatch):
    calls = []
    states = [{"id": "s2", "name": "In Review", "position": 2.0}]
    monkeypatch.setattr(
        linear_client.httpx,
        "AsyncClient",
        _review_client(calls, states, attach_success=False),
    )

    result = await linear_client.move_issue_to_review("issue-1", ["https://pr/1"])

    assert result["success"] is True


# EVE-41: a client_credentials token expires after 30 days. With the app's
# client id/secret configured, a 401 mints a fresh token and retries once.


@pytest.fixture
def client_credentials(monkeypatch):
    monkeypatch.setenv("EVE_TOOLS_LINEAR_CLIENT_ID", "cid")
    monkeypatch.setenv("EVE_TOOLS_LINEAR_CLIENT_SECRET", "csecret")
    from eve_tools.settings import get_tools_settings

    get_tools_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_minted_token(monkeypatch):
    monkeypatch.setattr(linear_client, "_minted_token", None)


def _scripted_client(graphql, token_responses=()):
    """A fake AsyncClient that answers GraphQL posts from `graphql` (a list
    of (status, payload) consumed in order) and token posts from
    `token_responses`, recording every call."""
    graphql = list(graphql)
    token_responses = list(token_responses)
    calls = []

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, data=None):
            calls.append({"url": url, "headers": headers, "data": data})
            if url == linear_client._TOKEN_URL:
                status, payload = token_responses.pop(0)
            else:
                status, payload = graphql.pop(0)
            return _FakeResponse(payload, status)

    return _FakeClient, calls


_OK = (200, {"data": {"agentActivityCreate": {"success": True}}})
_UNAUTHORIZED = (401, {"errors": [{"message": "Authentication required"}]})


def _token_calls(calls):
    return [c for c in calls if c["url"] == linear_client._TOKEN_URL]


async def test_an_expired_token_is_refreshed_and_the_call_retried_once(
    monkeypatch, client_credentials
):
    fake, calls = _scripted_client(
        [_UNAUTHORIZED, _OK, _OK], [(200, {"access_token": "fresh"})]
    )
    monkeypatch.setattr(linear_client.httpx, "AsyncClient", fake)

    result = await linear_client.create_activity("sess-1", {"type": "thought"})

    assert result["success"] is True
    [token_call] = _token_calls(calls)
    assert token_call["data"]["grant_type"] == "client_credentials"
    assert token_call["data"]["client_id"] == "cid"
    assert token_call["data"]["client_secret"] == "csecret"
    assert calls[-1]["headers"]["Authorization"] == "Bearer fresh"

    # The minted token is cached: the next call uses it without re-minting.
    await linear_client.create_activity("sess-1", {"type": "thought"})
    assert len(_token_calls(calls)) == 1
    assert calls[-1]["headers"]["Authorization"] == "Bearer fresh"


async def test_a_graphql_authentication_error_also_triggers_a_refresh(
    monkeypatch, client_credentials
):
    auth_error = (
        200,
        {
            "errors": [
                {
                    "message": "Authentication required, not authenticated",
                    "extensions": {
                        "type": "authentication error",
                        "code": "AUTHENTICATION_ERROR",
                    },
                }
            ]
        },
    )
    fake, calls = _scripted_client(
        [auth_error, _OK], [(200, {"access_token": "fresh"})]
    )
    monkeypatch.setattr(linear_client.httpx, "AsyncClient", fake)

    result = await linear_client.create_activity("sess-1", {"type": "thought"})

    assert result["success"] is True
    assert len(_token_calls(calls)) == 1


async def test_a_failed_refresh_raises_linear_error(monkeypatch, client_credentials):
    fake, _ = _scripted_client([_UNAUTHORIZED], [(400, {"error": "invalid_client"})])
    monkeypatch.setattr(linear_client.httpx, "AsyncClient", fake)

    with pytest.raises(linear_client.LinearError, match="refresh"):
        await linear_client.create_activity("sess-1", {"type": "thought"})
    assert linear_client._minted_token is None


async def test_a_second_401_raises_linear_error_without_looping(
    monkeypatch, client_credentials
):
    fake, calls = _scripted_client(
        [_UNAUTHORIZED, _UNAUTHORIZED], [(200, {"access_token": "fresh"})]
    )
    monkeypatch.setattr(linear_client.httpx, "AsyncClient", fake)

    with pytest.raises(linear_client.LinearError):
        await linear_client.create_activity("sess-1", {"type": "thought"})
    assert len(_token_calls(calls)) == 1
    assert len(calls) == 3


@pytest.mark.parametrize("status", [400, 403])
async def test_other_client_errors_do_not_refresh(
    monkeypatch, client_credentials, status
):
    fake, calls = _scripted_client([(status, {"errors": [{"message": "no"}]})])
    monkeypatch.setattr(linear_client.httpx, "AsyncClient", fake)

    # Unchanged from before EVE-41: raise_for_status surfaces the error.
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        await linear_client.create_activity("sess-1", {"type": "thought"})
    assert _token_calls(calls) == []
    assert len(calls) == 1


async def test_without_client_credentials_a_401_is_not_refreshed(monkeypatch):
    fake, calls = _scripted_client([_UNAUTHORIZED])
    monkeypatch.setattr(linear_client.httpx, "AsyncClient", fake)

    with pytest.raises(RuntimeError, match="HTTP 401"):
        await linear_client.create_activity("sess-1", {"type": "thought"})
    assert _token_calls(calls) == []
