"""The real `dsh` binary, against the real file `harness.py` writes.

Everything in tests/test_acp_harness_home.py asserts the SHAPE of that
file - which keys it holds, where it lands, what it never contains. None of
that proves the launcher accepts it, and the three ways it could be wrong
are all silent:

- `!!js` is a shorthand for `tag:yaml.org,2002:js`. An emitter that writes
  the long form, or quotes the scalar wrong, produces a valid YAML file the
  launcher reads as a literal string - and every session runs against a
  model named `process.env.EVE_ACP_MODEL`.
- pi-ai refuses a model its route does not declare. The model therefore
  appears TWICE in that file, and a version that declares it once still
  boots, still initializes, and fails only at `session/new`.
- `--patch` is the last layer the launcher applies. That is the whole
  reason this box's routing can share a harness home with a profile pulled
  from git (EVE-24), and it is a property of the launcher, not of us.

So this file drives the actual binary over actual stdio and asserts a
session is created. It needs no key and no proxy: `session/new` resolves
the route and the model without sending a single token, which is exactly
the boundary worth testing here - tests/test_coding_live.py owns the part
that spends money.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

pytestmark = pytest.mark.live

_MODEL = "chatgpt/gpt-5.6-sol"


def _dsh_available() -> bool:
    return shutil.which("dsh") is not None


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _dsh_available(), reason="the dsh launcher is not installed"),
]


def _speak_acp(home, cwd, frames: list[dict], settle: float = 20.0) -> list[dict]:
    """Write ACP frames to a real `dsh --profile acp` and read what comes
    back. Newline-delimited JSON-RPC over stdio, which is the whole
    transport - the harness owns stdout and writes nothing else to it.

    stdin is held open between frames rather than closed with them: EOF on
    stdin is the launcher's bounded shutdown signal, so writing every frame
    and closing would race the replies against the exit it just asked for.
    `dsh` also brings its routes up asynchronously after `initialize`
    answers, which is why `session/new` is sent after a pause rather than
    back to back - a real client observes the same and the pause is the
    honest way to model it.
    """
    from eve_computer.acp import harness

    process = subprocess.Popen(
        ["dsh", "--profile", "acp", "--patch", str(harness.route_patch_path())],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=str(cwd),
        env={
            **os.environ,
            "DSH_HOME": str(home),
            harness.MODEL_VAR: _MODEL,
            harness.API_KEY_VAR: "sk-not-used-by-session-new",
        },
    )
    try:
        for index, frame in enumerate(frames):
            process.stdin.write(json.dumps(frame) + "\n")
            process.stdin.flush()
            if index == 0:
                time.sleep(settle)
        time.sleep(settle)
        # Closing stdin IS the shutdown request; `communicate` then only
        # drains what the harness already wrote and waits for the exit.
        stdout, _stderr = process.communicate(timeout=60)
    finally:
        process.kill()
    return [
        json.loads(line)
        for line in stdout.splitlines()
        if line.strip().startswith("{")
    ]


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_DSH_HOME", str(tmp_path / "harness"))
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_BASE_URL", "https://litellm.example")
    monkeypatch.setenv("EVE_COMPUTER_LITELLM_API_KEY", "sk-test")
    monkeypatch.setenv("EVE_COMPUTER_DSH_PROFILE_REPO", "")
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    from eve_computer.acp import harness

    home = harness.prepare()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield home, workspace
    get_computer_settings.cache_clear()


def _new_session(home, workspace) -> dict:
    replies = _speak_acp(
        home,
        workspace,
        [
            {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}},
                },
            },
            {
                "jsonrpc": "2.0", "id": 2, "method": "session/new",
                "params": {"cwd": str(workspace), "mcpServers": []},
            },
        ],
    )
    by_id = {reply.get("id"): reply for reply in replies}
    assert "result" in by_id[1], by_id[1]
    return by_id[2]


def _selected_model(reply: dict) -> str:
    """What the harness will actually prompt with. `session/new` answers with
    the session's resolved configuration, and the model option's
    `currentValue` is a JSON `[provider, model]` pair - which is the only
    place the launcher tells us whether it EVALUATED our `!!js` expression
    or took it as the literal name of a model."""
    for option in reply["result"]["configOptions"]:
        if option["id"] == "model":
            return json.loads(option["currentValue"])[1]
    raise AssertionError(f"no model option in {reply}")


def test_the_launcher_accepts_the_route_this_box_writes(prepared):
    """`session/new` is the assertion: it is the call that resolves the
    provider and the model, so a route the launcher parsed but could not
    serve fails here and nowhere earlier.

    Asserting the RESOLVED model, not merely that a session was created: a
    `!!js` scalar emitted as a plain string is a route the launcher accepts,
    a session it creates, and a model named `process.env.EVE_ACP_MODEL` that
    nothing in this repository would ever notice.
    """
    home, workspace = prepared

    reply = _new_session(home, workspace)

    assert "result" in reply, reply
    assert reply["result"]["sessionId"]
    assert _selected_model(reply) == _MODEL


def test_a_pulled_profile_cannot_outrank_this_boxs_route(prepared):
    """EVE-24 asks for a profile pulled from git AND for this harness to be
    the default. Those two only coexist because `--patch` is applied after
    the profile's own patch file. A launcher that changed that order would
    leave this box routed at whatever Noah's laptop was configured for -
    which would fail at the first prompt, in production, on a box with no
    interactive user."""
    home, workspace = prepared
    hijack = "- id: acp\n  config: {provider: laptop-only, model: laptop-only}\n"
    (home / "profiles" / "acp" / "cordis.patch.yml").write_text(hijack)
    (home / "cordis.patch.yml").write_text(hijack)

    reply = _new_session(home, workspace)

    # `laptop-only` is a provider nothing registers, so a launcher that let
    # the pulled layer win would answer `no adapter registered for provider`.
    assert "result" in reply, reply
    assert _selected_model(reply) == _MODEL
