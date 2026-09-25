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
from langchain.agents.middleware import wrap_model_call
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_config
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import InjectedState
from opentelemetry import trace

from eve.context import principal_sub
from eve.images import store as image_store
from eve.images.hydrate import hydrate, reference
from eve.models import TIER_VISION, Tier, get_model
from eve.settings import get_settings
from eve.skills.specialist_search import build_skills_search
from eve.specialists.permissions import permission_denial
from eve.state import EveState

# The ChatGPT backend refuses plain system messages outright - live-verified
# in graph.py's identical marker, whose comment has the full story: the
# Responses API's replacement is the `developer` role, which langchain-openai
# emits when a SystemMessage carries this marker. Every specialist runs on a
# chatgpt/*-backed tier (MECHANICAL), so `create_agent`'s system_prompt has
# to carry it too, or every real specialist call 400s with "System messages
# are not allowed" - caught live against the real proxy, not by any of this
# module's tests, all of which fake the model.
_OPENAI_DEVELOPER_ROLE = {"__openai_role__": "developer"}

# `specialist_max_iterations` counts model+tool ROUNDS; LangGraph's
# `recursion_limit` counts SUPERSTEPS, and `create_agent`'s graph spends two
# per round (`model`, then `tools`) plus one to enter and one to answer.
# Passing the setting through raw bought 2 rounds out of 6 (EVE-15): every
# specialist request needing a third tool call - search mail then open the
# message, list accounts then pull transactions - raised GraphRecursionError,
# which ToolNode's handler stringified into the conversation for Eve to read
# out to the member.
def _superstep_limit(rounds: int) -> int:
    return 2 * rounds + 2


# Whatever the budget is, a specialist that blows it has to answer in English.
# `graph.py`'s `_LOOP_EXHAUSTED` is the same guarantee for the outer loop.
def _loop_exhausted(name: str) -> str:
    return (
        f"I asked {name} to look into that, but it kept going without "
        "reaching an answer. Could you narrow the request down for me?"
    )


@wrap_model_call
async def _image_middleware(request, handler):
    """Hydrates reference blocks right before each inner model call, on the
    specialist's own tier (MECHANICAL) - the same function Eve's loop uses,
    so an image means the same thing at both levels (EVE-21, spec 3.3).

    The agent is cached in `agent_holder` across calls, so this cannot close
    over the outer `member` variable from one particular `ask` invocation;
    `get_config()` returns the INNER run's config at the moment the model is
    actually being called, which is the only reliable source here."""
    member_sub = principal_sub(get_config())
    messages = await hydrate(
        request.messages, member_sub, native=TIER_VISION[Tier.MECHANICAL]
    )
    return await handler(request.override(messages=messages))


def build_specialist(
    name: str,
    tools: list[BaseTool],
    system_prompt: str,
    permission: str | list[str],
    model_factory=get_model,
    *,
    accepts_images: bool = False,
) -> BaseTool:
    # Built lazily, on first non-denied call, not here: `model_factory` must
    # never run before the permission check below has had a chance to deny
    # the request (test_denies_the_call_before_touching_the_model).
    agent_holder: dict[str, object] = {}

    async def _run(
        request: str,
        state: Annotated[EveState, InjectedState],
        config: RunnableConfig,
        image_ids: list[str],
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
                model_factory(Tier.MECHANICAL),
                # Every specialist gets its own scoped skills search from the
                # factory rather than from four separate wirings, so the
                # capability arrives with the mechanism. A specialist with no
                # skills searches an empty set and is told so.
                [*tools, build_skills_search(name)],
                system_prompt=SystemMessage(
                    system_prompt, additional_kwargs=_OPENAI_DEVELOPER_ROLE
                ),
                **({"middleware": [_image_middleware]} if accepts_images else {}),
            )
        agent = agent_holder["agent"]
        started = perf_counter()
        inner_config: RunnableConfig = {
            **config,
            "configurable": {**config.get("configurable", {}), "member": member},
            "recursion_limit": _superstep_limit(
                get_settings().specialist_max_iterations
            ),
        }
        human: HumanMessage = HumanMessage(request)
        if image_ids:
            thread_id = (config.get("configurable") or {}).get("thread_id")
            refs = []
            for ref in image_ids:
                row = await image_store.resolve(ref, member["sub"], thread_id)
                if row is not None:
                    refs.append(reference(row.id, f"image {image_store.short_id(row.id)}"))
            if len(refs) < len(image_ids):
                span.set_attribute(
                    "eve.specialist.images_dropped", len(image_ids) - len(refs)
                )
            if refs:
                human = HumanMessage(content=[{"type": "text", "text": request}, *refs])
        try:
            result = await agent.ainvoke({"messages": [human]}, inner_config)
        except GraphRecursionError:
            span.set_attribute("eve.specialist.loop_exhausted", True)
            return _loop_exhausted(name)
        finally:
            span.set_attribute(
                "eve.specialist.latency_ms",
                round((perf_counter() - started) * 1000, 1),
            )
        return str(result["messages"][-1].content)

    # The schema `tool()` infers comes straight from this signature, so the
    # two variants are two separate function definitions rather than one
    # with a conditionally-added parameter - the only way to keep the
    # default schema byte-identical to before this flag existed.
    if accepts_images:

        async def ask(
            request: str,
            state: Annotated[EveState, InjectedState],
            config: RunnableConfig,
            image_ids: list[str] | None = None,
        ) -> str:
            return await _run(request, state, config, image_ids or [])
    else:

        async def ask(
            request: str,
            state: Annotated[EveState, InjectedState],
            config: RunnableConfig,
        ) -> str:
            return await _run(request, state, config, [])

    ask.__name__ = f"ask_{name}"
    ask.__doc__ = f"Ask the {name} specialist to handle a request in its domain."
    if accepts_images:
        ask.__doc__ += (
            '\n\nimage_ids: ids of photos from this conversation, as written '
            'in "[image <id>]".'
        )
    return tool(ask)
