"""The box's side of EVE-31 and EVE-32: the hints, `followup.json`
validation, and an address session's close.

`repo` is faked here; test_acp_repo_followup.py drives the real git.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from eve_computer.acp import review
from eve_computer.acp import session as session_mod
from eve_computer.acp.review import InvalidFindings

FEEDBACK = [
    {"kind": "review", "id": 5, "author": "chalifournoah", "body": "see inline"},
    {"kind": "review_comment", "id": 11, "author": "chalifournoah", "body": "rename"},
    {"kind": "comment", "id": 21, "author": "chalifournoah", "body": "also docs"},
]


@pytest.fixture(autouse=True)
def _clean():
    session_mod._SESSIONS.clear()
    yield
    session_mod._SESSIONS.clear()


def _write(tmp_path, followup):
    (tmp_path / "followup.json").write_text(json.dumps(followup))


# --- hints ---------------------------------------------------------------


def test_a_first_review_is_scoped_to_the_whole_pull_request():
    hint = session_mod.review_hint(["acme/repo"], 7, "base")

    assert "git diff base...HEAD" in hint
    assert "RE-REVIEW" not in hint


def test_a_rereview_focuses_on_what_changed_since_the_last_one():
    hint = session_mod.review_hint(["acme/repo"], 7, "base", since="old-head")

    assert "RE-REVIEW" in hint
    assert "git diff old-head..HEAD" in hint
    # The whole pull request stays in view as context.
    assert "git diff base...HEAD" in hint
    assert "review.json" in hint


def test_the_address_hint_names_the_skill_and_the_contract():
    hint = session_mod.address_hint(["acme/repo"], 7, "eve/fix-1")

    assert "receiving-code-review/SKILL.md" in hint
    assert "feedback.json" in hint
    assert "followup.json" in hint
    assert "do not push" in hint.lower()
    assert "eve/fix-1" in hint


# --- followup.json -------------------------------------------------------


def test_a_valid_followup_loads(tmp_path):
    _write(tmp_path, {"summary": "Done.", "replies": [{"comment_id": 11, "body": "Renamed."}]})

    followup = review.load_followup(tmp_path, FEEDBACK)

    assert followup["replies"][0]["comment_id"] == 11


def test_a_missing_followup_is_invalid(tmp_path):
    with pytest.raises(InvalidFindings):
        review.load_followup(tmp_path, FEEDBACK)


@pytest.mark.parametrize("comment_id", [999, 21, 5, "11", True])
def test_a_reply_may_only_name_an_inline_comment_the_box_fetched(tmp_path, comment_id):
    """A confused or manipulated agent cannot reply into some other thread,
    and a conversation comment or review has no reply thread to post into."""
    _write(tmp_path, {"summary": "", "replies": [{"comment_id": comment_id, "body": "x"}]})

    with pytest.raises(InvalidFindings):
        review.load_followup(tmp_path, FEEDBACK)


def test_an_empty_reply_body_is_invalid(tmp_path):
    _write(tmp_path, {"summary": "", "replies": [{"comment_id": 11, "body": "  "}]})

    with pytest.raises(InvalidFindings):
        review.load_followup(tmp_path, FEEDBACK)


def test_no_replies_is_a_valid_followup(tmp_path):
    """Pushing back in one summary comment, or changing nothing, is legitimate."""
    _write(tmp_path, {"summary": "Nothing needed changing; see reasoning."})

    assert review.load_followup(tmp_path, FEEDBACK)["replies"] == []


# --- closing an address session ------------------------------------------


def _address_session(tmp_path):
    session = session_mod.Session(
        id="s1", agent="dsh", model="m", repos=["acme/repo"],
        branch="eve/fix-1", directory=tmp_path, kind="address", pr_number=7,
        feedback=FEEDBACK,
    )
    session_mod._SESSIONS["s1"] = session
    return session


async def test_closing_pushes_then_replies(tmp_path, monkeypatch):
    _address_session(tmp_path)
    _write(tmp_path, {"summary": "Fixed.", "replies": [{"comment_id": 11, "body": "Renamed."}]})
    pushed = AsyncMock(return_value={"repo": "acme/repo", "commits": 2,
                                     "branch": "eve/fix-1", "head_sha": "new"})
    posted = AsyncMock(return_value={"replies": 1, "commented": True, "error": None,
                                     "url": "https://github.com/acme/repo/pull/7"})
    monkeypatch.setattr(session_mod.repo, "push_followup", pushed)
    monkeypatch.setattr(session_mod.repo, "post_followup", posted)
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())

    result = await session_mod.close_address("s1")

    assert result["commits"] == 2
    assert result["replies"] == 1
    # The `prs` shape, so `implementer_of` finds this session as the author
    # of the head it pushed and a re-review picks a different model.
    assert result["prs"][0]["head_sha"] == "new"
    assert posted.await_args.args[:2] == ("acme/repo", 7)


async def test_a_rejected_push_posts_no_replies(tmp_path, monkeypatch):
    """Never tell a reviewer "fixed" when the fix never reached the PR."""
    _address_session(tmp_path)
    _write(tmp_path, {"summary": "Fixed.", "replies": [{"comment_id": 11, "body": "Renamed."}]})
    monkeypatch.setattr(session_mod.repo, "push_followup", AsyncMock(
        return_value={"repo": "acme/repo", "commits": 1, "error": "rejected"}
    ))
    posted = AsyncMock()
    monkeypatch.setattr(session_mod.repo, "post_followup", posted)
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", AsyncMock())

    result = await session_mod.close_address("s1")

    posted.assert_not_awaited()
    assert result["error"] == "rejected"


async def test_no_followup_file_pushes_nothing_and_fails(tmp_path, monkeypatch):
    """Unexplained commits on a reviewer's pull request are worse than none."""
    session = _address_session(tmp_path)
    pushed = AsyncMock()
    removed = AsyncMock()
    monkeypatch.setattr(session_mod.repo, "push_followup", pushed)
    monkeypatch.setattr(session_mod.repo, "remove_worktrees", removed)

    with pytest.raises(InvalidFindings):
        await session_mod.close_address("s1")

    pushed.assert_not_awaited()
    removed.assert_awaited_once()
    assert session.status == "failed"


async def test_an_address_session_needs_a_pull_request(tmp_path, monkeypatch):
    monkeypatch.setenv("EVE_COMPUTER_SESSIONS_DIR", str(tmp_path))
    from eve_computer.settings import get_computer_settings

    get_computer_settings.cache_clear()
    with pytest.raises(ValueError):
        await session_mod.create("s2", "dsh", "m", ["acme/repo"], "x", kind="address")
    assert "s2" not in session_mod._SESSIONS
    get_computer_settings.cache_clear()
