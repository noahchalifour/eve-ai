"""Local, authoritative run storage and the regression gate.

The gate reads Postgres and never calls Langfuse, so a reporting outage cannot
block a regression check (eval design 7.1). Langfuse is where a human looks at
history; this is what decides an exit code.
"""

from __future__ import annotations

import subprocess

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from eve.eval.types import RunScore
from eve.memory.db import get_pool
from eve.settings import get_settings


def git_sha() -> str:
    """Which code produced a score. Without it, run-over-run comparison is
    meaningless the moment two commits land in one week."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


async def record_run(score: RunScore, sha: str | None = None) -> str:
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO eve_eval_run (dataset, arm, git_sha, item_count, scores)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (
                score.dataset, score.arm, sha or git_sha(),
                score.item_count, Jsonb(score.scores),
            ),
        )
        return str((await cur.fetchone())[0])


async def last_two(dataset: str, arm: str) -> list[dict]:
    """Newest first. The exact lookup eve_eval_run_dataset_created serves."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT arm, git_sha, item_count, scores, created_at"
                " FROM eve_eval_run WHERE dataset = %s AND arm = %s"
                " ORDER BY created_at DESC LIMIT 2",
                (dataset, arm),
            )
            return list(await cur.fetchall())


def evaluate_gate(runs: list[dict], previous: dict | None) -> tuple[int, list[str]]:
    """Exit code and human-readable reasons.

    Pure, so every threshold is unit-testable without a database.
    """
    points = get_settings().eval_regression_points
    reasons: list[str] = []
    failed = False

    for run in runs:
        scores = run.get("scores") or {}
        if not run.get("item_count"):
            reasons.append(f"{run.get('arm')}: skipped, the dataset is empty")
            continue

        if scores.get("canary_passed"):
            reasons.append(
                "canary_passed: the canary assertion passed, so the judge is "
                "rubber-stamping and no other score here can be trusted"
            )
            failed = True

        if "rule_delta" in scores and scores["rule_delta"] < 0:
            reasons.append(
                f"rule_delta: {scores['rule_delta']} - the authored rule set is "
                "making Eve worse on the golden turns"
            )
            failed = True

        if previous is None:
            continue
        old = previous.get("scores") or {}

        # Exact: a member receiving a notification they lack the permission
        # for is never noise.
        if "audience_exact" in scores and "audience_exact" in old:
            if scores["audience_exact"] < old["audience_exact"]:
                reasons.append(
                    f"audience_exact: {old['audience_exact']} -> "
                    f"{scores['audience_exact']} (any drop fails)"
                )
                failed = True

        for metric in ("notify_agreement", "assertion_pass"):
            if metric in scores and metric in old:
                drop = old[metric] - scores[metric]
                if drop > points:
                    reasons.append(
                        f"{metric}: {old[metric]} -> {scores[metric]} "
                        f"(dropped {round(drop, 1)} > {points})"
                    )
                    failed = True

    return (1 if failed else 0), reasons


async def gate(dataset: str, arm: str = "with-rules") -> tuple[int, list[str]]:
    runs = await last_two(dataset, arm)
    if not runs:
        return 0, [f"{dataset}: no runs recorded yet"]
    return evaluate_gate([runs[0]], runs[1] if len(runs) > 1 else None)
