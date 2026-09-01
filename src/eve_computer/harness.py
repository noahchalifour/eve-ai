"""Drives one task through claude-agent-sdk. Everything downstream of
run_task only needs a goal in and a {"summary"|"error", "artifacts"} out
(design doc: "The swap seam is the task API itself") - a second driver later
is one new function, not a rewrite of app.py.

Verified against the installed claude-agent-sdk==0.2.149 (see
batch-D-report.md for the full investigation). Notable deviations from the
plan's guessed API:

- query() only yields dataclass instances (UserMessage/AssistantMessage/
  SystemMessage/ResultMessage/StreamEvent/...); only ResultMessage carries
  `.result`/`.is_error`/`.errors`, so those are checked with an explicit
  isinstance() rather than getattr(message, "result", None) against every
  message.
- ClaudeAgentOptions has no base_url field - the CLI subprocess reads
  ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY from its own environment, and
  ClaudeAgentOptions.env layers on top of (and overrides) the inherited
  process environment for exactly that subprocess, so the litellm routing
  is passed via `options.env` instead of mutating os.environ globally.
- ClaudeAgentOptions.permission_mode="bypassPermissions" is set explicitly:
  the design doc's "Oversight" section is explicit that there is no
  per-action approval gate here, and the default permission_mode otherwise
  prompts (over a control channel with no one listening) instead of running.
- Nothing in claude-agent-sdk itself enforces a wall-clock timeout; the
  plan's own settings.task_timeout_seconds field was otherwise unused, so
  the query loop is wrapped in asyncio.wait_for to actually enforce it
  (design doc "Bounds": "Per-task maximum turns and wall-clock timeout").
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from eve_computer.gui_tool import computer_use_server
from eve_computer.settings import get_computer_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You operate a real Linux desktop on behalf of a family member. Use the "
    "shell, the browser, and the computer tool to complete the goal below. "
    "Write anything the family should see - a file, a summary, a screenshot "
    "- into ./out/. You are not Eve; do not speak in her voice, and do not "
    "address the family member directly - your final message is a report to "
    "her, not to them."
)


async def _run(goal: str, options: ClaudeAgentOptions) -> ResultMessage | None:
    """Drive the query loop to completion and return its terminal ResultMessage.

    A task can, in principle, run more than one internal turn/subagent
    without necessarily reporting more than one top-level ResultMessage;
    the last one observed is authoritative for the outer task's outcome.
    """
    final: ResultMessage | None = None
    async for message in query(prompt=goal, options=options):
        if isinstance(message, ResultMessage):
            final = message
    return final


async def run_task(task_id: str, goal: str) -> dict:
    settings = get_computer_settings()
    workdir = Path(settings.tasks_dir) / task_id
    (workdir / "out").mkdir(parents=True, exist_ok=True)

    options = ClaudeAgentOptions(
        cwd=str(workdir),
        max_turns=settings.max_turns,
        system_prompt=_SYSTEM_PROMPT,
        mcp_servers={"computer-use": computer_use_server},
        permission_mode="bypassPermissions",
        env={
            "ANTHROPIC_BASE_URL": settings.litellm_base_url,
            "ANTHROPIC_API_KEY": settings.litellm_api_key,
        },
    )

    try:
        result = await asyncio.wait_for(
            _run(goal, options), timeout=settings.task_timeout_seconds
        )
    except TimeoutError:
        return {
            "error": f"TimeoutError: task exceeded {settings.task_timeout_seconds}s"
        }
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("task %s failed", task_id, exc_info=True)
        return {"error": f"{exc.__class__.__name__}: {exc}"}

    artifacts = sorted(p.name for p in (workdir / "out").glob("*") if p.is_file())

    if result is None:
        return {"error": "claude-agent-sdk produced no ResultMessage"}
    if result.is_error:
        detail = (
            "; ".join(result.errors)
            if result.errors
            else (result.result or result.stop_reason or "unknown error")
        )
        return {"error": detail}
    return {"summary": result.result or "", "artifacts": artifacts}
