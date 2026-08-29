"""Shapes only. No behaviour, no I/O, no internal imports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ToolProposal:
    name: str
    description: str
    args_schema: dict
    source: str
    # Populated by inspect.check: which allowlisted modules the source
    # imports. Rendered in the interrupt payload so the approver's read is
    # short.
    imports: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    imports: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
