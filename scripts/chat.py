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
        async for chunk in client.runs.stream(
            thread_id,
            _ASSISTANT,
            input={"messages": [{"role": "user", "content": text}]},
            stream_mode="messages-tuple",
        ):
            if chunk.event != "messages":
                continue
            message, _metadata = chunk.data
            if message.get("type") == "AIMessageChunk":
                print(message.get("content", ""), end="", flush=True)
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
