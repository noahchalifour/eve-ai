"""The provisioning script's pure parts. The browser round trip is not tested
- there is nothing to assert about it that would not be asserting httpx.

Loaded by path, not imported: `scripts/` has no `__init__.py` and is operator
tooling rather than part of the package. Same approach as
tests/test_gmail_oauth_setup.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "health_oauth_setup.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("health_oauth_setup", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = _load()


def test_the_authorize_url_carries_the_offline_scope_for_whoop():
    """WHOOP only issues a refresh token when `offline` is requested. Without
    it provisioning appears to succeed and auth dies an hour later - the worst
    available failure mode."""
    url = setup.authorize_url("whoop", "client-1", "http://localhost:8321/callback", "st")
    assert "api.prod.whoop.com" in url
    assert "offline" in url
    assert "response_type=code" in url
    assert "state=st" in url


def test_the_authorize_url_requests_ouras_daily_scopes():
    url = setup.authorize_url("oura", "client-1", "http://localhost:8321/callback", "st")
    assert "cloud.ouraring.com" in url
    assert "daily" in url


def test_an_unknown_provider_is_rejected_by_name():
    with pytest.raises(ValueError, match="garmin"):
        setup.authorize_url("garmin", "c", "http://localhost/cb", "st")


def test_expiry_is_computed_from_expires_in():
    from datetime import UTC, datetime

    result = setup.expires_at({"expires_in": 3600})
    assert result is not None
    assert 3500 < (result - datetime.now(UTC)).total_seconds() < 3700


def test_a_response_without_an_expiry_stores_null():
    """A non-expiring credential is an ordinary row whose refresh path is
    never entered (oauth_store._is_stale)."""
    assert setup.expires_at({"access_token": "a"}) is None
