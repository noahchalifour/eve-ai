"""Personal access tokens: one long-lived, individually revocable credential
per scripted client.

The third credential `eve.auth` accepts, and the answer to the ambient
token's one real weakness: `EVE_AMBIENT_TOKEN` is a single shared secret that
can impersonate any member, so rotating it after a laptop is lost kills every
client at once. A PAT names one member, belongs to one client, and is revoked
on its own.

The token is shown once at mint time and never stored - only its sha256. A
dump of `eve_pat` therefore yields no working credential, and there is no
"show me the token again" operation to build. Lose it, mint another.

Tokens carry an `evepat_` prefix. That is not decoration: every OIDC request
presents a JWT, and without a cheap syntactic test for "is this even a PAT"
each one would cost a database round trip. It also makes a leaked token
greppable in a secret store or a log.

    ponytail: no expiry column. Revocation is the lever, and a token that
    expires on a schedule nobody watches is a 401 during dinner rather than a
    security control. Add `expires_at` if a token ever has to outlive its
    owner's attention.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import secrets

from psycopg.rows import dict_row

from eve.family import UnknownMemberError, get_family
from eve.memory.db import close_pool, get_pool
from eve.settings import get_settings

PREFIX = "evepat_"


def generate() -> str:
    """A new token. 32 random bytes, base64url-encoded to 43 characters."""
    return PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def looks_like_pat(token: str) -> bool:
    return token.startswith(PREFIX)


async def subject_for(token: str) -> str | None:
    """The subject this token authenticates as, or None if it is unknown,
    revoked, or not a PAT at all.

    Authentication and the last-used stamp are one statement: the write is
    the read. Splitting them would double the per-request cost of the one
    query on Eve's hot path, and the stamp is only ever wanted for tokens
    that just authenticated anyway.

    Deliberately uncached. This is a primary-key lookup on an already-open
    pool, and a cache is the difference between revocation taking effect now
    and taking effect eventually - which is the whole reason this table
    exists instead of an environment variable.
    """
    if not looks_like_pat(token) or not get_settings().database_url:
        return None
    pool = await get_pool()
    async with pool.connection() as conn:
        # Scoped to this cursor, not the connection: see db.migrate()'s
        # comment on why row_factory must not leak to the next checkout.
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "UPDATE eve_pat SET last_used_at = now() "
                "WHERE token_hash = %s AND revoked_at IS NULL "
                "RETURNING sub",
                (hash_token(token),),
            )
            row = await cur.fetchone()
    return row["sub"] if row else None


async def mint(sub: str, label: str) -> str:
    """Create a token for a roster member. Returns it once.

    The roster check is here rather than left to first use: authentication
    resolves the subject before it consults `family.yaml`, so a typo'd sub
    mints a token that 401s with a message about a subject the operator never
    typed anywhere they can see.
    """
    get_family().get(sub)  # raises UnknownMemberError
    token = generate()
    pool = await get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_pat (token_hash, sub, label) VALUES (%s, %s, %s)",
            (hash_token(token), sub, label),
        )
    return token


async def revoke(label: str) -> int:
    """Revoke by label. Returns how many tokens that was - 0 if the label is
    unknown or was already revoked, never more than 1 (see the partial unique
    index in db.MIGRATIONS)."""
    pool = await get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            "UPDATE eve_pat SET revoked_at = now() "
            "WHERE label = %s AND revoked_at IS NULL",
            (label,),
        )
        return cur.rowcount


async def active() -> list[dict]:
    """Live tokens, oldest first. Without the hash: nothing needs it outside
    this module, and printing it invites treating it as the credential."""
    pool = await get_pool()
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT label, sub, created_at, last_used_at FROM eve_pat "
                "WHERE revoked_at IS NULL ORDER BY created_at"
            )
            return await cur.fetchall()


def main() -> None:
    """`eve-pat` console script, alongside `eve-migrate`."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    minter = sub.add_parser("mint", help="create a token for a family member")
    minter.add_argument("sub", help="the member's sub, as in family.yaml")
    minter.add_argument("label", help="which client this is for, e.g. 'laptop'")
    sub.add_parser("list", help="show live tokens")
    revoker = sub.add_parser("revoke", help="revoke a token by label")
    revoker.add_argument("label")
    args = parser.parse_args()

    async def _run() -> None:
        try:
            if args.command == "mint":
                try:
                    token = await mint(args.sub, args.label)
                except UnknownMemberError as exc:
                    # A mistyped sub is the likeliest way to use this command
                    # wrong. A traceback buries the one line that says so.
                    raise SystemExit(f"eve-pat: {exc}") from None
                print(token)
                print(
                    "\nShown once and not stored. Present it as "
                    "`Authorization: Bearer <token>`.",
                )
            elif args.command == "revoke":
                count = await revoke(args.label)
                print(f"revoked {count}")
            else:
                for row in await active():
                    used = row["last_used_at"] or "never"
                    print(f"{row['label']}\t{row['sub']}\t{used}")
        finally:
            await close_pool()

    asyncio.run(_run())
