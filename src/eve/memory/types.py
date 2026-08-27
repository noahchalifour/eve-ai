"""Shapes only. No behaviour, no I/O, no internal imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

# "rule" and "procedure" are Phase 5a's Eve-authored layers. `layer` is an
# unconstrained text column in Postgres, so widening this alias is the whole
# schema change - see the design doc section 3.
Layer = Literal[
    "profile", "household", "episodic", "digest", "rule", "procedure"
]


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
    source_thread: str | None = None
    source_run: str | None = None


class MemoryBundle(TypedDict):
    """What `recall` puts in state and `build_system_prompt` renders."""

    profile: list[Memory]
    household: list[Memory]
    episodic: list[Memory]
    # Phase 5a: Eve's own notes on how to behave. Always present, empty when
    # EVE_SELF_AUTHORING_ENABLED is off, so every consumer can read the key
    # unconditionally.
    rules: list[Memory]
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
    # No "procedure": a procedure is authored deliberately through
    # write_skill, never proposed by the REFLEX extraction pass.
    layer: Literal["profile", "household", "episodic", "rule"] | None = None
    kind: Literal["fact", "preference", "event", "decision"] | None = None
    subject: str | None = Field(
        default=None,
        description="Lowercase entity this is about: 'cooper', 'kendra', 'honda'.",
    )
    content: str | None = Field(
        default=None, description="ONE self-contained sentence."
    )
    # Only meaningful for layer="rule". A household rule applies to the whole
    # family, so it needs memory.write_shared; _resolve_scope downgrades it to
    # member scope without that permission.
    shared: bool = Field(
        default=False,
        description="For a rule: true if it applies to the whole family.",
    )


class Extraction(BaseModel):
    operations: list[Operation] = Field(default_factory=list)
