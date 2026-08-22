"""Turns a DynamicToolSpec back into a callable tool, freshly, on every model
call - design doc section 5.1 explains why state can only ever hold the
spec. Only string/integer/number/boolean argument types are supported;
a schema with a richer type (array, object, enum) falls back to str, which
is wrong for validation but not for dispatch - eve-tools receives whatever
JSON-compatible value the model supplies either way.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import create_model

from eve.skills.types import DynamicToolSpec
from eve.tools_client import invoke

_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _args_model(tool_name: str, schema: dict):
    fields = {
        prop: (_TYPE_MAP.get(info.get("type", "string"), str), ...)
        for prop, info in schema.get("properties", {}).items()
    }
    return create_model(f"{tool_name}Args", **fields)


def materialize(spec: DynamicToolSpec) -> StructuredTool:
    args_model = _args_model(spec["tool_name"], spec["schema"])

    async def _call(**kwargs) -> str:
        return await invoke(
            "mcp.invoke",
            {
                "server_id": spec["server_id"],
                "tool_name": spec["tool_name"],
                "arguments": kwargs,
            },
        )

    return StructuredTool.from_function(
        coroutine=_call,
        name=f"{spec['server_id']}_{spec['tool_name']}",
        description=spec["description"],
        args_schema=args_model,
    )
