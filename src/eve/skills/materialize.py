"""Turns a DynamicToolSpec back into a callable tool, freshly, on every model
call - design doc section 5.1 explains why state can only ever hold the
spec. Only string/integer/number/boolean argument types are supported;
a schema with a richer type (array, object, enum) falls back to str, which
is wrong for validation but not for dispatch - eve-tools receives whatever
JSON-compatible value the model supplies either way.
"""

from __future__ import annotations

import logging
from time import perf_counter

from langchain_core.tools import StructuredTool
from opentelemetry import trace
from pydantic import create_model

from eve.skills.types import DynamicToolSpec
from eve.tools_client import invoke

logger = logging.getLogger(__name__)

_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _args_model(tool_name: str, schema: dict):
    fields = {
        prop: (_TYPE_MAP.get(info.get("type", "string"), str), ...)
        for prop, info in schema.get("properties", {}).items()
    }
    return create_model(f"{tool_name}Args", **fields)


def materialize(spec: DynamicToolSpec) -> StructuredTool:
    args_model = _args_model(spec["tool_name"], spec["schema"])
    is_sandbox = spec.get("server_id") == "sandbox"

    async def _call(**kwargs) -> str:
        if is_sandbox:
            started = perf_counter()
            # The source travels with the request, so eve-sandbox needs no
            # database credential - the last one it might otherwise hold.
            result = await invoke(
                spec["tool_name"],
                kwargs,
                target="sandbox",
                extra={
                    "source": spec.get("source", ""),
                    "source_sha256": spec.get("source_sha256", ""),
                },
            )
            # Observability on THIS side of the hop. eve-sandbox emits no
            # spans - it holds no Langfuse credential and should not - so the
            # numbers design section 11 asks for are recorded by the caller,
            # which is also the only side that knows the round trip's cost.
            span = trace.get_current_span()
            span.set_attribute(
                "eve.sandbox.duration_ms", round((perf_counter() - started) * 1000, 1)
            )
            if result.startswith("error:"):
                lowered = result.lower()
                if "time limit" in lowered:
                    span.set_attribute("eve.sandbox.timeouts", 1)
                if "hash mismatch" in lowered:
                    # Should be zero forever. Non-zero is an incident: the
                    # database and the caller disagree about approved bytes.
                    span.set_attribute("eve.sandbox.hash_mismatch", 1)
                    logger.error(
                        "sandbox refused %r on a source hash mismatch",
                        spec["tool_name"],
                    )

            # Count the use, best-effort. A tool approved and then used once
            # was a wasted approval; `eve-tool list` is where that shows up,
            # and it only shows up if dispatch records it.
            tool_id = spec.get("tool_id")
            if tool_id:
                try:
                    from eve.tools_authoring.store import record_invocation

                    await record_invocation(tool_id)
                except Exception:
                    logger.debug("could not count the invocation", exc_info=True)
            return result
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
