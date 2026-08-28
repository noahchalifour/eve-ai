"""`eve-eval`: build datasets, run them, gate on regressions, report hygiene.

Runs on demand and weekly via a CronJob. Not in CI: the calls are paid and
nondeterministic, so wiring this to block merges buys flaky builds and a
budget bill (eval design 2.1).
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys

from eve.eval import hygiene as hygiene_mod
from eve.eval.datasets import build_ambient, build_turns
from eve.eval.publish import publish_run
from eve.eval.replay import replay_ambient, replay_turn, voice_call_estimate
from eve.eval.scorers import judge_assertion, rule_delta, score_ambient, score_turns
from eve.eval.store import gate, record_run
from eve.eval.types import RunScore
from eve.memory.db import close_pool
from eve.memory.store import load_always_on
from eve.settings import get_settings

_TURNS_FILE = "tests/eval/turns.yaml"
_SPOT_CHECK = 10


def check_ceiling(estimate: int, yes: bool) -> None:
    ceiling = get_settings().eval_voice_call_ceiling
    print(f"estimated VOICE-tier calls: {estimate} (ceiling {ceiling})")
    if estimate > ceiling and not yes:
        raise SystemExit(
            f"refusing to make {estimate} VOICE-tier calls without --yes. "
            "Both subscription proxies share a 30-day budget with your own work."
        )


async def _run_ambient(limit: int | None) -> tuple[RunScore, list, dict]:
    items = await build_ambient(limit)
    results = {item.id: await replay_ambient(item) for item in items}
    scores = score_ambient(items, results)
    return (
        RunScore("ambient", "with-rules", len(items), scores),
        items,
        results,
    )


async def _run_turns(arm: str) -> tuple[RunScore, list, dict, list[str]]:
    items = build_turns(_TURNS_FILE)
    judged: dict[str, list] = {}
    spot: list[str] = []
    for item in items:
        response = await replay_turn(item, suppress_rules=(arm == "without-rules"))
        verdicts = [
            await judge_assertion(assertion, response)
            for assertion in item.expected["expects"]
        ]
        judged[item.id] = verdicts
        for verdict in verdicts:
            spot.append(f"[{'PASS' if verdict.passed else 'FAIL'}] {item.id}: {verdict.why}")
    scores = score_turns(items, judged)
    return RunScore("turns", arm, len(items), scores), items, judged, spot


async def _cmd_run(args) -> int:
    items = build_turns(_TURNS_FILE)
    check_ceiling(voice_call_estimate(items, arms=2), args.yes)

    ambient_score, ambient_items, ambient_results = await _run_ambient(args.limit)
    await record_run(ambient_score)
    await publish_run(
        "ambient", "with-rules", ambient_items, ambient_results, ambient_score.scores
    )
    print(f"ambient: {ambient_score.scores}")

    with_score, with_items, _wj, spot = await _run_turns("with-rules")
    without_score, _oi, _oj, _os = await _run_turns("without-rules")
    delta = rule_delta(with_score.scores, without_score.scores)
    with_score = RunScore(
        "turns", "with-rules", with_score.item_count,
        {**with_score.scores, "rule_delta": delta},
    )
    await record_run(with_score)
    await record_run(without_score)
    await publish_run("turns", "with-rules", with_items, {}, with_score.scores)

    print(f"turns with-rules:    {with_score.scores}")
    print(f"turns without-rules: {without_score.scores}")
    print(f"rule_delta:          {delta:+}")
    print("\n-- judge spot check (read these; the tier choice depends on it) --")
    for line in random.sample(spot, min(_SPOT_CHECK, len(spot))):
        print(f"  {line}")
    return 0


async def _cmd_gate(args) -> int:
    code = 0
    for dataset, arm in (("ambient", "with-rules"), ("turns", "with-rules")):
        this_code, reasons = await gate(dataset, arm)
        for reason in reasons:
            print(f"{dataset}/{arm}: {reason}")
        code = code or this_code
    print("GATE: " + ("FAIL" if code else "PASS"))
    return code


async def _cmd_build(args) -> int:
    ambient = await build_ambient(args.limit)
    turns = build_turns(_TURNS_FILE)
    print(f"ambient items: {len(ambient)}")
    print(f"turn items:    {len(turns)}")
    if not ambient:
        print(
            "note: shape 1 is empty. Decisions are recorded from this deploy "
            "forward only; the first useful precision number is weeks away."
        )
    return 0


async def _cmd_hygiene(args) -> int:
    settings = get_settings()
    _p, _h, _d, rules = await load_always_on(
        args.member, None, include_rules=True
    )
    dead = hygiene_mod.find_dead(rules, settings.eval_dead_rule_days)
    conflicts = await hygiene_mod.report_contradictions(rules)

    print(f"{len(rules)} live rules for {args.member}")
    for rule in dead:
        print(f"  dormant: {rule.id} {rule.content[:80]}")
    for conflict in conflicts:
        print(f"  CONFLICT (report only): {conflict}")
    print(
        "duplicate detection needs embeddings; run with --apply once "
        "EVE_EVAL_HYGIENE_APPLY_ENABLED is set to act on them."
        if not args.apply
        else ""
    )
    if args.apply and not settings.eval_hygiene_apply_enabled:
        print("--apply is inert: EVE_EVAL_HYGIENE_APPLY_ENABLED is false")
    # Pruning rides this command so the weekly CronJob does it in one place.
    from eve_ambient.store import prune_decisions

    pruned = await prune_decisions(settings.eval_decision_retention_days)
    print(f"pruned {pruned} decision rows beyond the retention window")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    builder = sub.add_parser("build", help="report dataset sizes")
    builder.add_argument("--limit", type=int, default=None)

    runner = sub.add_parser("run", help="replay and score both datasets")
    runner.add_argument("--limit", type=int, default=None)
    runner.add_argument("--yes", action="store_true", help="proceed past the call ceiling")

    sub.add_parser("gate", help="exit non-zero on a regression")

    hyg = sub.add_parser("hygiene", help="report redundant, conflicting, dormant rules")
    hyg.add_argument("--member", required=True)
    hyg.add_argument("--apply", action="store_true")

    args = parser.parse_args()
    handlers = {
        "build": _cmd_build, "run": _cmd_run,
        "gate": _cmd_gate, "hygiene": _cmd_hygiene,
    }

    async def _run() -> int:
        try:
            return await handlers[args.command](args)
        finally:
            await close_pool()

    sys.exit(asyncio.run(_run()))
