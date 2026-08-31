"""Eve's graph.

    START -> load_context -> recall -> eve <-> tools -> extract -> END

`load_context` is pure local computation. `recall` is the one place ADR 0002
bends: a single bounded, cancellable embedding call, which ships lexical-only
if it misses its budget. `extract` runs after the answer has streamed and hands its
work to a background task, so it delays neither the answer nor the end of the
turn; the next turn on the thread joins it before reading memory (ADR 0012).
Phase 3 (this file) adds the `eve <-> tools` cycle:
`eve` binds the static specialist/skill tools plus any dynamically-discovered
ones (freshly materialized from state on every call) and either answers,
routing to `extract`, or emits tool calls, routing to `tools` and back.

The system prompt is rebuilt from scratch every turn and passed to the model
without being appended to `messages`, so persona, member-context and memory
edits take effect on existing threads instead of being frozen into history.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from eve import context
from eve.context import load_context
from eve.memory import extract as memory_extract, recall as memory_recall
from eve.memory.search import search_memory
from eve.models import Tier, get_model
from eve.settings import get_settings
from eve.skills.authoring import write_skill
from eve.skills.materialize import materialize
from eve.skills.search import search_skills
from eve.specialists.finances import ask_finances
from eve.specialists.home import ask_home
from eve.specialists.mail import ask_mail
from eve.state import LOOP_EXHAUSTED as _LOOP_EXHAUSTED, EveState
from eve.suggest import suggest as suggest_node
from eve.tools_authoring.propose import propose_tool

_BASE_TOOLS = [ask_home, ask_mail, ask_finances, search_skills, search_memory]


def _live_specs(state: EveState) -> list:
    """Drop sandbox specs when the kill switch is off, wherever they came
    from - including a thread checkpointed while it was on."""
    enabled = get_settings().sandbox_enabled
    return [
        spec
        for spec in state.get("dynamic_tools", [])
        if enabled or spec.get("server_id") != "sandbox"
    ]


def _static_tools() -> list:
    """Rebuilt per call rather than fixed at import: two settings gate two
    tools, and both `eve` and `tools_node` need the same answer within one
    turn. Settings are lru_cached, so this is a dict lookup."""
    settings = get_settings()
    tools = list(_BASE_TOOLS)
    if settings.self_authoring_enabled:
        tools.append(write_skill)
    if settings.sandbox_enabled:
        tools.append(propose_tool)
    return tools


# The ChatGPT backend refuses system messages outright - verified live on
# 2026-08-18, it answers `{"detail":"System messages are not allowed"}` and the
# whole turn fails. The Responses API's replacement is the `developer` role,
# which langchain-openai emits when a SystemMessage carries this marker.
#
# Keep the marker even if the tiers move off `chatgpt/*`: LiteLLM translates
# `developer` back to a system message for providers that want one, so this is
# portable, whereas a bare SystemMessage is not.
_OPENAI_DEVELOPER_ROLE = {"__openai_role__": "developer"}


def _persona_message(system_prompt: str) -> SystemMessage:
    return SystemMessage(system_prompt, additional_kwargs=_OPENAI_DEVELOPER_ROLE)


def _handle_tool_error(error: Exception) -> str:
    """The plan's global constraint - every call to an external system
    degrades to a returned string, never raises - applied at the one place it
    can be applied to every tool at once. `ToolNode`'s default handler
    (`_default_handle_tool_errors`) re-raises anything that is not an
    argument-validation failure, so a LiteLLM outage inside a specialist's
    inner loop, an embedding failure inside `search_skills` or a Postgres
    failure inside `search_memory` would otherwise kill the whole run and
    answer a 500 instead of an Eve sentence.

    Annotated `Exception` deliberately: LangGraph's `_infer_handled_types`
    reads this annotation to decide what to catch, and `GraphBubbleUp`
    (interrupts, parent commands) is re-raised before it ever gets here."""
    return f"error: {error.__class__.__name__}: {error}"


def _tool_rounds_this_turn(messages: list) -> int:
    """How many times `eve` has already answered with tool calls since the
    member last spoke. Derived from `messages` rather than kept as an
    EveState field on purpose: every field of EveState is a required field of
    the pydantic schema `InjectedState` validates, so adding one would make
    every tool taking injected state fail wherever the new key is missing -
    the same failure mode `_last_write_wins` exists to prevent. Reading
    backwards to the last HumanMessage also resets the budget per turn for
    free, which a checkpointed counter would have to be told to do."""
    rounds = 0
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage) and message.tool_calls:
            rounds += 1
    return rounds


def build_graph(
    model_factory=get_model,
    recall_fn=memory_recall,
    extract_fn=memory_extract,
    suggest_fn=suggest_node,
) -> StateGraph:
    async def eve(state: EveState, config: RunnableConfig) -> dict:
        if _tool_rounds_this_turn(state["messages"]) >= (
            get_settings().max_tool_loop_iterations
        ):
            # A normal AIMessage carrying no tool calls, so `tools_condition`
            # routes to `extract` and the turn ends with a sentence instead
            # of an exception.
            return {"messages": [AIMessage(_LOOP_EXHAUSTED)]}
        model = model_factory(Tier.VOICE)
        dynamic = [materialize(spec) for spec in _live_specs(state)]
        bound_model = model.bind_tools([*_static_tools(), *dynamic])
        # Through the MODULE, not a from-import. `tests/test_graph.py`
        # monkeypatches `eve.context.load_persona`, and a module-level
        # `from eve.context import load_persona` here would bind the real
        # function at import time and quietly ignore the patch - the tests
        # would still pass while asserting against the real prompts/eve.md.
        prompt = context.build_system_prompt(
            context.load_persona(), state["member"], state.get("memory")
        )
        messages = [_persona_message(prompt), *state["messages"]]
        return {"messages": [await bound_model.ainvoke(messages, config)]}

    async def tools_node(state: EveState, config: RunnableConfig) -> dict:
        dynamic = [materialize(spec) for spec in _live_specs(state)]
        # Rebuilt fresh, not cached on the module: a spec discovered by
        # search_skills two turns ago must still resolve to a live tool now,
        # and EveState is the only thing that survives between them.
        node = ToolNode(
            [*_static_tools(), *dynamic], handle_tool_errors=_handle_tool_error
        )
        return await node.ainvoke(state, config)

    builder = StateGraph(EveState)
    builder.add_node("load_context", load_context)
    builder.add_node("recall", recall_fn)
    builder.add_node("eve", eve)
    builder.add_node("tools", tools_node)
    builder.add_node("extract", extract_fn)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "recall")
    builder.add_edge("recall", "eve")
    # Bounded by `eve`'s own `_tool_rounds_this_turn` check, not by
    # LangGraph's recursion_limit: that default is 10007
    # (langgraph/_internal/_config.py's DEFAULT_RECURSION_LIMIT), `.compile()`
    # takes no recursion_limit, and Aegra supplies its own invoke-time config
    # that would override a `.with_config()` here anyway. Left to the
    # platform, a confused model burns thousands of paid calls per turn.
    builder.add_conditional_edges("eve", tools_condition, {"tools": "tools", END: "extract"})
    builder.add_edge("tools", "eve")
    builder.add_node("suggest", suggest_fn)
    # AFTER extract, not before. With EVE_MEMORY_EXTRACT_BACKGROUND=true (the
    # default) `extract` returns as soon as it registers its task, so
    # extraction's REFLEX call and the suggestion call overlap and the turn
    # pays max() rather than sum(). Reversing them serialises two calls for
    # no gain. See ADR 0013.
    builder.add_edge("extract", "suggest")
    builder.add_edge("suggest", END)
    return builder


# Compiled WITHOUT a checkpointer: Aegra attaches its own Postgres persistence
# to graphs it serves. Adding one here would shadow it.
graph = build_graph().compile()
