"""Permission enforcement at the tool boundary. `family.yaml`'s own comment has
said "enforced at the tool boundary in Phase 3" since Phase 1; this is that
boundary, applied twice per design doc section 8 - coarse at the eve ->
specialist edge (Task 4), fine inside a specialist's own tools (Task 6's
send_email).
"""

from __future__ import annotations


def permission_denial(permissions: list[str], required: str | list[str]) -> str | None:
    """`None` if any of `required` is held; otherwise a string meant to be
    returned directly as a tool's result, so the turn continues and Eve
    explains the boundary instead of the graph erroring out."""
    needed = [required] if isinstance(required, str) else required
    if any(permission in permissions for permission in needed):
        return None
    return f"Permission denied: this action requires {' or '.join(needed)}."
