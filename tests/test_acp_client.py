"""The box's side of the protocol. Confinement is the load-bearing part:
the agent has a shell and could ask for any path, and `fs/*` must not become
a second, wider door than the worktree it was given."""

from pathlib import Path

import pytest
from acp.schema import (
    AllowedOutcome,
    PermissionOption,
    ToolCallUpdate,
)

from eve_computer.acp.client import PathEscapedRoot, SessionClient


def _client(tmp_path: Path, updates: list | None = None) -> SessionClient:
    return SessionClient(root=tmp_path, on_update=(updates if updates is None else updates.append))


async def test_permission_requests_are_auto_approved(tmp_path):
    client = _client(tmp_path, [])
    options = [
        PermissionOption(option_id="no", name="Reject", kind="reject_once"),
        PermissionOption(option_id="yes", name="Allow", kind="allow_always"),
    ]

    response = await client.request_permission(
        session_id="s1", tool_call=ToolCallUpdate(tool_call_id="t1"), options=options
    )

    assert isinstance(response.outcome, AllowedOutcome)
    assert response.outcome.option_id == "yes"


async def test_permission_is_denied_when_no_allow_option_is_offered(tmp_path):
    client = _client(tmp_path, [])
    options = [PermissionOption(option_id="no", name="Reject", kind="reject_once")]

    response = await client.request_permission(
        session_id="s1", tool_call=ToolCallUpdate(tool_call_id="t1"), options=options
    )

    assert response.outcome.outcome == "cancelled"


async def test_reading_a_file_inside_the_root_returns_its_content(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    client = _client(tmp_path, [])

    response = await client.read_text_file(session_id="s1", path="a.txt")

    assert response.content == "hello"


async def test_reading_honours_line_and_limit(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\nfour\n")
    client = _client(tmp_path, [])

    response = await client.read_text_file(session_id="s1", path="a.txt", line=2, limit=2)

    assert response.content == "two\nthree\n"


async def test_writing_a_file_creates_missing_parents(tmp_path):
    client = _client(tmp_path, [])

    await client.write_text_file(session_id="s1", path="deep/nested/a.txt", content="x")

    assert (tmp_path / "deep" / "nested" / "a.txt").read_text() == "x"


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "sub/../../outside.txt"])
async def test_paths_outside_the_root_are_refused(tmp_path, path):
    client = _client(tmp_path, [])

    with pytest.raises(PathEscapedRoot):
        await client.read_text_file(session_id="s1", path=path)
    with pytest.raises(PathEscapedRoot):
        await client.write_text_file(session_id="s1", path=path, content="x")


async def test_a_symlink_pointing_out_of_the_root_is_refused(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret")
    (tmp_path / "link.txt").symlink_to(outside)
    client = _client(tmp_path, [])

    with pytest.raises(PathEscapedRoot):
        await client.read_text_file(session_id="s1", path="link.txt")


async def test_session_updates_are_handed_to_the_callback(tmp_path):
    updates: list = []
    client = _client(tmp_path, updates)

    await client.session_update(session_id="s1", update={"session_update": "agent_message_chunk"})

    assert updates == [{"session_update": "agent_message_chunk"}]
