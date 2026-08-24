# 7. Ambient runs impersonate family members through one scoped token

**Status:** Accepted
**Date:** 2026-08-23

## Context

A proactive message has to land somewhere the member can reply. Aegra scopes
threads to `user.identity` and does so before any handler in `src/eve/auth.py`
runs, which is why a cross-member read returns 404 rather than 403. So a
thread a member can open and answer in must be created *as that member* —
there is no "create on behalf of" in the Agent Protocol, and no way to hand a
thread over afterwards.

`eve-ambient` runs unattended. It has no member sitting in front of it to
authenticate, and the members it acts for are four people in one household.

## Decision

One shared secret, `EVE_AMBIENT_TOKEN`, plus an `x-eve-on-behalf-of` header.
`src/eve/auth.py` accepts that pair as an additional credential — not a third
`EVE_AUTH_MODE`, because production runs `oidc` and this has to work there —
and resolves the principal to the named roster member, with that member's
permissions.

Guardrails, all in `auth.py` and `settings.py`:

- `compare_digest`, not `==`.
- A token under 32 characters is refused at startup, beside the existing rule
  that refuses `dev` auth in production.
- The subject must exist in `family.yaml`.
- The header is inert on every other auth path: a member's own token carrying
  `x-eve-on-behalf-of` still authenticates as that member. There is a unit
  test and an integration test whose only job is to hold that true.
- Every use logs the impersonated subject.

## Consequences

The credential is issued to exactly two pods: `eve-ambient`, which presents
it, and `eve`, which verifies it. That is a wider blast radius than a
per-member credential would have, and it is stated plainly rather than hidden:
whoever holds this secret can act as any family member.

It also means ambient turns carry the member's own permissions, which is what
bounds Phase 4's "ambient turns may act" decision — ambient adds initiative,
not capability.

## Alternatives considered

- **Per-member Authentik service accounts.** Four OAuth clients, four
  secrets, and a token cache, to buy separation between four people who share
  a house and a budget. Rejected as ceremony.
- **Signed requests (HMAC or asymmetric).** Removes the shared secret at the
  cost of a key distribution mechanism this lab does not have. Worth
  revisiting only if something outside the household ever needs to create
  threads.
- **ntfy-only, no threads.** Cheapest, and it loses the reply-in-place
  behaviour that makes a proactive message a conversation rather than an
  alert.
