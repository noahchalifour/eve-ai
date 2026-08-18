"""Eve's graph.

    START -> load_context -> eve -> END

Two nodes on purpose. Phase 2 inserts a `recall` step that runs CONCURRENTLY
with the `eve` model call, and Phase 3 wraps `eve` in a tools loop. Neither
reshapes this.

The system prompt is rebuilt from scratch every turn and passed to the model
without being appended to `messages`, so persona and member-context edits take
effect on existing threads instead of being frozen into their history.
"""

from __future__ import annotations

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from eve.context import load_context
from eve.models import Tier, get_model
from eve.state import EveState


def build_graph(model_factory=get_model) -> StateGraph:
    async def eve(state: EveState, config: RunnableConfig) -> dict:
        model = model_factory(Tier.VOICE)
        messages = [SystemMessage(state["system_prompt"]), *state["messages"]]
        return {"messages": [await model.ainvoke(messages, config)]}

    builder = StateGraph(EveState)
    builder.add_node("load_context", load_context)
    builder.add_node("eve", eve)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "eve")
    builder.add_edge("eve", END)
    return builder


# Compiled WITHOUT a checkpointer: Aegra attaches its own Postgres persistence
# to graphs it serves. Adding one here would shadow it.
graph = build_graph().compile()
