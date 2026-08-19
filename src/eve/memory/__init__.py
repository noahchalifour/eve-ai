"""Eve's memory (Phase 2).

Four layers - profile, household, episodic, digest - in one table,
distinguished by retrieval policy rather than by shape. See
docs/superpowers/specs/2026-08-18-eve-memory-design.md.

Import from this module, not from its submodules.
"""

from eve.memory.extract import extract
from eve.memory.recall import recall
from eve.memory.types import Memory, MemoryBundle

__all__ = ["Memory", "MemoryBundle", "extract", "recall"]
