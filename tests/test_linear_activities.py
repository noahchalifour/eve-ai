from eve_linear import activities


def test_each_builder_produces_the_shape_linear_validates():
    assert activities.thought("thinking") == {"type": "thought", "body": "thinking"}
    assert activities.elicitation("which repo?") == {
        "type": "elicitation",
        "body": "which repo?",
    }
    assert activities.response("done") == {"type": "response", "body": "done"}
    assert activities.error("broke") == {"type": "error", "body": "broke"}


def test_an_action_without_a_result_omits_the_result_key():
    # Linear validates these server-side; a null result on a started action
    # is a different shape from an absent one.
    assert activities.action("Searching", "the repo") == {
        "type": "action",
        "action": "Searching",
        "parameter": "the repo",
    }


def test_an_action_with_a_result_includes_it():
    assert activities.action("Searched", "the repo", "3 hits") == {
        "type": "action",
        "action": "Searched",
        "parameter": "the repo",
        "result": "3 hits",
    }


async def test_emit_calls_eve_tools_and_reports_success(monkeypatch):
    calls = []

    async def _fake_invoke(tool, arguments, **kwargs):
        calls.append((tool, arguments))
        return '{"success": true}'

    monkeypatch.setattr(activities, "invoke", _fake_invoke)

    assert await activities.emit("sess-1", activities.thought("hi")) is True
    assert calls[0][0] == "linear.create_activity"
    assert calls[0][1]["session_id"] == "sess-1"


async def test_emit_returns_false_on_an_error_string_and_never_raises(monkeypatch):
    # tools_client.invoke degrades to an "error:" string rather than raising.
    async def _fake_invoke(tool, arguments, **kwargs):
        return "error: eve-tools unavailable (ConnectError)"

    monkeypatch.setattr(activities, "invoke", _fake_invoke)

    assert await activities.emit("sess-1", activities.thought("hi")) is False


async def test_emit_swallows_an_unexpected_exception(monkeypatch):
    # THE RULE: losing narration is bad; killing a running coding session
    # because a GraphQL call failed is worse.
    async def _fake_invoke(tool, arguments, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(activities, "invoke", _fake_invoke)

    assert await activities.emit("sess-1", activities.thought("hi")) is False


async def test_emit_stamps_the_heartbeat_only_on_success(monkeypatch):
    touched = []

    async def _fake_touch(session_id):
        touched.append(session_id)

    monkeypatch.setattr(activities.store, "touch_linear_emitted", _fake_touch)

    async def _ok(tool, arguments, **kwargs):
        return '{"success": true}'

    monkeypatch.setattr(activities, "invoke", _ok)
    await activities.emit("sess-1", activities.thought("hi"), session_id="row-1")
    assert touched == ["row-1"]

    async def _fail(tool, arguments, **kwargs):
        return "error: nope"

    monkeypatch.setattr(activities, "invoke", _fail)
    await activities.emit("sess-1", activities.thought("hi"), session_id="row-1")
    # A failed emission must not advance the clock, or the heartbeat would
    # believe Linear heard something it never received.
    assert touched == ["row-1"]
