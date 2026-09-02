"""The box's side of the Agent Client Protocol.

Deliberately ignorant of HTTP, git, and Eve: this file answers what the
*agent* calls back into, and nothing else. `session.py` composes it with
the rest.

Two decisions worth reading before changing anything here.

`request_permission` auto-approves. The design doc's "Oversight" section is
explicit that there is no per-action gate on this box - dispatching is the
gate, the pod and the NetworkPolicy are the boundary, and the pull request
is the review. An approval prompt over a control channel with nobody
listening is not a boundary, it is a hang. When the agent offers no
allow-shaped option at all we return `cancelled` rather than inventing one:
refusing a menu we were not given is the honest answer.

`_resolve` confines every `fs/*` path to the session root, symlinks
included. The agent already has a shell and can read what it likes with it;
the point is not to contain the machine (the pod does that) but to stop
`fs/*` quietly becoming a second, wider door than the worktree the session
was handed. `Path.resolve()` before the check is what makes `..` and
symlinks both fall out of one comparison.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from acp import Client
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    PermissionOption,
    ReadTextFileResponse,
    RequestPermissionResponse,
    WriteTextFileResponse,
)

logger = logging.getLogger(__name__)

_ALLOW_KINDS = ("allow_always", "allow_once")


class PathEscapedRoot(Exception):
    """An `fs/*` path resolved outside the session root."""


class SessionClient(Client):
    def __init__(self, root: Path, on_update: Callable[[Any], None]) -> None:
        self._root = Path(root).resolve()
        self._on_update = on_update

    def _resolve(self, path: str) -> Path:
        candidate = Path(path)
        absolute = candidate if candidate.is_absolute() else self._root / candidate
        # strict=False: a write to a file that does not exist yet still has to
        # be resolved and checked, not rejected for being new.
        resolved = absolute.resolve(strict=False)
        if resolved != self._root and self._root not in resolved.parents:
            raise PathEscapedRoot(f"{path!r} resolves outside the session root")
        return resolved

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self._on_update(update)

    async def request_permission(
        self,
        session_id: str,
        tool_call: Any,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        for option in options:
            if option.kind in _ALLOW_KINDS:
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(outcome="selected", option_id=option.option_id)
                )
        logger.warning("no allow-shaped permission option offered; denying")
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        text = self._resolve(path).read_text()
        if line is None and limit is None:
            return ReadTextFileResponse(content=text)
        lines = text.splitlines(keepends=True)
        start = (line - 1) if line else 0
        end = (start + limit) if limit else None
        return ReadTextFileResponse(content="".join(lines[start:end]))

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> WriteTextFileResponse:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return WriteTextFileResponse()
