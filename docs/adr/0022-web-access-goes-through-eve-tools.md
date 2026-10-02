# 22. Web access goes through eve-tools

**Status:** Accepted
**Date:** 2026-10-02
**Relates to:** [ADR 0006](0006-eve-tools-isolation.md), [ADR 0008](0008-authored-behaviour-is-memory.md), [ADR 0015](0015-granted-identity-vs-authored-capability.md)

## Context

ENG-372 gives Eve `web_search` (the cluster's self-hosted SearXNG) and
`fetch_url` (read a public page). Eve's own pod has no general egress, by
design. Fetching an arbitrary URL is also the classic server-side request
forgery shape: the URL is model-chosen, often member-pasted, and the fetcher
sits inside a cluster full of services that trust their network.

Three places could host it: Eve's pod, a new dedicated fetcher pod, or
eve-tools.

## Decision

**eve-tools**, as two ordinary handlers (`web.search`, `web.fetch`). Eve keeps
egress to eve-tools only; eve-tools' `NetworkPolicy` gains egress to
SearXNG and to the public internet, with cluster and private CIDRs still
denied.

eve-tools holds the family's credentials, so the fetch handler is built to
never touch them:

- **Its own client, per call.** `eve_tools.web` constructs a fresh
  `httpx.AsyncClient` with only a User-Agent and Accept header. No cookie
  jar survives a call, no auth is ever set, and no credentialed module's
  client is reused. A test pins every client the fetch builds.
- **SSRF guard with pinned connections.** The host is resolved by the guard
  and every address must be public (not private, loopback, link-local,
  multicast, reserved, or IPv4-mapped private). The connection then goes to
  the address the guard checked, with the name kept for Host and TLS SNI, so
  a second DNS answer cannot redirect it (rebinding). Cluster names
  (`*.svc`, `*.cluster.local`, `*.local`, bare hostnames) are refused before
  lookup. Redirects are followed by hand, at most five, each hop guarded.
- **Bounded.** 10 s timeout, 5 MB download, HTML/text/JSON/PDF only, text
  truncated server-side with an explicit flag.

On Eve's side, fetched text arrives wrapped as untrusted content, and a turn
that called a web tool may not author a rule, a procedure, a routine or a
reminder (`eve.state.turn_read_web`) - the Phase 5a rule that tool results
are not authoring input, made concrete for the most attacker-reachable tool
Eve has.

## Alternatives

- **Eve's pod**: would give the pod that runs the model general egress. No.
- **A dedicated fetcher pod**: the smallest blast radius, and the right
  answer if eve-tools' credentials ever become reachable from a handler. It
  was the spec's first suggestion; it was not chosen because it is another
  deployment, image and secret for a handler that, as built, shares nothing
  with the credentialed code paths. Revisit if that stops being true.

## Consequences

- One `NetworkPolicy` change in home-lab-infrastructure, plus a SearXNG
  deployment and `EVE_TOOLS_SEARXNG_URL`.
- `EVE_WEB_ENABLED` (default off) and a per-member `web` grant gate both
  tools.
