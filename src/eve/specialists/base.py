"""One factory for every specialist: a small tool-calling loop on
Tier.MECHANICAL (langchain.agents.create_agent - NOT the deprecated
langgraph.prebuilt.create_react_agent, verified removed in LangGraph V2),
wrapped as a single opaque tool for eve's own loop. ADR 0001: specialists
keep their own agentic loop rather than becoming a flat tool list on Eve -
this factory is the one place that loop is built, so Home, Mail, and
Finances (Tasks 5-7) share it instead of three hand-rolled graphs.
"""

from __future__ import annotations

from time import perf_counter
from typing import Annotated

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import InjectedState
from opentelemetry import trace

from eve.models import Tier, get_model
from eve.settings import get_settings
from eve.specialists.permissions import permission_denial
from eve.state import EveState


def build_specialist(
    name: str,
    tools: list[BaseTool],
    system_prompt: str,
    permission: str | list[str],
    model_factory=get_model,
) -> BaseTool:
    # Built lazily, on first non-denied call, not here: `model_factory` must
    # never run before the permission check below has had a chance to deny
    # the request (test_denies_the_call_before_touching_the_model).
    agent_holder: dict[str, object] = {}

    async def ask(
        request: str,
        state: Annotated[EveState, InjectedState],
        config: RunnableConfig,
    ) -> str:
        # Design doc section 10: "which specialists actually get used" and
        # "is the permission boundary being hit in practice" are both
        # questions that need a number, not an assumption - same discipline
        # as memory/recall.py's eve.recall.* attributes.
        span = trace.get_current_span()
        span.set_attribute("eve.specialist.called", name)
        member = state["member"]
        denial = permission_denial(member["permissions"], permission)
        if denial:
            span.set_attribute("eve.specialist.permission_denied", True)
            return denial
        if "agent" not in agent_holder:
            agent_holder["agent"] = create_agent(
                model_factory(Tier.MECHANICAL), tools, system_prompt=system_prompt
            )
        agent = agent_holder["agent"]
        started = perf_counter()
        inner_config: RunnableConfig = {
            **config,
            "configurable": {**config.get("configurable", {}), "member": member},
            "recursion_limit": get_settings().specialist_max_iterations,
        }
        result = await agent.ainvoke(
            {"messages": [HumanMessage(request)]}, inner_config
        )
        span.set_attribute(
            "eve.specialist.latency_ms", round((perf_counter() - started) * 1000, 1)
        )
        return str(result["messages"][-1].content)

    ask.__name__ = f"ask_{name}"
    ask.__doc__ = f"Ask the {name} specialist to handle a request in its domain."
    return tool(ask)
