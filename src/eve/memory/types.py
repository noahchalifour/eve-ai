"""Shapes only. No behaviour, no I/O, no internal imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

Layer = Literal["profile", "household", "episodic", "digest"]


@dataclass(frozen=True, slots=True)
class Memory:
    id: str
    layer: str
    scope_kind: str
    scope_id: str
    kind: str
    subject: str | None
    content: str
    confidence: float
    salience: float
    created_at: datetime
    last_seen_at: datetime


class MemoryBundle(TypedDict):
    """What `recall` puts in state and `build_system_prompt` renders."""

    profile: list[Memory]
    household: list[Memory]
    episodic: list[Memory]
    digest: str | None
    # Observability, not behaviour: whether the vector arm landed inside its
    # budget. Read by the span attributes in recall.py.
    vector_used: bool
    latency_ms: float


# Pydantic, not a dataclass: these are the structured-output schema handed to
# the REFLEX model in extract.py.
class Operation(BaseModel):
    op: Literal["add", "supersede", "reinforce", "forget"]
    target_id: str | None = Field(
        default=None, description="Existing memory id. Required except for `add`."
    )
    layer: Literal["profile", "household", "episodic"] | None = None
    kind: Literal["fact", "preference", "event", "decision"] | None = None
    subject: str | None = Field(
        default=None,
        description="Lowercase entity this is about: 'cooper', 'kendra', 'honda'.",
    )
    content: str | None = Field(
        default=None, description="ONE self-contained sentence."
    )


class Extraction(BaseModel):
    operations: list[Operation] = Field(default_factory=list)
