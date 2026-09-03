# 16. The box runs the protocol, Eve holds the judgement

**Status:** Accepted
**Date:** 2026-09-01

## Context

EVE-4 gives Eve real conversations with coding agents over ACP. A
conversation needs someone deciding what an agent's turn *means*: whether
"which auth library do you want?" is a question to answer, a sign the task
was underspecified, or the last thing before it opens a pull request.

ACP offers no help here. `session/prompt` returns a `stop_reason` and
nothing else; "the turn ended" is the only fact on the wire. Something has
to classify it.

eve-computer's standing invariant is that the box learns nothing about the
family - no member subject, no roster, no permissions. That invariant is
what bounds the blast radius of a machine with a shell, passwordless sudo,
and Eve's own GitHub credential.

## Decision

The box records; Eve classifies.

A turn that ends leaves the session `idle` on the box, forever, whatever it
contains. Eve's container reads the turn and decides reply, done, or
escalate, with the goal, the member, the thread, and a recall snapshot in
front of it. Only the composed prompt text crosses back.

`eve_computer/acp/session.py` therefore contains no branch on what an agent
said, and adding one is the design going wrong.

## Consequences

**The supervisor needs its own tick.** An agent waiting on an answer cannot
wait 300 seconds, so the loop runs at ~20s inside eve-ambient, separate from
the ambient poll. Two loops in one process, with two different reasons to
exist.

**Recall is snapshotted, not repeated.** A hybrid recall every twenty
seconds per live session would be indefensible, so it is taken once at
dispatch and stored on the row. This is the cost of putting the judgement in
Eve's container, paid once per session instead of once per tick.

**Eve's session vocabulary is wider than the box's.** The box has `idle`;
Eve has `blocked`, which the box could never produce, because deciding a
question is unanswerable requires the member.

**The alternative was worse in both directions.** A supervisor loop on the
box would answer with no family context - the invariant working as intended,
producing a worse correspondent than the member who delegated the work.
Proxying raw ACP to Eve's container would give her `session/update` push,
but `session/update` is server-push, and accepting it inverts the
one-directional network rule the whole eve-computer safety argument rests
on.
