"""The webhook's own logic: signature verification and payload shaping."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from eve_ambient.sources import github


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correct_signature_verifies():
    body = b'{"action":"labeled"}'

    assert github.verify("s3cret", _sign("s3cret", body), body) is True


def test_a_wrong_signature_is_rejected():
    body = b'{"action":"labeled"}'

    assert github.verify("s3cret", _sign("wrong", body), body) is False


def test_a_tampered_body_is_rejected():
    signature = _sign("s3cret", b'{"action":"labeled"}')

    assert github.verify("s3cret", signature, b'{"action":"closed"}') is False


def test_an_empty_secret_never_verifies():
    """An unconfigured secret must not become an open door."""
    body = b"{}"

    assert github.verify("", _sign("", body), body) is False


def test_a_malformed_signature_header_is_rejected_not_raised():
    assert github.verify("s3cret", "garbage", b"{}") is False
    assert github.verify("s3cret", "", b"{}") is False


def _payload(action="labeled", login="chalifournoah"):
    return {
        "action": action,
        "number": 7,
        "pull_request": {
            "number": 7,
            "head": {"sha": "abc123"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/acme/repo/pull/7",
        },
        "repository": {"full_name": "acme/repo"},
        "sender": {"login": login},
        "label": {"name": "eve-review"},
    }


def test_a_labeled_event_becomes_a_signal():
    signal = github.from_webhook(_payload())

    assert signal.source == "review"
    assert signal.payload["repo"] == "acme/repo"
    assert signal.payload["pr_number"] == 7
    assert signal.payload["head_sha"] == "abc123"
    assert signal.payload["base_ref"] == "main"


def test_the_dedup_key_is_repo_pr_and_commit():
    """Not the delivery id: a redelivery, a relabel, and two people
    labelling at once all describe the same review of the same code."""
    signal = github.from_webhook(_payload())

    assert signal.key == "acme/repo#7@abc123"


def test_an_unrelated_action_is_refused():
    with pytest.raises(ValueError):
        github.from_webhook(_payload(action="closed"))


def test_an_assigned_event_is_accepted():
    payload = _payload(action="assigned")
    del payload["label"]

    signal = github.from_webhook(payload)

    assert signal.payload["pr_number"] == 7


def test_a_payload_without_a_pull_request_is_refused():
    with pytest.raises(ValueError):
        github.from_webhook({"action": "labeled", "repository": {}})
