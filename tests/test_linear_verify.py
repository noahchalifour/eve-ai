import hashlib
import hmac
import time

from eve_linear.verify import timestamp_is_fresh, verify_signature

SECRET = "s" * 32


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_a_correct_signature_verifies():
    body = b'{"action":"created"}'
    assert verify_signature(body, _sign(body), SECRET) is True


def test_a_wrong_signature_is_refused():
    body = b'{"action":"created"}'
    assert verify_signature(body, _sign(body, "other" * 10), SECRET) is False


def test_a_missing_header_is_refused():
    assert verify_signature(b"{}", None, SECRET) is False
    assert verify_signature(b"{}", "", SECRET) is False


def test_an_empty_secret_is_refused():
    body = b"{}"
    assert verify_signature(body, _sign(body), "") is False


def test_a_non_ascii_header_is_refused_rather_than_raising():
    # compare_digest raises TypeError on a str operand containing non-ASCII,
    # and a raise here is a 500, which tells Linear to retry a request that
    # should have been refused outright.
    assert verify_signature(b"{}", "\u00ff" * 64, SECRET) is False


def test_a_non_hex_header_is_refused_rather_than_raising():
    assert verify_signature(b"{}", "zzzz", SECRET) is False


def test_the_signature_is_over_raw_bytes_not_reserialized_json():
    # Linear signs the exact bytes sent. Re-serializing parsed JSON changes
    # the spacing and therefore the MAC.
    raw = b'{"a": 1,  "b": 2}'
    reserialized = b'{"a":1,"b":2}'
    signature = _sign(raw)
    assert verify_signature(raw, signature, SECRET) is True
    assert verify_signature(reserialized, signature, SECRET) is False


def test_a_fresh_timestamp_passes_and_a_stale_one_does_not():
    now_ms = int(time.time() * 1000)
    assert timestamp_is_fresh(now_ms, now_ms) is True
    assert timestamp_is_fresh(now_ms - 59_000, now_ms) is True
    assert timestamp_is_fresh(now_ms - 61_000, now_ms) is False


def test_a_future_timestamp_outside_the_window_is_refused():
    now_ms = int(time.time() * 1000)
    assert timestamp_is_fresh(now_ms + 61_000, now_ms) is False


def test_a_missing_or_unusable_timestamp_is_refused():
    now_ms = int(time.time() * 1000)
    assert timestamp_is_fresh(None, now_ms) is False
    assert timestamp_is_fresh("not-a-number", now_ms) is False
