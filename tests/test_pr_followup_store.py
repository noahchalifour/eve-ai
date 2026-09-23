"""The store queries behind EVE-31 and EVE-32.

Marked `integration`: these run against the real Postgres from
docker-compose.test.yml, like every other store test in this repository.
"""

from __future__ import annotations

import uuid

import pytest

from eve.coding import store

pytestmark = pytest.mark.integration


async def _session(kind, pr_number, repo, status="running", thread_id="t1", **extra):
    session_id = str(uuid.uuid4())
    await store.create_session(
        session_id=session_id, member_sub="sub-noah", thread_id=thread_id,
        goal="g", agent="dsh", model="m", repos=[repo], context="",
        kind=kind, pr_number=pr_number, **extra,
    )
    if status != "running":
        await store.mark_resolved(session_id, status, {})
    return session_id


async def test_latest_review_for_is_the_newest_review_of_that_pr():
    repo = f"acme/{uuid.uuid4().hex}"
    await _session("review", 7, repo, status="finished", thread_id=None, head_sha="a")
    newest = await _session("review", 7, repo, status="finished", thread_id=None, head_sha="b")
    await _session("review", 8, repo, thread_id=None, head_sha="c")

    row = await store.latest_review_for(repo, 7)

    assert row["id"] == newest
    assert row["head_sha"] == "b"
    assert await store.latest_review_for(repo, 99) is None


async def test_pr_session_stats_counts_totals_and_live_sessions_per_kind():
    repo = f"acme/{uuid.uuid4().hex}"
    await _session("address", 7, repo, status="finished")
    await _session("address", 7, repo)
    await _session("review", 7, repo, thread_id=None, head_sha="x")

    stats = await store.pr_session_stats("address", repo, 7)

    assert stats["total"] == 2
    assert stats["live"] == 1
    assert (await store.pr_session_stats("address", repo, 8))["total"] == 0


async def test_origin_of_pr_finds_the_coding_session_that_opened_it():
    repo = f"acme/{uuid.uuid4().hex}"
    url = f"https://github.com/{repo}/pull/7"
    opener = await _session("code", None, repo)
    await store.mark_resolved(opener, "finished", {"prs": [
        {"repo": repo, "commits": 1, "pr_url": url, "head_sha": "abc"}
    ]})

    row = await store.origin_of_pr(url)

    assert row["id"] == opener
    assert await store.origin_of_pr(f"https://github.com/{repo}/pull/8") is None


async def test_an_address_session_counts_as_an_implementer():
    """So a re-review of the commit a follow-up pushed picks another model."""
    repo = f"acme/{uuid.uuid4().hex}"
    fixer = await _session("address", 7, repo)
    await store.mark_resolved(fixer, "finished", {"prs": [
        {"repo": repo, "commits": 1, "branch": "eve/x", "head_sha": "fixed"}
    ]})

    assert await store.implementer_of(repo, "fixed") == ("dsh", "m")
