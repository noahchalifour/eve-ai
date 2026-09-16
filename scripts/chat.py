"""A manual REPL for talking to Eve, streaming her reply token by token.

Points at a local `uv run aegra dev` by default, authenticated with the dev
token from `.env.example`'s `EVE_DEV_TOKENS`. Override for a different
target (e.g. the production deployment, with a PAT minted via `eve-pat
mint`):

    AEGRA_URL=https://eve.chalifour.dev AEGRA_TOKEN=<pat> \\
        uv run python scripts/chat.py

Usage: uv run python scripts/chat.py [thread_id]

A thread id argument resumes that conversation; otherwise a new thread is
created. The thread id is printed at startup so it can be resumed later.
"""

from __future__ import annotations

import asyncio
import os
import sys

from langgraph_sdk import get_client

_ASSISTANT = "eve"


async def main() -> None:
    client = get_client(
        url=os.environ.get("AEGRA_URL", "http://localhost:2026"),
        headers={"Authorization": f"Bearer {os.environ.get('AEGRA_TOKEN', 'dev-noah-token')}"},
    )

    if len(sys.argv) > 1:
        thread_id = sys.argv[1]
    else:
        thread = await client.threads.create()
        thread_id = thread["thread_id"]
    print(f"thread: {thread_id}\n")

    while True:
        try:
            text = input("you> ")
        except EOFError:
            break
        if text in ("exit", "quit"):
            break

        print("eve> ", end="", flush=True)
        chips: list[str] = []
        labels: dict[str, str] = {}
        async for chunk in client.runs.stream(
            thread_id,
            _ASSISTANT,
            input={"messages": [{"role": "user", "content": text}]},
            stream_mode=["messages-tuple", "custom"],
        ):
            if chunk.event == "custom":
                # The same frames the Flutter client reads (OPENA-14). Printed
                # after the reply rather than inline: chips arrive once the
                # answer has finished streaming.
                data = chunk.data or {}
                suggestions = data.get("suggestions")
                if isinstance(suggestions, list):
                    chips = [s for s in suggestions if isinstance(s, str)]
                # The activity phrases the client puts on its trace ticker.
                # Shown here so the one local harness we have does not go
                # quiet about a frame that ships to members.
                tool_labels = data.get("tool_labels")
                if isinstance(tool_labels, dict):
                    labels = tool_labels
                continue
            if chunk.event != "messages":
                continue
            message, _metadata = chunk.data
            if message.get("type") == "AIMessageChunk":
                print(message.get("content", ""), end="", flush=True)
            elif message.get("type") == "tool":
                # Labelled where a label exists, raw name where it does not -
                # which is exactly what the client would render either way.
                name = message.get("name", "")
                print(f"\n  ({labels.get(name, name)})", end="", flush=True)
        print()
        if chips:
            print("  " + "   ".join(f"[{chip}]" for chip in chips))
        print()


if __name__ == "__main__":
    asyncio.run(main())
