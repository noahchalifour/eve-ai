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
credential, no permission data, no Kubernetes credentials of its own, and no
family roster data beyond the member subject identifiers that per-member
credentials are keyed by. That exception is real but narrow: `mail.*` calls
carry a `member_sub` across the boundary because Gmail tokens are per-member
(`src/eve_tools/gmail.py`), so `eve-tools` learns which opaque Authentik
subjects exist. It never learns their names, roles, timezones or
permissions. `NetworkPolicy` restricts its egress to exactly the external
hosts it needs. Permission checks happen in Eve's main container, before
the HTTP call, so a denied request never reaches `eve-tools` at all.

## Consequences

A misbehaving or compromised tool call can reach at most the three
external credentials `eve-tools` holds — not the cluster, not the family
roster or anyone's permissions, not the other credentials Eve's main
container has (LiteLLM, the database). The member subject identifiers that
cross the boundary are opaque and already the key `eve-tools` stores Gmail
tokens under; they are the boundary's one deliberate leak, not an oversight.
This refines ADR 0001 rather than reversing it: the rejection of a network
hop there was about the specialist *reasoning loop*, which still runs
in-process; a leaf tool call's HTTP hop to `eve-tools` lands after the
first streamed token and inside the same Langfuse trace either way, so
neither of ADR 0001's original objections applies to it. "One deploy"
becomes two: `eve-ai` and `eve-tools`, both built from this repository.
