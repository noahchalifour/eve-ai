"""Approved tools as DynamicToolSpecs, so the Phase 3 dispatch path carries
them unchanged: search_skills ranks and binds, materialize builds the
callable, tools_client posts to eve-sandbox.

Never called when EVE_SANDBOX_ENABLED is false - the check lives at the call
site in search_skills so the kill switch holds even for a name the database
still lists as approved.
"""

from __future__ import annotations

from eve.skills.types import DynamicToolSpec
from eve.tools_authoring.store import live_tools


async def sandbox_specs() -> list[DynamicToolSpec]:
    return [
        DynamicToolSpec(
            server_id="sandbox",
            tool_name=row["name"],
            description=row["description"],
            schema=row["args_schema"] or {},
            source=row["source"],
            source_sha256=row["source_sha256"],
            # Carried so materialize can count the invocation. A tool used
            # once was a wasted approval, and that is only visible if
            # dispatch records it (design section 11).
            tool_id=str(row["id"]),
        )
        for row in await live_tools()
    ]
