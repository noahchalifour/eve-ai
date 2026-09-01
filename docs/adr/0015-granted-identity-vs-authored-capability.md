# 15. A granted identity is not authored credentialed capability

**Status:** Accepted
**Date:** 2026-08-28

## Context

The README's permanent boundaries include "Eve does not author credentialed
capability" - a tool needing a secret is an `eve-tools` handler, written by a
human, forever. `eve-computer` gives Eve a persistent machine, her own
accounts, and real logins acting unattended. Read literally, that is
credentialed capability, and this document has to say why the boundary still
holds rather than pretend the tension away.

## Decision

The revision is narrower than it first appears, and rests on three
properties: (1) the credentials are hers, not the family's - her own Google
account, her own GitHub, granted access the same way a human assistant would
be onboarded, revoked with a checkbox rather than a code change; (2) a human
provisions every one of them, logged in once, by hand, over VNC - Eve cannot
create an account or obtain a credential herself; (3) the blast radius is
recoverable - a maximally bad day costs her own accounts and some compute,
never the family roster, anyone's permissions, or any of the other services'
credentials, because the pod's `NetworkPolicy` cannot reach any of them.

The *shape* of the original boundary is unchanged: Eve still does not author
credentialed capability, because she authors nothing here - a human granted a
bounded identity to a bounded machine. What changed is that "actions Eve can
take" is no longer categorically off the table; it is on the table exactly to
the extent that a wrong outcome is recoverable.

`eve-computer` is a new service beside `eve-sandbox`, not a replacement for
it or a change to its contract. ADR 0010's argument - "one service satisfying
both satisfies neither" - applies here by the same symmetry it used to
separate `eve-tools` from Eve's main container: `eve-sandbox` holds nothing
and runs machine-authored pure functions with no network; `eve-computer`
persists files, browses the web, and holds login sessions. Their invariants
are opposite, so they stay two services.

## Consequences

A maximally malicious task on `eve-computer` can spend her dedicated LiteLLM
budget and misuse her own accounts - not the family's Google/GitHub/anything
else, not the cluster, not Postgres, not any other service's credential,
because the pod cannot reach any of them (verified by
`tests/test_computer_live.py`). "Eve does not learn unsupervised" is
similarly narrowed rather than broken: the worker maintains its own
`AGENTS.md` on the box's disk, which is unsupervised learning bounded to
operating her own machine - it has no route into her persona, her authored
rules, or her behaviour toward any family member, all of which still come
from a specific turn with a specific member.
