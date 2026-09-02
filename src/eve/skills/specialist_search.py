"""A specialist's own skills search: its scoped procedures, as text.

Deliberately NOT `eve.skills.search.search_skills`. That tool returns a
`Command` because half its job is appending DynamicToolSpecs to
`dynamic_tools` in EveState for materialization on the next model call. Inside
a specialist the loop is `create_agent`'s own message state, not EveState, and
there is no rebinding step to receive a spec - so MCP, sandbox and authored
matches are all excluded here and this is a plain string tool.

Knowledge crosses the boundary; capability does not. If a specialist ever
needs a dynamically-bound tool, that is a design problem deserving its own
ticket rather than something smuggled in behind a skills search.
"""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool, tool
from opentelemetry import trace

from eve.skills.registry import load_skills
from eve.skills.search import rank_skills

logger = logging.getLogger(__name__)

NO_MATCH = "No matching skill found."


def build_skills_search(specialist: str) -> BaseTool:
    """Build one search tool scoped to a specialist's own procedures."""

    async def search_skills(query: str) -> str:
        # ponytail: filesystem corpus only - no Eve-authored database rows,
        # which would mean a Postgres round trip inside every specialist loop.
        # Add one if authored specialist procedures ever become a real want.
        try:
            skills = [skill for skill in load_skills() if skill.specialist == specialist]
            matches = await rank_skills(query, skills)
        except Exception as exc:
            # Same contract as every other specialist tool: a filesystem or
            # embedding failure returns an error string, it does not fail the
            # specialist's turn.
            logger.warning(
                "specialist skills search failed for %s", specialist, exc_info=exc
            )
            return f"error: the skills search is unavailable ({exc.__class__.__name__})"
        trace.get_current_span().set_attribute(
            "eve.skills.specialist_search_used", specialist
        )
        if not matches:
            return NO_MATCH
        return "\n\n".join(f"# {match.name}\n{match.content}" for match in matches)

    search_skills.__doc__ = (
        f"Search the {specialist} specialist's own procedures for guidance on "
        "how to handle a request. Returns written guidance, not a new tool."
    )
    return tool(search_skills)
