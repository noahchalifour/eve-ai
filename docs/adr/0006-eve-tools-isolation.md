# 6. Specialist and skill tool execution runs in an isolated service

**Status:** Accepted
**Date:** 2026-08-21

## Context

Specialists and dynamically-discovered skills hold real third-party
credentials (Home Assistant, Gmail, Monarch Money) and take real-world
actions. ADR 0001 kept specialist *reasoning* in Eve's own process for
latency and tracing reasons. Whether specialist *tool execution* should
live there too is a separate question, and the risk profile differs: a
credential-holding leaf call is exactly the kind of thing whose blast
radius matters if a call goes wrong or a dependency is compromised.

## Decision

Every specialist tool and the generic MCP dispatcher call out to
`eve-tools`, a separate long-running service holding every third-party
credential, no family or permission data, and no Kubernetes credentials of
its own. `NetworkPolicy` restricts its egress to exactly the external
hosts it needs. Permission checks happen in Eve's main container, before
the HTTP call, so a denied request never reaches `eve-tools` at all.

## Consequences

A misbehaving or compromised tool call can reach at most the three
external credentials `eve-tools` holds — not the cluster, not family data,
not the other credentials Eve's main container has (LiteLLM, the database).
This refines ADR 0001 rather than reversing it: the rejection of a network
hop there was about the specialist *reasoning loop*, which still runs
in-process; a leaf tool call's HTTP hop to `eve-tools` lands after the
first streamed token and inside the same Langfuse trace either way, so
neither of ADR 0001's original objections applies to it. "One deploy"
becomes two: `eve-ai` and `eve-tools`, both built from this repository.
