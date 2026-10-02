"""`run_shortcut`, and the prompt section that tells Eve what she has.

Every run re-checks, in this order: the shortcut exists and is active for
this member; the member still holds its permission (permission enforcement
never reads memory - Phase 5a design 6.1 - so a revoked grant revokes every
shortcut that needs it, instantly); the chosen option is one actually
observed; and the resolved call still passes its allowlist check. Only then
does it reach eve-tools.

A failed run tells Eve to fall back to the specialist in the same turn, so
the member still gets what they asked for.
"""

from __future__ import annotations

import logging
from time import perf_counter

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from opentelemetry import trace

from eve.settings import get_settings
from eve.shortcuts import allowlist, store
from eve.specialists.permissions import permission_denial

logger = logging.getLogger(__name__)


def _member(config: RunnableConfig) -> dict:
    return (config.get("configurable") or {}).get("member") or {}


def _fallback(specialist: str, why: str) -> str:
    return (
        f"Shortcut failed ({why}). Do not retry it: handle the request with "
        f"ask_{specialist} instead, now, in this same turn."
    )


@tool
async def run_shortcut(name: str, config: RunnableConfig, option: str | None = None) -> str:
    """Run one of your learned shortcuts (listed in your instructions under
    "Your shortcuts") instead of asking a specialist. `option` is required
    when the shortcut lists options, and must be one of them. If the result
    says the shortcut failed, use the specialist instead."""
    member = _member(config)
    span = trace.get_current_span()
    span.set_attribute("eve.shortcut.name", name)
    started = perf_counter()
    try:
        row = await store.active_by_name(member.get("sub", ""), name)
    except Exception as exc:
        logger.warning("run_shortcut lookup failed", exc_info=True)
        return f"error: {exc.__class__.__name__}"
    if row is None:
        return f"There is no shortcut named {name!r}. Use the matching specialist instead."

    denial = permission_denial(member.get("permissions") or [], row["permission"])
    if denial:
        span.set_attribute("eve.shortcut.permission_denied", True)
        return denial

    action = allowlist.lookup(row["specialist"], row["tool"])
    if action is None:
        return _fallback(row["specialist"], "it is no longer an allowed action")
    args = dict(row["fixed_args"] or {})
    if row["variant_key"]:
        options = [str(v) for v in row["variants"] or []]
        if option is None and len(options) == 1:
            option = options[0]
        if option is None or str(option) not in options:
            return (
                f"error: option must be one of {', '.join(options)} for {name!r}"
            )
        value: object = option
        if row["variant_key"] == "days":
            value = int(option)
        args[row["variant_key"]] = value
    error = action.check(args)
    if error is not None:
        return _fallback(row["specialist"], error)

    result = await action.run(args, member)
    elapsed = round((perf_counter() - started) * 1000, 1)
    span.set_attribute("eve.shortcut.latency_ms", elapsed)
    if result.strip().lower().startswith("error"):
        span.set_attribute("eve.shortcut.failed", True)
        try:
            retired = await store.record_failure(row["id"], get_settings().shortcut_max_failures)
        except Exception:
            logger.warning("could not record a shortcut failure", exc_info=True)
            retired = False
        if retired:
            logger.info("shortcut %s retired after repeated failures", row["id"])
        return _fallback(row["specialist"], result.strip()[:200])

    span.set_attribute("eve.shortcut.hit", True)
    try:
        await store.record_hit(row["id"])
    except Exception:
        logger.warning("could not record a shortcut hit", exc_info=True)
    return result


def render(shortcuts: list[dict]) -> str:
    """The system-prompt section. Empty when there are none."""
    if not shortcuts:
        return ""
    lines = []
    for row in shortcuts:
        action = allowlist.lookup(row["specialist"], row["tool"])
        if action is None:
            continue
        options = [str(v) for v in row.get("variants") or []]
        what = action.describe(row["fixed_args"] or {}, options)
        said = ""
        if row.get("phrasings"):
            said = f' - e.g. "{row["phrasings"][0]}"'
        opt = f" (option: {' | '.join(options)})" if row.get("variant_key") and options else ""
        lines.append(f"- {row['name']}{opt}: {what}{said}")
    if not lines:
        return ""
    return (
        "\n## Your shortcuts\n"
        "Requests this member makes often, already resolved. When a request "
        "plainly matches one, call run_shortcut instead of the specialist - "
        "it is much faster. If nothing matches exactly, use the specialist.\n"
        + "\n".join(lines)
        + "\n"
    )
