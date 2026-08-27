"""`eve-skill`: read and revoke what Eve has written about her own behaviour.

Autonomous does not mean invisible. eve_memory already carries source_thread,
source_run and created_at, so provenance is free - this is the read-and-revoke
path that is not raw SQL, modelled on eve-pat (design doc section 7).

A CLI rather than a UI because the review action is rare and the operator is
one person with a terminal.
"""

from __future__ import annotations

import argparse
import asyncio

from eve.memory.db import close_pool
from eve.memory.store import _COLUMNS, _fetch, supersede


async def authored() -> list:
    """Every live rule and procedure, newest first."""
    return await _fetch(
        f"""
        SELECT {_COLUMNS} FROM eve_memory
        WHERE superseded_why IS NULL
          AND layer IN ('rule', 'procedure')
        ORDER BY layer, created_at DESC
        """,
        {},
    )


async def revoke(memory_id: str, why: str) -> None:
    """Retire a row, keeping it. supersede, not forget: forget is a hard
    DELETE reserved for a member's own data, and this row is the audit trail
    Phase 5b learns from."""
    await supersede(memory_id, None, f"revoked by operator: {why}")


def _render(rows: list) -> str:
    if not rows:
        return "Nothing authored yet."
    lines = []
    for row in rows:
        scope = f"{row.scope_kind}:{row.scope_id}" if row.scope_id else row.scope_kind
        name = f" {row.subject}" if row.subject else ""
        first_line = row.content.splitlines()[0][:100] if row.content else "(empty)"
        lines.append(
            f"{row.id}  {row.layer:<9}  {scope:<20}{name}\n"
            f"    {first_line}\n"
            f"    created {row.created_at:%Y-%m-%d %H:%M}  thread={row.source_thread or '-'}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show live rules and procedures")
    revoker = sub.add_parser("revoke", help="retire one by id")
    revoker.add_argument("id")
    revoker.add_argument("--why", default="unspecified")
    args = parser.parse_args()

    async def _run() -> None:
        try:
            if args.command == "list":
                print(_render(await authored()))
            else:
                await revoke(args.id, args.why)
                print(f"revoked {args.id}")
        finally:
            await close_pool()

    asyncio.run(_run())
