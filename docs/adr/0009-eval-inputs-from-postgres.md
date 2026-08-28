# 9. Eval inputs come from Postgres, not from Langfuse traces

**Status:** Accepted
**Date:** 2026-08-27

## Context

The program spec calls for "an eval harness over Langfuse datasets."
Langfuse holds every trace already, so reconstructing eval inputs from traces
looks like the cheapest path.

It is not. A trace's span tree exists for human debugging, and its shape is set
by Aegra and LiteLLM rather than by this repository — it changes whenever
either is upgraded. Meanwhile `eve_memory`, `eve_ambient_notice` and their
siblings are tables this repository owns.

Phase 4's tables turned out not to hold enough: `eve_ambient_seen` keeps only
`(source, key)`, and `eve_ambient_notice` keeps no signal content, so a
replayable ambient item could not be reconstructed from either.

## Decision

Datasets are **built** from Eve's own Postgres tables and one hand-authored
golden file, and **published** to Langfuse. Scores are written to
`eve_eval_run` locally first; the Langfuse upload is best-effort and its
failure is logged and ignored. `eve-eval gate` reads only Postgres.

The corollary: a subsystem that wants to be evaluated must *record* its
decisions, not merely log them. This is why Phase 5b adds
`eve_ambient_decision` — one row per judged signal, carrying the whole
`Signal` — rather than parsing `pipeline.py`'s resolution log line.

## Consequences

The gate works with Langfuse down, which means a reporting outage can never
block a regression check. Langfuse keeps the one job the local table does not
do cheaply: run-over-run comparison in a UI nobody had to build.

The cost is a recording obligation on any subsystem entering the harness, and
the fact that every dataset is forward-looking from the deploy that starts
recording it. Shape 1 was empty on the day this phase shipped.
