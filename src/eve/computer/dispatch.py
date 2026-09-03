"""dispatch_computer_task: Eve's one tool onto her own machine. Permission is
checked here, before the HTTP call, so a denied request never reaches
eve-computer at all - ADR 0006's pattern (permission checks happen in Eve's
main container, before the HTTP call), applied a third time."""

from __future__ import annotations

import uuid
from typing import Annotated

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from eve.computer.store import create_task
from eve.specialists.permissions import permission_denial
from eve.state import EveState
from eve.tools_client import dispatch_task


@tool
async def dispatch_computer_task(
    goal: str, state: Annotated[EveState, InjectedState], config: RunnableConfig
) -> str:
    """Dispatch a task to Eve's own computer: a persistent Linux desktop with
    a browser, a shell, and her own accounts. Use this for anything that
    needs a real account, a real browser, or a real shell rather than
    something answerable directly. Returns immediately; the result is
    reported later, in a separate message, once the task finishes."""
    member = state["member"]
    denial = permission_denial(member.get("permissions", []), "computer.use")
    if denial:
        return denial

    configurable = config.get("configurable", {}) if config else {}
    thread_id = configurable.get("thread_id")
    if not thread_id:
        return "error: no thread to report the result on"

    task_id = str(uuid.uuid4())
    dispatched = await dispatch_task(task_id, goal)
    if dispatched.startswith("error:"):
        return dispatched

    await create_task(
        task_id=task_id, member_sub=member["sub"], thread_id=thread_id, goal=goal
    )
    return "I'm on it — I'll let you know when it's done."
