"""The review columns on eve_coding_session.

Marked `integration`: these run against the real Postgres from
docker-compose.test.yml, like every other store test in this repository.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from eve.coding import store

pytestmark = pytest.mark.integration


async def test_a_coding_session_defaults_to_kind_code():
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id, member_sub="sub-noah", thread_id="t1",
        goal="fix the thing", agent="dsh", model="m", repos=["acme/repo"],
        context="",
    )

    row = await store.get(session_id)

    assert row["kind"] == "code"
    assert row["pr_number"] is None
    assert row["head_sha"] is None


async def test_a_review_session_records_the_pull_request_it_reviews():
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id, member_sub="sub-noah", thread_id="t1",
        goal="review acme/repo#7", agent="claude", model="m",
        repos=["acme/repo"], context="", kind="review", pr_number=7,
        head_sha="abc123",
    )

    row = await store.get(session_id)

    assert row["kind"] == "review"
    assert row["pr_number"] == 7
    assert row["head_sha"] == "abc123"


async def test_an_already_reviewed_commit_is_recognised():
    """The idempotence key. A relabelled pull request whose code has not
    changed must not buy a second review."""
    session_id = str(uuid.uuid4())
    sha = uuid.uuid4().hex
    await store.create_session(
        session_id=session_id, member_sub="sub-noah", thread_id="t1",
        goal="review", agent="claude", model="m", repos=["acme/repo"],
        context="", kind="review", pr_number=7, head_sha=sha,
    )

    assert await store.review_exists_for("acme/repo", 7, sha) is True
    assert await store.review_exists_for("acme/repo", 7, "a-different-sha") is False
    assert await store.review_exists_for("acme/other", 7, sha) is False


async def test_a_review_session_can_have_no_thread():
    """A review triggered by a GitHub webhook has no member-owned
    conversation thread to attach to - nobody was chatting when GitHub fired
    the hook."""
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id, member_sub="sub-noah", thread_id=None,
        goal="review acme/repo#7", agent="claude", model="m",
        repos=["acme/repo"], context="", kind="review", pr_number=7,
        head_sha="abc",
    )

    row = await store.get(session_id)

    assert row["thread_id"] is None


async def test_a_coding_session_still_requires_a_thread():
    """The check constraint: only `review` sessions may omit a thread."""
    session_id = str(uuid.uuid4())
    with pytest.raises(psycopg.Error):
        await store.create_session(
            session_id=session_id, member_sub="sub-noah", thread_id=None,
            goal="fix the thing", agent="dsh", model="m",
            repos=["acme/repo"], context="", kind="code",
        )
