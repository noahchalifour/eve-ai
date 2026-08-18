"""Integration tests against the real Postgres in docker-compose.test.yml.

The compose file already runs the VectorChord image the cluster runs, so the
vector path is exercised on the same engine as production.
"""

import pytest

from eve.memory import db

pytestmark = pytest.mark.integration


@pytest.fixture
async def pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    p = await db.get_pool()
    async with p.connection() as conn:
        await conn.execute("TRUNCATE eve_memory")
    yield p
    await db.close_pool()


async def test_migrate_creates_the_table(pool):
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT to_regclass('public.eve_memory')")
        assert (await cur.fetchone())[0] == "eve_memory"


async def test_migrate_is_idempotent(pool):
    """It runs on every pod start. If a second run is not a no-op, a rolling
    restart is an outage."""
    await db.migrate()
    await db.migrate()
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_schema_version")
        assert (await cur.fetchone())[0] == len(db.MIGRATIONS)


async def test_the_vector_column_accepts_a_1536_dim_vector(pool):
    vec = "[" + ",".join(["0.01"] * 1536) + "]"
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, "
            "content, embedding) VALUES "
            "('episodic','member','sub-noah','event','x', %s::vector)",
            (vec,),
        )
        cur = await conn.execute("SELECT count(*) FROM eve_memory")
        assert (await cur.fetchone())[0] == 1


