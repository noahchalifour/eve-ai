"""Eve's graph.

    START -> load_context -> recall -> eve <-> tools -> extract -> END

`load_context` is pure local computation. `recall` is the one place ADR 0002
bends: a single bounded, cancellable embedding call, which ships lexical-only
if it misses its budget. `extract` runs after the answer has streamed, so its
latency is invisible. Phase 3 (this file) adds the `eve <-> tools` cycle:
`eve` binds the static specialist/skill tools plus any dynamically-discovered
ones (freshly materialized from state on every call) and either answers,
routing to `extract`, or emits tool calls, routing to `tools` and back.

The system prompt is rebuilt from scratch every turn and passed to the model
without being appended to `messages`, so persona, member-context and memory
edits take effect on existing threads instead of being frozen into history.
"""

from __future__ import annotations

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from eve import context
from eve.context import load_context
from eve.memory import extract as memory_extract, recall as memory_recall
from eve.memory.search import search_memory
from eve.models import Tier, get_model
from eve.skills.materialize import materialize
from eve.skills.search import search_skills
from eve.specialists.finances import ask_finances
from eve.specialists.home import ask_home
from eve.specialists.mail import ask_mail
from eve.state import EveState

_STATIC_TOOLS = [ask_home, ask_mail, ask_finances, search_skills, search_memory]


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


def build_graph(
    model_factory=get_model, recall_fn=memory_recall, extract_fn=memory_extract
) -> StateGraph:
    async def eve(state: EveState, config: RunnableConfig) -> dict:
        model = model_factory(Tier.VOICE)
        dynamic = [materialize(spec) for spec in state.get("dynamic_tools", [])]
        bound_model = model.bind_tools([*_STATIC_TOOLS, *dynamic])
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
        dynamic = [materialize(spec) for spec in state.get("dynamic_tools", [])]
        # Rebuilt fresh, not cached on the module: a spec discovered by
        # search_skills two turns ago must still resolve to a live tool now,
        # and EveState is the only thing that survives between them.
        node = ToolNode([*_STATIC_TOOLS, *dynamic])
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
    # Bounded by LangGraph's own recursion_limit (default 10007 as of the
    # installed langgraph - langgraph/_internal/_config.py's
    # DEFAULT_RECURSION_LIMIT, verified against this project's lockfile),
    # not a custom counter - a runaway loop still terminates instead of
    # running forever, and this is the platform mechanism for exactly that
    # ceiling. Raise a dedicated counter only if a real runaway is ever
    # observed; the default is generous enough that hitting it in practice
    # means something is actually broken, not merely a long conversation.
    builder.add_conditional_edges("eve", tools_condition, {"tools": "tools", END: "extract"})
    builder.add_edge("tools", "eve")
    builder.add_edge("extract", END)
    return builder


# Compiled WITHOUT a checkpointer: Aegra attaches its own Postgres persistence
# to graphs it serves. Adding one here would shadow it.
graph = build_graph().compile()
