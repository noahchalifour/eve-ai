"""`eve-shortcut`: read and revoke Eve's learned shortcuts (ENG-296).

Same posture as `eve-skill`: learned does not mean invisible. A revoked
shortcut leaves the next turn's prompt and is never re-learned.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from eve.memory.db import close_pool
from eve.shortcuts import store


def _render(rows: list) -> str:
    if not rows:
        return "No shortcuts yet."
    lines = []
    for row in rows:
        options = ",".join(str(v) for v in row.get("variants") or []) or "-"
        used = row["last_used_at"].strftime("%Y-%m-%d %H:%M") if row.get("last_used_at") else "never"
        lines.append(
            f"{row['id']}  {row['status']:<8} {row['member_sub'][:12]:<12}  {row['name']}\n"
            f"    {row['specialist']}.{row['tool']} {json.dumps(row['fixed_args'])} options={options}\n"
            f"    hits={row['hits']} failures={row['consecutive_failures']} last used {used}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    lister = sub.add_parser("list", help="show every shortcut")
    lister.add_argument("--member", help="only this member's sub")
    shower = sub.add_parser("show", help="show one shortcut in full")
    shower.add_argument("id")
    revoker = sub.add_parser("revoke", help="revoke one by id; it is never re-learned")
    revoker.add_argument("id")
    args = parser.parse_args()

    async def _run() -> None:
        try:
            if args.command == "list":
                print(_render(await store.list_all(args.member)))
            elif args.command == "show":
                row = await store.get(args.id)
                print(json.dumps(row, indent=2, default=str) if row else f"no shortcut {args.id}")
            else:
                print(f"revoked {args.id}" if await store.revoke(args.id) else f"no shortcut {args.id}")
        finally:
            await close_pool()

    asyncio.run(_run())
