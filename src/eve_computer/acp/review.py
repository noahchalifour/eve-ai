"""`review.json` in, a GitHub review body out.

WHY THIS VALIDATES RATHER THAN TRUSTS. The findings file is model output.
The security checklist this very feature reviews against says to treat model
output as untrusted and to validate it at the boundary (LLM05, Improper
Output Handling), and the boundary is here: everything downstream of this
module is an HTTP call to GitHub under Eve's identity.

WHY AN UNANCHORABLE FINDING IS DEMOTED RATHER THAN DROPPED. GitHub rejects
an entire review if any one inline comment names a line outside the diff. A
reviewer that miscounts one line number must not cost the other twenty-nine
findings, and silently dropping it would hide a real finding.
"""

from __future__ import annotations

import json
from pathlib import Path

SEVERITIES: tuple[str, ...] = ("critical", "required", "optional", "nit", "fyi")
AXES: tuple[str, ...] = (
    "correctness", "readability", "architecture", "security", "performance",
)

# The rubric's own table: no prefix means required, so the author can tell
# what is mandatory from what is taste.
_PREFIX: dict[str, str] = {
    "critical": "**Critical:** ",
    "required": "",
    "optional": "**Optional:** ",
    "nit": "**Nit:** ",
    "fyi": "**FYI:** ",
}

_FILE_NAME = "review.json"


class InvalidFindings(Exception):
    """`review.json` is absent, unparseable, or does not say what it must."""


def load(session_dir: Path) -> dict:
    path = Path(session_dir) / _FILE_NAME
    if not path.is_file():
        raise InvalidFindings(f"no {_FILE_NAME} in {session_dir}")
    try:
        review = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise InvalidFindings(f"{_FILE_NAME} could not be read: {exc}") from exc

    if not isinstance(review, dict):
        raise InvalidFindings(f"{_FILE_NAME} is not an object")
    findings = review.get("findings")
    if not isinstance(findings, list):
        raise InvalidFindings(f"{_FILE_NAME} has no findings list")

    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise InvalidFindings(f"finding {index} is not an object")
        if finding.get("severity") not in SEVERITIES:
            raise InvalidFindings(
                f"finding {index} has severity {finding.get('severity')!r};"
                f" expected one of {', '.join(SEVERITIES)}"
            )
        if finding.get("axis") not in AXES:
            raise InvalidFindings(
                f"finding {index} has axis {finding.get('axis')!r};"
                f" expected one of {', '.join(AXES)}"
            )
        if not finding.get("file"):
            raise InvalidFindings(f"finding {index} names no file")
        if not finding.get("body"):
            raise InvalidFindings(f"finding {index} has no body")

    review.setdefault("summary", "")
    review.setdefault("skipped_sections", [])
    return review


def _rendered(finding: dict) -> str:
    return f"{_PREFIX[finding['severity']]}{finding['body']}"


def split_comments(
    review: dict, changed_lines: dict[str, set[int]]
) -> tuple[list[dict], list[dict]]:
    """`(anchorable, demoted)`. Anchorable comments become inline review
    comments; demoted ones ride in the summary body."""
    anchorable: list[dict] = []
    demoted: list[dict] = []
    for finding in review["findings"]:
        line = finding.get("line")
        lines = changed_lines.get(finding["file"], set())
        if isinstance(line, int) and line in lines:
            anchorable.append({
                "path": finding["file"],
                "line": line,
                "side": "RIGHT",
                "body": _rendered(finding),
            })
        else:
            demoted.append({**finding, "line": line})
    return anchorable, demoted


def build_body(
    review: dict, agent: str, model: str, demoted: list[dict] | None = None
) -> str:
    counts: dict[str, int] = {}
    for finding in review["findings"]:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    tally = ", ".join(f"{n} {s}" for s, n in counts.items()) or "no findings"

    lines = [
        review.get("summary", ""),
        "",
        f"**Findings:** {tally}.",
    ]
    skipped = review.get("skipped_sections") or []
    if skipped:
        lines.append(f"**Rubric sections skipped:** {'; '.join(skipped)}.")
    if demoted:
        lines.append("")
        lines.append("**Findings that could not be anchored to a changed line:**")
        for f in demoted:
            line_no = f.get("line")
            line_suffix = f":{line_no}" if isinstance(line_no, int) else ""
            lines.append(f"- `{f['file']}`{line_suffix}: {_rendered(f)}")
    lines.extend([
        "",
        f"<sub>Reviewed by `{agent}` / `{model}`. This review is advisory and "
        f"carries no merge authority.</sub>",
    ])
    return "\n".join(lines)


_FOLLOWUP_FILE = "followup.json"
# GitHub's own comment ceiling is 65536 characters. Well under it, because a
# reply longer than this is not a reply a reviewer will read.
_MAX_REPLY_CHARS = 8000


def load_followup(session_dir: Path, feedback: list[dict]) -> dict:
    """`followup.json` from an address session (EVE-31), validated.

    The same posture as `load`: this is model output about to be posted
    under Eve's identity, so it is untrusted input to `gh`. A reply may only
    name an inline comment the box itself fetched and gave the agent, so a
    confused or manipulated agent cannot reply into some other thread; one
    naming anything else fails the session rather than being posted or
    silently dropped.
    """
    path = Path(session_dir) / _FOLLOWUP_FILE
    if not path.is_file():
        raise InvalidFindings(f"no {_FOLLOWUP_FILE} in {session_dir}")
    try:
        followup = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise InvalidFindings(f"{_FOLLOWUP_FILE} could not be read: {exc}") from exc
    if not isinstance(followup, dict):
        raise InvalidFindings(f"{_FOLLOWUP_FILE} is not an object")

    summary = followup.get("summary", "")
    if not isinstance(summary, str) or len(summary) > _MAX_REPLY_CHARS:
        raise InvalidFindings(f"{_FOLLOWUP_FILE} summary is not a short string")

    replies = followup.get("replies", [])
    if not isinstance(replies, list):
        raise InvalidFindings(f"{_FOLLOWUP_FILE} replies is not a list")
    known = {
        item["id"] for item in feedback
        if item.get("kind") == "review_comment" and isinstance(item.get("id"), int)
    }
    for index, reply in enumerate(replies):
        if not isinstance(reply, dict):
            raise InvalidFindings(f"reply {index} is not an object")
        comment_id = reply.get("comment_id")
        if not isinstance(comment_id, int) or isinstance(comment_id, bool) \
                or comment_id not in known:
            raise InvalidFindings(
                f"reply {index} names comment {comment_id!r}, which is not an"
                " inline review comment in feedback.json"
            )
        body = reply.get("body")
        if not isinstance(body, str) or not body.strip() or len(body) > _MAX_REPLY_CHARS:
            raise InvalidFindings(f"reply {index} has no usable body")

    return {"summary": summary, "replies": replies}
