"""Personal access tokens: minting, resolution, and revocation.

The unit tier covers the parts that decide whether the database is touched
at all. The integration tier covers the single statement that resolves a
token, against the compose Postgres.
"""

from __future__ import annotations

import pytest

from eve import pat
from eve.family import UnknownMemberError
from eve.memory import db
from eve.settings import get_settings


def test_a_minted_token_carries_the_prefix_and_is_not_guessable():
    first, second = pat.generate(), pat.generate()
    assert first.startswith(pat.PREFIX)
    assert first != second
    # 32 random bytes, base64url-encoded. Short enough to paste, long enough
    # that the length itself is the argument against brute force.
    assert len(first) - len(pat.PREFIX) >= 43


def test_the_hash_does_not_contain_the_token():
    token = pat.generate()
    digest = pat.hash_token(token)
    assert token not in digest
    assert digest == pat.hash_token(token)
    assert digest != pat.hash_token(pat.generate())


async def test_a_bearer_without_the_prefix_never_touches_the_database(monkeypatch):
    """Every OIDC request presents a JWT. If resolution did not short-circuit
    on the prefix, each one would cost a query."""

    async def explode():
        raise AssertionError("the database must not be reached")

    monkeypatch.setattr(pat, "get_pool", explode)
    assert await pat.subject_for("eyJhbGciOiJSUzI1NiJ9.e30.sig") is None


async def test_resolution_is_inert_without_a_database_url(monkeypatch):
    """A dev run has no Postgres. A PAT-shaped bearer must fail closed with
    a 401 from the caller, not a connection error out of the auth handler."""

    async def explode():
        raise AssertionError("the database must not be reached")

    monkeypatch.setattr(pat, "get_pool", explode)
    monkeypatch.setenv("EVE_DATABASE_URL", "")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    assert await pat.subject_for(pat.generate()) is None


# --- integration tier ------------------------------------------------------

integration = pytest.mark.integration


@pytest.fixture
async def clean_pats(monkeypatch):
    monkeypatch.setenv("EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve")
    monkeypatch.setenv("EVE_FAMILY_FILE", "tests/fixtures/family.yaml")
    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    pool = await db.get_pool()
    async with pool.connection() as conn:
        await conn.execute("TRUNCATE eve_pat")
    yield
    await db.close_pool()


@integration
async def test_a_minted_token_resolves_to_its_member(clean_pats):
    token = await pat.mint("sub-noah", "laptop")
    assert await pat.subject_for(token) == "sub-noah"


@integration
async def test_use_stamps_last_used_at(clean_pats):
    token = await pat.mint("sub-noah", "laptop")
    assert [row["last_used_at"] for row in await pat.active()] == [None]
    await pat.subject_for(token)
    assert all(row["last_used_at"] is not None for row in await pat.active())


@integration
async def test_a_revoked_token_stops_resolving(clean_pats):
    token = await pat.mint("sub-noah", "laptop")
    assert await pat.revoke("laptop") == 1
    assert await pat.subject_for(token) is None


@integration
async def test_revoking_one_label_leaves_the_others(clean_pats):
    """The whole reason this table exists rather than one shared secret."""
    keep = await pat.mint("sub-noah", "laptop")
    drop = await pat.mint("sub-noah", "phone")
    await pat.revoke("phone")
    assert await pat.subject_for(keep) == "sub-noah"
    assert await pat.subject_for(drop) is None


@integration
async def test_revoking_an_unknown_label_reports_nothing_revoked(clean_pats):
    assert await pat.revoke("never-existed") == 0


@integration
async def test_revoking_twice_reports_nothing_the_second_time(clean_pats):
    await pat.mint("sub-noah", "laptop")
    assert await pat.revoke("laptop") == 1
    assert await pat.revoke("laptop") == 0


@integration
async def test_minting_for_a_subject_not_on_the_roster_is_refused(clean_pats):
    """Otherwise a typo mints a token that 401s on first use with no clue
    why - the roster lookup happens on the far side of authentication."""
    with pytest.raises(UnknownMemberError):
        await pat.mint("sub-stranger", "laptop")
    assert await pat.active() == []


@integration
async def test_an_unknown_token_resolves_to_nothing(clean_pats):
    await pat.mint("sub-noah", "laptop")
    assert await pat.subject_for(pat.generate()) is None


@integration
async def test_active_omits_revoked_tokens_and_hides_the_hash(clean_pats):
    await pat.mint("sub-noah", "laptop")
    await pat.mint("sub-kid", "tablet")
    await pat.revoke("tablet")
    rows = await pat.active()
    assert [row["label"] for row in rows] == ["laptop"]
    assert "token_hash" not in rows[0]


@integration
async def test_a_second_live_token_cannot_reuse_a_label(clean_pats):
    """Revocation is by label, so two live tokens sharing one would make
    `revoke` ambiguous about which client just lost access."""
    from psycopg.errors import UniqueViolation

    await pat.mint("sub-noah", "laptop")
    with pytest.raises(UniqueViolation):
        await pat.mint("sub-noah", "laptop")


@integration
async def test_a_revoked_label_can_be_reused_for_a_replacement(clean_pats):
    await pat.mint("sub-noah", "laptop")
    await pat.revoke("laptop")
    replacement = await pat.mint("sub-noah", "laptop")
    assert await pat.subject_for(replacement) == "sub-noah"
