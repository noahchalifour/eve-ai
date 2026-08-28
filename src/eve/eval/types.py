"""Shapes only. No behaviour, no I/O, no internal imports."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DatasetItem:
    id: str
    shape: str  # "ambient" | "turns"
    input: dict
    expected: dict
    # A canary's assertion is written to FAIL against correct behaviour. A run
    # in which it passes means the judge is rubber-stamping, and the gate
    # fails on it (eval design 11).
    canary: bool = False


@dataclass(frozen=True, slots=True)
class ItemResult:
    item_id: str
    scores: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RunScore:
    dataset: str
    arm: str
    item_count: int
    scores: dict
