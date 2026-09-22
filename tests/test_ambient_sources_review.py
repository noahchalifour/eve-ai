"""Resolved reviews as signals. Mirrors test_ambient_sources_coding.py,
including its 24-hour re-derivation window."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from eve_ambient.sources import review


def _session(status="finished", result=None):
    return {
        "id": "s1", "member_sub": "sub-noah", "thread_id": "t1",
        "goal": "review acme/repo#7", "repos": ["acme/repo"], "kind": "review",
        "pr_number": 7, "head_sha": "abc", "status": status,
        "result": result if result is not None else {},
        "finished_at": datetime.now(UTC),
    }


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.setattr(
        review.coding_store, "recently_resolved_sessions", AsyncMock(return_value=[])
    )


async def test_a_posted_review_names_its_counts_and_link():
    review.coding_store.recently_resolved_sessions.return_value = [
        _session(result={"posted": True, "findings": 3, "url": "https://x/7",
                         "counts": {"critical": 1, "nit": 2}})
    ]

    signals = await review.poll("sub-noah")

    assert signals[0].source == "review"
    assert "3" in signals[0].summary
    assert "critical" in signals[0].summary
    assert "https://x/7" in signals[0].summary


async def test_a_clean_review_says_so_rather_than_staying_silent():
    review.coding_store.recently_resolved_sessions.return_value = [
        _session(result={"posted": True, "findings": 0, "url": "https://x/7",
                         "counts": {}})
    ]

    signals = await review.poll("sub-noah")

    assert "no findings" in signals[0].summary.lower()


async def test_a_review_that_could_not_be_posted_still_carries_its_findings():
    """The review took real time and real tokens; losing the findings
    because the post failed would waste all of it."""
    review.coding_store.recently_resolved_sessions.return_value = [
        _session(result={"posted": False, "error": "gh failed", "findings": 2,
                         "counts": {"critical": 2}, "url": None})
    ]

    signals = await review.poll("sub-noah")

    assert "could not" in signals[0].summary.lower()
    assert "2" in signals[0].summary


async def test_a_failed_review_is_reported():
    review.coding_store.recently_resolved_sessions.return_value = [
        _session(status="failed", result={"error": "no usable findings file"})
    ]

    signals = await review.poll("sub-noah")

    assert "no usable findings file" in signals[0].summary


async def test_coding_sessions_are_not_reported_as_reviews():
    """Both kinds live in one table; this source must filter."""
    coding_row = {**_session(), "kind": "code"}
    review.coding_store.recently_resolved_sessions.return_value = [coding_row]

    signals = await review.poll("sub-noah")

    assert signals == []
