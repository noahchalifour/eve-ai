# 16. eve-tools owns one credential table

**Status:** Accepted
**Date:** 2026-09-01
**Amends:** [ADR 0006](0006-eve-tools-isolation.md)

## Context

ADR 0006 gave `eve-tools` "no permission data, no Kubernetes credentials of
its own, and no family roster data beyond the member subject identifiers
that per-member credentials are keyed by" — and, implicitly but centrally,
no database. Every third-party credential it held was either static (the
Home Assistant token) or non-rotating (Google refresh tokens), so
environment variables were sufficient storage and the service kept no
persistent state at all.

The health coach specialist breaks that. **WHOOP returns a new
`refresh_token` on every refresh** and the previous one cannot be relied on
afterwards. A rotating token in an environment variable is stale after its
first use: the next pod restart reads a dead value from the ExternalSecret
and auth is broken until a human re-runs the provisioning flow. There is no
version of "store it in the environment" that works.

Alternatives considered and rejected:

- **A writable file on a PVC.** Avoids a database role, but adds a volume,
  needs file locking the moment eve-tools has more than one replica, and
  the concurrency problem below is the hard part either way.
- **Keeping the rotated token in process memory.** Works until restart,
  then breaks permanently. Not a design.
- **Having Eve hold the token and pass it down per call.** Puts a
  third-party credential in Eve's container, which is the specific thing
  ADR 0006 exists to prevent, and the refresh call needs the client secret
  anyway.
- **Oura only, deferring WHOOP.** Real option, and it would have needed no
  new infrastructure. Rejected because both members' devices were in scope
  and deferring one of two providers is not delivering the feature.

## Decision

`eve-tools` gets a Postgres connection of its own, and exactly one table.

- The table is `eve_oauth_token`, keyed `(provider, member_sub)`. Its DDL
  lives in Eve's Alembic history (revision `0005_eve_oauth_token`, private
  `eve_alembic_version` table per ADR 0011). eve-tools has no DDL grant and
  never migrates.
- eve-tools connects via `EVE_TOOLS_DATABASE_URL`, a **separate connection
  string** resolving to a **dedicated Postgres role** granted
  `SELECT, INSERT, UPDATE` on `eve_oauth_token` and nothing else. No
  `DELETE`. No grant on `eve_memory`, `eve_pat`, `eve_tool`,
  `eve_computer_task`, or any Aegra table. Sharing Eve's connection string
  would hand eve-tools Eve's role and forfeit the whole point.
- `src/eve_tools/` continues to import nothing from `src/eve/`. It has its
  own pool in `src/eve_tools/db.py` rather than reusing
  `eve.memory.db.get_pool`.
- Token refresh is serialized by `SELECT ... FOR UPDATE` on the row, with
  the freshness check repeated inside the lock. This is a correctness
  requirement, not an optimization: two concurrent refreshes would each
  rotate the other's token away, leaving a stored credential the provider
  has already invalidated.

This ADR also corrects a detail of 0006's text. 0006 described
`member_sub` crossing the boundary as one narrow exception, for `mail.*`.
It is now two domains, `mail.*` and `health.*`. The identifiers remain
opaque; eve-tools still learns no names, roles, timezones, or permissions.
Notably, the health clients derive each record's local date from the
provider's own attribution (Oura's `day` string, WHOOP's
`timezone_offset`) specifically so that member timezones do **not** have to
cross the boundary.

## Consequences

ADR 0006's isolation claim weakens from "no database" to **"one table, its
own role, no read access to anything else."** That is a real reduction and
the reason this is a written amendment rather than an implementation
detail: a compromised eve-tools can now read and rewrite every family
member's health OAuth tokens. It still cannot reach the cluster, the family
roster, anyone's permissions, Eve's memory, or Eve's own credentials.

The blast radius grew by exactly the credentials eve-tools was always going
to hold — the tokens are for APIs it already calls. What is new is that they
are now durable and shared between replicas rather than injected per pod.

Two operational costs follow. eve-tools now needs network egress to the
CNPG cluster, so it can fail to start for a reason unrelated to any
third-party API. And the Postgres role and its grants are provisioned
out-of-band in `home-lab-infrastructure`; a deploy that ships this code
without them starts and then fails every health question with a connection
error.
