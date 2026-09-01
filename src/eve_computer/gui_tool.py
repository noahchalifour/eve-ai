"""Anthropic's reference computer-use tool, lifted rather than rewritten
(design doc: "v1: Claude, and a seam") - xdotool driving the desktop on
:99, a screenshot via ImageMagick's `import`. Registered as an SDK MCP tool
so claude-agent-sdk can call it exactly like its built-in bash/read/write/
edit tools.

Schema note: claude_agent_sdk.tool()'s plain-dict shorthand
(`{"action": str, ...}`) marks every key "required" in the JSON Schema it
builds (see claude_agent_sdk.__init__._build_input_schema) - that would
reject a bare `{"action": "screenshot"}` call, which is the tool's most
common invocation. A literal JSON Schema dict is passed instead so only
`action` is required and `coordinate`/`text` stay optional, matching how
they're actually used per-action below.
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

DISPLAY = ":99"

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["screenshot", "left_click", "mouse_move", "type", "key"],
        },
        "coordinate": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "[x, y], required for left_click/mouse_move.",
        },
        "text": {
            "type": "string",
            "description": "Required for type/key.",
        },
    },
    "required": ["action"],
}


async def _xdotool(*args: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "xdotool", *args, env={"DISPLAY": DISPLAY},
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)} failed: {stderr.decode()}")


async def _screenshot() -> str:
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        proc = await asyncio.create_subprocess_exec(
            "import", "-window", "root", handle.name, env={"DISPLAY": DISPLAY},
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"screenshot failed: {stderr.decode()}")
        return base64.b64encode(Path(handle.name).read_bytes()).decode()


@tool(
    "computer",
    "Control the desktop: screenshot, click, move the mouse, type text, or "
    "press a key. `action` is one of: screenshot, left_click, mouse_move, "
    "type, key. `coordinate` is [x, y], required for left_click/mouse_move. "
    "`text` is required for type/key.",
    _INPUT_SCHEMA,
)
async def computer(args: dict) -> dict:
    action = args["action"]
    if action == "screenshot":
        # mimeType is required by claude_agent_sdk's image content block
        # (mcp_types.ImageContent) - omitting it turns every screenshot into
        # a silent "'mimeType'" KeyError result instead of an actual image.
        return {
            "content": [
                {"type": "image", "data": await _screenshot(), "mimeType": "image/png"}
            ]
        }
    if action == "left_click":
        x, y = args["coordinate"]
        await _xdotool("mousemove", str(x), str(y), "click", "1")
    elif action == "mouse_move":
        x, y = args["coordinate"]
        await _xdotool("mousemove", str(x), str(y))
    elif action == "type":
        await _xdotool("type", "--", args["text"])
    elif action == "key":
        await _xdotool("key", args["text"])
    else:
        return {"content": [{"type": "text", "text": f"unknown action {action!r}"}]}
    return {"content": [{"type": "text", "text": "ok"}]}


computer_use_server = create_sdk_mcp_server(name="computer-use", tools=[computer])
