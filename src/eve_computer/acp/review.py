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
