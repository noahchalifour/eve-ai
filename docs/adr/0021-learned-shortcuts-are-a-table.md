# 21. Learned shortcuts are a table, not a memory layer

**Status:** Accepted
**Date:** 2026-10-02
**Relates to:** [ADR 0001](0001-agents-as-subgraph-tools.md), [ADR 0002](0002-no-llm-before-first-token.md), [ADR 0008](0008-authored-behaviour-is-memory.md), [ADR 0012](0012-extraction-is-detached-and-joined.md)

## Context

ENG-296: "turn off the living room lights" costs Eve (VOICE) -> `ask_home` ->
a MECHANICAL agent loop (`list_entities`, `call_service`, answer) -> Eve
phrasing the reply. At least four model calls and two eve-tools round trips,
every time, for a request whose resolved form never changes.

ADR 0001 keeps specialists as opaque agent loops, and that stays right for
the open-ended case. What is missing is a way to stop paying for the loop
when its outcome is already known.

## Decision

When a specialist run reduces to exactly one call from a closed, code-defined
allowlist (`eve.shortcuts.allowlist.FAST_ACTIONS`: one-tap home actions,
home state reads, health reads), that call is recorded as an observation.
Three uncontradicted observations of the same call within 30 days promote it
to a **shortcut**: a row in `eve_shortcut`. Eve sees her live shortcuts in
the system prompt and calls them with one `run_shortcut` call, which goes
straight to eve-tools.

**A table, not an `eve_memory` layer**, despite ADR 0008's precedent for
rules and procedures. Those are prose Eve reads; a shortcut is structured
(tool, fixed arguments, the observed options), counted (observations, hits,
failures), and *executed*. Embedding, decay and salience mean nothing for it,
and rendering it as a sentence would invite the model to improvise the call.

**No new capability.** A shortcut only replays a call the member already got
through a specialist, under the same permission, with its fixed arguments.
`run_shortcut` re-checks the member's permission and the allowlist on every
run, so revoking a grant revokes every shortcut needing it with no cleanup.
Only option values actually observed are legal (`turn_on`/`turn_off`, never
a guessed `toggle`).

**ADR 0002 holds.** There is no router or classifier before the first token.
Shortcuts are loaded in `recall`'s existing database pass and rendered into
the prompt; Eve's own VOICE model picks `run_shortcut` the way it picks any
tool. Observation is recorded in a detached task (the ADR 0012 pattern), so
learning never delays an answer.

**Not learned unsupervised in the sense the README forbids.** Nothing is
inferred: a shortcut is the literal call a member's own request produced,
three times. Ambient and routine turns never count, nor does a turn that
read the web. A member opening the next turn with a correction ("no, the
kitchen") retracts the last observation on that thread. `eve-shortcut
revoke` removes one permanently; a revoked fingerprint is never re-learned.

## Consequences

- A repeated one-tap request becomes one VOICE call plus one eve-tools call.
- A shortcut that starts failing (an entity renamed in Home Assistant) tells
  Eve to use the specialist in the same turn, and retires itself after three
  consecutive failures. A retired fingerprint can be re-learned.
- Widening the allowlist is a code change and a review, never a setting.
  Confirm-risk actions (locks, covers), anything that sends or spends, and
  calls carrying service data are excluded by construction.
- Off by default (`EVE_SHORTCUTS_ENABLED`).
