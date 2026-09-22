"""`review.json` is model output, so it is untrusted input to a `gh` call.

This is the security checklist's own LLM05 (Improper Output Handling)
applied to ourselves.
"""

from __future__ import annotations

import json

import pytest

from eve_computer.acp import review as review_mod
from eve_computer.acp.review import InvalidFindings, build_body, load, split_comments


def _write(tmp_path, payload):
    (tmp_path / "review.json").write_text(json.dumps(payload))
    return tmp_path


def _valid():
    return {
        "summary": "Two findings.",
        "skipped_sections": ["performance: frontend (Python service)"],
        "findings": [
            {
                "severity": "critical", "axis": "security",
                "file": "src/eve/a.py", "line": 10, "body": "no permission check",
            },
            {
                "severity": "nit", "axis": "readability",
                "file": "src/eve/a.py", "line": 20, "body": "unclear name",
            },
        ],
    }


def test_a_well_formed_findings_file_loads(tmp_path):
    review = load(_write(tmp_path, _valid()))

    assert len(review["findings"]) == 2
    assert review["skipped_sections"] == ["performance: frontend (Python service)"]


def test_a_missing_file_is_a_failure_not_an_empty_review(tmp_path):
    """A review that silently degrades into 'no findings' is worse than no
    review, because it looks like a review."""
    with pytest.raises(InvalidFindings, match="no review.json"):
        load(tmp_path)


def test_malformed_json_is_rejected(tmp_path):
    (tmp_path / "review.json").write_text("{not json")

    with pytest.raises(InvalidFindings):
        load(tmp_path)


def test_an_unknown_severity_is_rejected(tmp_path):
    payload = _valid()
    payload["findings"][0]["severity"] = "catastrophic"

    with pytest.raises(InvalidFindings, match="severity"):
        load(_write(tmp_path, payload))


def test_an_unknown_axis_is_rejected(tmp_path):
    payload = _valid()
    payload["findings"][0]["axis"] = "vibes"

    with pytest.raises(InvalidFindings, match="axis"):
        load(_write(tmp_path, payload))


def test_a_finding_without_a_file_is_rejected(tmp_path):
    payload = _valid()
    del payload["findings"][0]["file"]

    with pytest.raises(InvalidFindings, match="file"):
        load(_write(tmp_path, payload))


def test_findings_must_be_a_list(tmp_path):
    with pytest.raises(InvalidFindings):
        load(_write(tmp_path, {"summary": "x", "findings": "none"}))


def test_a_review_with_no_findings_is_valid(tmp_path):
    """Clean code is a legitimate outcome, not a malformed file."""
    review = load(_write(tmp_path, {"summary": "Looks good.", "findings": []}))

    assert review["findings"] == []


def test_the_body_names_the_reviewing_agent_and_model(tmp_path):
    """The member can only check that a different model reviewed than
    implemented if it is written where they look."""
    body = build_body(_valid(), "claude", "anthropic/claude-sonnet-5")

    assert "claude" in body
    assert "anthropic/claude-sonnet-5" in body


def test_the_body_reports_counts_and_skipped_sections():
    body = build_body(_valid(), "claude", "m")

    assert "critical" in body.lower()
    assert "frontend" in body


def test_an_unanchorable_finding_is_demoted_rather_than_dropped():
    """GitHub rejects the whole review if one comment is unanchorable, so one
    bad line number must not cost the other findings."""
    changed = {"src/eve/a.py": {10}}

    anchorable, demoted = split_comments(_valid(), changed)

    assert len(anchorable) == 1
    assert anchorable[0]["line"] == 10
    assert len(demoted) == 1
    assert demoted[0]["line"] == 20


def test_a_finding_in_an_untouched_file_is_demoted():
    anchorable, demoted = split_comments(_valid(), {"src/eve/other.py": {10, 20}})

    assert anchorable == []
    assert len(demoted) == 2


def test_demoted_findings_appear_in_the_body():
    changed = {"src/eve/a.py": {10}}
    _anchorable, demoted = split_comments(_valid(), changed)

    body = build_body(_valid(), "claude", "m", demoted=demoted)

    assert "unclear name" in body


def test_severity_prefixes_use_the_rubrics_vocabulary():
    changed = {"src/eve/a.py": {10, 20}}
    anchorable, _demoted = split_comments(_valid(), changed)

    critical = next(c for c in anchorable if c["line"] == 10)
    nit = next(c for c in anchorable if c["line"] == 20)
    assert critical["body"].startswith("**Critical:**")
    assert nit["body"].startswith("**Nit:**")


def test_a_required_finding_carries_no_prefix():
    """The rubric's table: no prefix means required."""
    payload = _valid()
    payload["findings"] = [{
        "severity": "required", "axis": "correctness",
        "file": "src/eve/a.py", "line": 10, "body": "off by one",
    }]

    anchorable, _demoted = split_comments(payload, {"src/eve/a.py": {10}})

    assert anchorable[0]["body"] == "off by one"


def test_every_declared_severity_is_renderable():
    """A severity that loads but cannot render would fail at post time,
    after the tokens were already spent."""
    for severity in review_mod.SEVERITIES:
        payload = {
            "summary": "x",
            "findings": [{
                "severity": severity, "axis": "correctness",
                "file": "a.py", "line": 1, "body": "text",
            }],
        }
        anchorable, _ = split_comments(payload, {"a.py": {1}})
        assert anchorable[0]["body"].endswith("text")
