# 1. Specialists are subgraph tools, not separate services

**Status:** Accepted
**Date:** 2026-08-17

## Context

Eve must feel like one person while domain specialists do the work. The
alternatives were separate Aegra assistants called over Agent Protocol, or
specialists exposed as MCP tool servers.

## Decision

One graph, one Aegra assistant. Specialists (Phase 3) are self-contained
subgraph modules with explicit interfaces, invoked by Eve as tools.

## Consequences

No network hop per specialist call, one Langfuse trace end to end, one
deploy. A specialist's dependencies share Eve's process, which at five users
is not a real risk. Because each specialist is a module with a declared
interface, promoting one to its own Aegra assistant later is a deployment
change rather than a rewrite.

Separate services were rejected because a network hop plus auth plus trace
stitching lands on every specialist call, contending directly with the
latency budget in ADR 0002. MCP tool servers were rejected because
specialists would lose their own agentic loop, and multi-step domain planning
degrades badly without it.