async def test_superseded_rows_are_excluded_by_the_partial_index(pool):
    """Not an index test - a correctness test. Every read path relies on
    `superseded_why IS NULL`, and this is the one place it is asserted
    directly rather than through a query helper."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('profile','member','sub-noah','fact','old') RETURNING id"
        )
        old = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('profile','member','sub-noah','fact','new') RETURNING id"
        )
        new = (await cur.fetchone())[0]
        await conn.execute(
            "UPDATE eve_memory SET superseded_by=%s, superseded_why='contradicted'"
            " WHERE id=%s",
            (new, old),
        )
        cur = await conn.execute(
            "SELECT content FROM eve_memory WHERE superseded_why IS NULL"
        )
        assert [r[0] for r in await cur.fetchall()] == ["new"]


from eve.memory import store

_VEC = [0.0] * 1535 + [1.0]


async def _insert(pool, **kw) -> str:
    cols = {
        "layer": "episodic",
        "scope_kind": "member",
        "scope_id": "sub-noah",
        "kind": "event",
        "subject": None,
        "content": "something happened",
        "embedding": None,
        **kw,
    }
    names = ", ".join(cols)
    holes = ", ".join(
        "%s::vector" if n == "embedding" else "%s" for n in cols
    )
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"INSERT INTO eve_memory ({names}) VALUES ({holes}) RETURNING id",
            tuple(cols.values()),
        )
        return str((await cur.fetchone())[0])


async def test_always_on_returns_profile_household_and_digest(pool):
    await _insert(pool, layer="profile", kind="fact", content="Noah is vegetarian")
    await _insert(
        pool,
        layer="household",
        scope_kind="household",
        scope_id="",
        kind="fact",
        content="The dog is Cooper",
    )
    await _insert(
        pool,
        layer="digest",
        scope_kind="thread",
        scope_id="thread-1",
        kind="digest",
        content="They discussed dinner.",
    )
    profile, household, digest = await store.load_always_on("sub-noah", "thread-1")
    assert [m.content for m in profile] == ["Noah is vegetarian"]
    assert [m.content for m in household] == ["The dog is Cooper"]
    assert digest == "They discussed dinner."


async def test_always_on_does_not_leak_another_members_profile(pool):
    """The isolation that matters most in this whole phase. A profile fact is
    the most personal thing Eve stores."""
    await _insert(
        pool, layer="profile", scope_id="sub-kendra", kind="fact", content="secret"
    )
    profile, _, _ = await store.load_always_on("sub-noah", "thread-1")
    assert profile == []


async def test_household_is_visible_to_every_member(pool):
    await _insert(
        pool,
        layer="household",
        scope_kind="household",
        scope_id="",
        kind="fact",
        content="Trash goes out Sunday",
    )
    _, household, _ = await store.load_always_on("sub-kendra", "thread-1")
    assert [m.content for m in household] == ["Trash goes out Sunday"]


async def test_lexical_search_finds_a_matching_episode(pool):
    await _insert(pool, content="We decided to replace the dishwasher in March")
    await _insert(pool, content="The car needs an oil change")
    found = await store.search_episodic_lexical("sub-noah", "dishwasher", limit=10)
    assert [m.content for m in found] == [
        "We decided to replace the dishwasher in March"
    ]


async def test_lexical_search_matches_on_subject_when_the_text_does_not(pool):
    """Entity matching is the arm that carries names, and names are most of
    family memory. FTS on 'cooper' would miss a row phrased 'he needs a walk'."""
    await _insert(pool, subject="cooper", content="He needs a walk before 7")
    found = await store.search_episodic_lexical("sub-noah", "how is Cooper", limit=10)
    assert len(found) == 1


async def test_lexical_search_excludes_superseded_rows(pool):
    old = await _insert(pool, content="Kendra works Tuesdays")
    new = await _insert(pool, content="Kendra works Wednesdays")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_memory SET superseded_by=%s,"
            " superseded_why='contradicted' WHERE id=%s",
            (new, old),
        )
    found = await store.search_episodic_lexical("sub-noah", "Kendra works", limit=10)
    assert [m.content for m in found] == ["Kendra works Wednesdays"]


async def test_vector_search_returns_the_nearest_row(pool):
    from eve.memory.embed import to_pgvector

    await _insert(pool, content="near", embedding=to_pgvector(_VEC))
    await _insert(
        pool, content="far", embedding=to_pgvector([1.0] + [0.0] * 1535)
    )
    found = await store.search_episodic_vector("sub-noah", _VEC, limit=1)
    assert [m.content for m in found] == ["near"]


async def test_vector_search_ignores_rows_with_no_embedding(pool):
    """Rows are written before they are embedded, and the embedding call can
    fail. A NULL embedding must not become a spurious nearest neighbour."""
    await _insert(pool, content="unembedded", embedding=None)
    assert await store.search_episodic_vector("sub-noah", _VEC, limit=5) == []


async def test_lexical_search_does_not_leak_another_members_episode(pool):
    """`load_always_on` already proves member isolation for profile facts;
    the episodic search arms have their own scope predicate and need their
    own proof, or a regression here would leak one member's memories to
    another through search rather than through the always-on load."""
    await _insert(
        pool, scope_id="sub-kendra", content="Kendra's secret dishwasher plan"
    )
    found = await store.search_episodic_lexical("sub-noah", "dishwasher", limit=10)
    assert found == []


async def test_lexical_search_finds_household_episodes_for_any_member(pool):
    """The other half of the scope predicate: a household-scoped episode is
    not owned by whichever member happens to match scope_id, it is visible to
    everyone."""
    await _insert(
        pool,
        scope_kind="household",
        scope_id="",
        content="We decided to replace the dishwasher in March",
    )
    found = await store.search_episodic_lexical("sub-kendra", "dishwasher", limit=10)
    assert [m.content for m in found] == [
        "We decided to replace the dishwasher in March"
    ]


async def test_vector_search_does_not_leak_another_members_episode(pool):
    """Same isolation proof as the lexical arm, for the nearest-neighbour
    path: a member-scoped embedding must not surface for a different
    caller, no matter how close the vector."""
    from eve.memory.embed import to_pgvector

    await _insert(pool, scope_id="sub-kendra", embedding=to_pgvector(_VEC))
    found = await store.search_episodic_vector("sub-noah", _VEC, limit=5)
    assert found == []


def test_subjects_in_lowercases_and_drops_stopwords():
    assert store.subjects_in("How is Cooper doing?") == ["cooper", "doing"]
