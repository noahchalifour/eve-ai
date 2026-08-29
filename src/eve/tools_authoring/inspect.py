"""The AST allowlist.

    NOT A SECURITY BOUNDARY. A determined bypass of an AST allowlist exists,
    and treating this as containment is a well-travelled way to get owned.

Its real jobs: give Eve a specific, actionable error so she can revise before
bothering a human, and make the approver's read short. Containment is the pod -
default-deny egress, no ServiceAccount token, no secret mounts, read-only root
filesystem (design section 6.3). Every guarantee in this phase must hold with
this module assumed defeated, and tests/test_sandbox_execute.py tests from that
assumption.

Pure: no I/O, so it is cheap to call at propose time and again at approve time.
"""

from __future__ import annotations

import ast

from eve.tools_authoring.types import CheckResult

# Parsing a URL is computation. Fetching one is not, which is why
# urllib.parse is here and urllib.request is not.
ALLOWED_IMPORTS = frozenset(
    {
        "json", "re", "math", "decimal", "statistics", "datetime", "zoneinfo",
        "itertools", "functools", "collections", "textwrap", "string",
        "dataclasses", "typing", "base64", "hashlib", "urllib.parse", "uuid",
    }
)

DENIED_NAMES = frozenset(
    {"eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars"}
)


def _import_allowed(dotted: str) -> bool:
    """`json.decoder` is fine if `json` is allowed; `urllib.request` is not
    allowed by `urllib.parse` being allowed."""
    if dotted in ALLOWED_IMPORTS:
        return True
    return any(
        dotted.startswith(f"{allowed}.")
        for allowed in ALLOWED_IMPORTS
        if "." not in allowed
    )


def check(source: str) -> CheckResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return CheckResult(ok=False, problems=[f"syntax error: {exc}"])

    problems: list[str] = []
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
                if not _import_allowed(alias.name):
                    problems.append(
                        f"import of {alias.name!r} is not allowed; a sandbox tool "
                        "is a pure function over data it is handed"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append(module)
            if not _import_allowed(module):
                problems.append(
                    f"import from {module!r} is not allowed; a sandbox tool is a "
                    "pure function over data it is handed"
                )
        elif isinstance(node, ast.Name) and node.id in DENIED_NAMES:
            problems.append(f"use of {node.id!r} is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(
                f"attribute access to {node.attr!r} is not allowed (dunder access)"
            )

    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    run = next((f for f in functions if f.name == "run"), None)
    if run is None:
        problems.append(
            "the source must define exactly one module-level function named "
            "'run' taking a single `arguments` dict"
        )
    elif len(run.args.args) != 1:
        problems.append(
            f"'run' must take exactly one parameter, not {len(run.args.args)}"
        )

    return CheckResult(
        ok=not problems, imports=sorted(set(imports)), problems=problems
    )
