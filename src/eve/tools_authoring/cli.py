"""`eve-tool`: review, approve, reject and revoke Eve-authored tool code.

Approving in a terminal rather than only in a chat thread matters for the
cases the interrupt does not cover: a proposal whose thread was abandoned, and
a revocation that has to happen now.
"""

from __future__ import annotations

import argparse
import asyncio

import psycopg.errors

from eve.memory.db import close_pool
from eve.tools_authoring.inspect import check
from eve.tools_authoring.store import (
    all_tools,
    approve,
    by_id,
    reject,
    revoke,
    revoke_all,
)


async def approve_one(tool_id: str, approver: str) -> bool:
    """Re-check the source at approval time. The propose-time check already
    ran, but an approval is a statement about these bytes, so it is re-made
    against these bytes."""
    row = await by_id(tool_id)
    if row is None:
        raise SystemExit(f"no such tool: {tool_id}")
    result = check(row["source"])
    if not result.ok:
        raise SystemExit(
            "refusing to approve; the source fails its checks:\n"
            + "\n".join(f"  - {p}" for p in result.problems)
        )
    return await approve(tool_id, approver)


async def revoke_one(name: str, why: str) -> int:
    return await revoke(name, why)


def _status(row: dict) -> str:
    if row["revoked_at"]:
        return "revoked"
    if row["approved_at"]:
        return "live"
    if row["rejected_why"]:
        return "rejected"
    return "pending"


def _render(rows: list[dict]) -> str:
    if not rows:
        return "No tools proposed yet."
    lines = []
    for row in rows:
        lines.append(
            f"{row['id']}  {_status(row):<8}  {row['name']:<20} "
            f"invocations={row['invocations']}\n"
            f"    {row['description'][:90]}\n"
            f"    sha256={row['source_sha256'][:16]}...  by={row['proposed_by']}"
            f"  thread={row['source_thread'] or '-'}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    lister = sub.add_parser("list", help="show every proposal and its status")
    lister.add_argument("--source", help="also print the full source of this id")

    approver = sub.add_parser("approve", help="approve a proposal by id")
    approver.add_argument("id")
    approver.add_argument("--as", dest="approver", required=True)

    rejecter = sub.add_parser("reject", help="reject a proposal by id")
    rejecter.add_argument("id")
    rejecter.add_argument("--why", required=True)

    revoker = sub.add_parser("revoke", help="retire a live tool by name")
    revoker.add_argument("name", nargs="?")
    revoker.add_argument("--all", action="store_true")
    revoker.add_argument("--why", default="unspecified")

    args = parser.parse_args()

    async def _run() -> None:
        try:
            if args.command == "list":
                print(_render(await all_tools()))
                if args.source:
                    row = await by_id(args.source)
                    if row:
                        print(f"\n--- {row['name']} ---\n{row['source']}")
            elif args.command == "approve":
                try:
                    ok = await approve_one(args.id, args.approver)
                except psycopg.errors.UniqueViolation:
                    # store.approve() deliberately lets this propagate (see
                    # its docstring): the partial unique index is the real
                    # backstop, and only the CLI layer knows what a friendly
                    # answer looks like.
                    raise SystemExit(
                        "a live version of this tool already exists; revoke it first"
                    ) from None
                print("approved" if ok else "not approved (already decided?)")
            elif args.command == "reject":
                await reject(args.id, args.why)
                print("rejected")
            else:
                if args.all:
                    print(f"revoked {await revoke_all(args.why)} tools")
                elif args.name:
                    print(f"revoked {await revoke_one(args.name, args.why)} tools")
                else:
                    raise SystemExit("give a name or --all")
        finally:
            await close_pool()

    asyncio.run(_run())
