"""`scripts/gmail_oauth_setup.py` is operator tooling, not part of the package,
so it is loaded by path rather than imported.

Only `merge_member` is covered, and deliberately so: it is the one piece of
logic in that script where a quiet mistake costs something real. Every family
member's refresh token lives in a single JSON object, so a merge that dropped
a key would destroy the *other* member's credential — and they would only find
out the next time Eve tried to read their mail.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gmail_oauth_setup.py"


def _load():
    spec = importlib.util.spec_from_file_location("gmail_oauth_setup", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = _load()

NOAH = "sub-noah"
KENDRA = "sub-kendra"
CREDENTIAL = {"refresh_token": "new", "client_id": "x"}


def test_a_first_member_starts_the_object():
    assert setup.merge_member(None, NOAH, CREDENTIAL) == {NOAH: CREDENTIAL}


@pytest.mark.parametrize("empty", ["", "   ", "{}"])
def test_an_empty_or_blank_value_is_treated_as_no_members(empty):
    assert setup.merge_member(empty, NOAH, CREDENTIAL) == {NOAH: CREDENTIAL}


def test_a_second_member_does_not_displace_the_first():
    existing = '{"sub-noah": {"refresh_token": "noahs"}}'
    merged = setup.merge_member(existing, KENDRA, CREDENTIAL)
    assert merged[NOAH] == {"refresh_token": "noahs"}
    assert merged[KENDRA] == CREDENTIAL


def test_rerunning_for_one_member_replaces_only_that_member():
    """Re-running is the normal way to refresh a token, and it must not cost
    anyone else theirs."""
    existing = '{"sub-noah": {"refresh_token": "old"}, "sub-kendra": {"refresh_token": "kendras"}}'
    merged = setup.merge_member(existing, NOAH, CREDENTIAL)
    assert merged[NOAH] == CREDENTIAL
    assert merged[KENDRA] == {"refresh_token": "kendras"}


def test_the_existing_value_is_never_mutated_in_place():
    """The caller writes the returned object under a compare-and-set; the
    value it read must stay untouched so a failed write leaves no half-state."""
    original = {"sub-noah": {"refresh_token": "noahs"}}
    import json

    merged = setup.merge_member(json.dumps(original), KENDRA, CREDENTIAL)
    assert set(original) == {NOAH}
    assert set(merged) == {NOAH, KENDRA}


def test_unparseable_existing_credentials_refuse_the_write():
    """Better to make the operator look than to replace a blob we cannot read
    with one holding a single member."""
    with pytest.raises(ValueError, match="not valid JSON"):
        setup.merge_member("{not json", NOAH, CREDENTIAL)


@pytest.mark.parametrize("wrong", ['["a list"]', '"a string"', "42"])
def test_a_non_object_refuses_the_write(wrong):
    with pytest.raises(ValueError, match="not a JSON object"):
        setup.merge_member(wrong, NOAH, CREDENTIAL)
