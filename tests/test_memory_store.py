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
    profile, household, digest, _rules = await store.load_always_on(
        "sub-noah", "thread-1"
    )
    assert [m.content for m in profile] == ["Noah is vegetarian"]
    assert [m.content for m in household] == ["The dog is Cooper"]
    assert digest == "They discussed dinner."


async def test_always_on_does_not_leak_another_members_profile(pool):
    """The isolation that matters most in this whole phase. A profile fact is
    the most personal thing Eve stores."""
    await _insert(
        pool, layer="profile", scope_id="sub-kendra", kind="fact", content="secret"
    )
    profile, _, _, _ = await store.load_always_on("sub-noah", "thread-1")
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
    _, household, _, _ = await store.load_always_on("sub-kendra", "thread-1")
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


async def test_add_returns_an_id_that_reads_back(pool):
    mid = await store.add(
        layer="profile",
        scope_kind="member",
        scope_id="sub-noah",
        kind="fact",
        content="Noah is vegetarian",
        subject="noah",
        source_thread="t1",
    )
    profile, _, _, _ = await store.load_always_on("sub-noah", "t1")
    assert [(m.id, m.content) for m in profile] == [(mid, "Noah is vegetarian")]


async def test_supersede_hides_the_old_row_but_keeps_it(pool):
    """The row survives because Phase 5's eval harness needs to answer 'what
    did Eve believe on the day she got that wrong'."""
    old = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="Kendra works Tuesdays",
    )
    new = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="Kendra works Wednesdays",
    )
    await store.supersede(old, new, "contradicted")
    profile, _, _, _ = await store.load_always_on("sub-noah", "t1")
    assert [m.content for m in profile] == ["Kendra works Wednesdays"]
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_memory")
        assert (await cur.fetchone())[0] == 2


async def test_forget_actually_deletes(pool):
    """'Eve, forget I said that' has to mean the row is gone. A tombstone
    that still holds the text is a quiet lie to a family member about their
    own data."""
    mid = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="something private",
    )
    await store.forget(mid)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM eve_memory")
        assert (await cur.fetchone())[0] == 0


async def test_reinforce_bumps_last_seen_and_salience(pool):
    mid = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="x",
    )
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE eve_memory SET last_seen_at = now() - interval '30 days'"
        )
    await store.reinforce(mid)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT salience, now() - last_seen_at < interval '1 minute'"
            " FROM eve_memory WHERE id=%s",
            (mid,),
        )
        salience, recent = await cur.fetchone()
        assert salience > 0.5
        assert recent


async def test_reinforce_clamps_salience_at_one(pool):
    """Otherwise a fact mentioned every day drifts to a salience no other
    memory can ever outrank, and the layer stops being sortable."""
    mid = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="x",
    )
    for _ in range(20):
        await store.reinforce(mid)
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT salience FROM eve_memory WHERE id=%s", (mid,))
        assert (await cur.fetchone())[0] <= 1.0


async def test_set_embeddings_makes_a_row_findable_by_vector(pool):
    mid = await store.add(
        layer="episodic", scope_kind="member", scope_id="sub-noah",
        kind="event", content="the dishwasher",
    )
    assert await store.search_episodic_vector("sub-noah", _VEC, limit=5) == []
    await store.set_embeddings([(mid, _VEC)])
    found = await store.search_episodic_vector("sub-noah", _VEC, limit=5)
    assert [m.id for m in found] == [mid]


async def test_upsert_digest_replaces_rather_than_accumulates(pool):
    await store.upsert_digest("t1", "first summary")
    await store.upsert_digest("t1", "second summary")
    _, _, digest, _ = await store.load_always_on("sub-noah", "t1")
    assert digest == "second summary"
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM eve_memory WHERE layer='digest'"
        )
        assert (await cur.fetchone())[0] == 1


async def test_eviction_retires_the_weakest_until_the_cap_is_met(pool):
    """Salience is deliberately anti-correlated with insertion order (and
    thus with recency): fact 0 is inserted first (oldest `last_seen_at`) but
    given the HIGHEST salience, fact 4 is inserted last (newest) but given
    the LOWEST. All five rows land within the same instant, so the decay
    factor is effectively identical across them and salience alone decides
    the order.

    If `evict_over_cap` ordered by recency/decay alone - e.g. a regression
    that dropped `salience *` from its ORDER BY - it would keep the most
    RECENTLY inserted rows (fact 2/3/4) instead of the ones this test
    expects (fact 0/1/2), so this test can only pass if salience is
    genuinely part of the ranking, not decay masquerading as it."""
    for i in range(5):
        mid = await store.add(
            layer="profile", scope_kind="member", scope_id="sub-noah",
            kind="fact", content=f"fact {i}",
        )
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE eve_memory SET salience=%s WHERE id=%s", ((4 - i) / 10.0, mid)
            )
    evicted = await store.evict_over_cap("profile", "member", "sub-noah", cap=3)
    profile, _, _, _ = await store.load_always_on("sub-noah", "t1")
    assert evicted == 2
    assert {m.content for m in profile} == {"fact 0", "fact 1", "fact 2"}


async def test_eviction_supersedes_rather_than_deletes(pool):
    """A mistakenly evicted fact has to be recoverable, and 'why did she
    forget that' has to have an answer."""
    for i in range(3):
        await store.add(
            layer="profile", scope_kind="member", scope_id="sub-noah",
            kind="fact", content=f"fact {i}",
        )
    await store.evict_over_cap("profile", "member", "sub-noah", cap=1)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) FROM eve_memory WHERE superseded_why='evicted'"
        )
        assert (await cur.fetchone())[0] == 2


async def test_eviction_is_idempotent(pool):
    """The subselect's `superseded_why IS NULL` guard exists precisely so
    that calling `evict_over_cap` twice with the same arguments does
    nothing the second time. Without that guard, every already-evicted row
    is re-selected on every run and the returned count is wrong forever -
    silently, with no error."""
    for i in range(5):
        mid = await store.add(
            layer="profile", scope_kind="member", scope_id="sub-noah",
            kind="fact", content=f"fact {i}",
        )
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE eve_memory SET salience=%s WHERE id=%s", (i / 10.0, mid)
            )
    first = await store.evict_over_cap("profile", "member", "sub-noah", cap=3)
    second = await store.evict_over_cap("profile", "member", "sub-noah", cap=3)
    profile, _, _, _ = await store.load_always_on("sub-noah", "t1")
    assert first == 2
    assert second == 0
    assert {m.content for m in profile} == {"fact 2", "fact 3", "fact 4"}


async def test_supersede_accepts_new_id_none_for_an_eviction(pool):
    """The brief requires `supersede(old_id, new_id, why)` to accept
    `new_id=None` for an eviction, which replaces nothing. Without a direct
    test, this branch is only exercised by evict_over_cap's own inline
    UPDATE - never through supersede() itself - and it would silently stay
    dead code if that changed."""
    old = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", content="an evicted-style fact",
    )
    await store.supersede(old, None, "evicted")
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT superseded_why, superseded_by FROM eve_memory WHERE id=%s",
            (old,),
        )
        why, by = await cur.fetchone()
        assert why == "evicted"
        assert by is None
    profile, _, _, _ = await store.load_always_on("sub-noah", "t1")
    assert profile == []


async def test_overlapping_finds_candidates_by_subject_and_by_vector(pool):
    by_subject = await store.add(
        layer="profile", scope_kind="member", scope_id="sub-noah",
        kind="fact", subject="kendra", content="Kendra works Tuesdays",
    )
    by_vector = await store.add(
        layer="episodic", scope_kind="member", scope_id="sub-noah",
        kind="event", content="unrelated words entirely",
    )
    await store.set_embeddings([(by_vector, _VEC)])
    found = await store.overlapping("sub-noah", ["kendra"], _VEC, limit=10)
    assert {m.id for m in found} == {by_subject, by_vector}


async def test_load_always_on_returns_rules_when_asked(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('rule','member','sub-noah','preference','Lead with the number.')"
        )
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('rule','household','','preference','Never text during dinner.')"
        )
    from eve.memory.store import load_always_on

    _p, _h, _d, rules = await load_always_on("sub-noah", None, include_rules=True)
    assert {r.content for r in rules} == {
        "Lead with the number.", "Never text during dinner.",
    }


async def test_load_always_on_omits_rules_by_default(pool):
    """With EVE_SELF_AUTHORING_ENABLED off, recall must not pay for or apply
    rules even if rows exist from an earlier enabled period."""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('rule','member','sub-noah','preference','Lead with the number.')"
        )
    from eve.memory.store import load_always_on

    _p, _h, _d, rules = await load_always_on("sub-noah", None)
    assert rules == []


async def test_load_always_on_excludes_another_members_rule(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('rule','member','sub-kid','preference','Use small words.')"
        )
    from eve.memory.store import load_always_on

    _p, _h, _d, rules = await load_always_on("sub-noah", None, include_rules=True)
    assert rules == []


async def test_load_always_on_excludes_a_superseded_rule(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory"
            " (layer, scope_kind, scope_id, kind, content, superseded_why)"
            " VALUES ('rule','member','sub-noah','preference','Old.','revoked')"
        )
    from eve.memory.store import load_always_on

    _p, _h, _d, rules = await load_always_on("sub-noah", None, include_rules=True)
    assert rules == []


async def test_load_always_on_never_returns_procedures(pool):
    """A procedure is on-demand only. Loading one into every prompt is the
    prompt-budget failure the two-layer split exists to prevent."""
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, content)"
            " VALUES ('procedure','member','sub-noah','decision','Step 1...')"
        )
    from eve.memory.store import load_always_on

    profile, household, _d, rules = await load_always_on(
        "sub-noah", None, include_rules=True
    )
    assert rules == [] and profile == [] and household == []


async def test_load_always_on_carries_source_thread_and_run(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory"
            " (layer, scope_kind, scope_id, kind, content, source_thread, source_run)"
            " VALUES ('profile','member','sub-noah','fact','x','thread-1','run-1')"
        )
    from eve.memory.store import load_always_on

    profile, _h, _d, _rules = await load_always_on("sub-noah", None)
    assert profile[0].source_thread == "thread-1"
    assert profile[0].source_run == "run-1"


async def test_load_procedures_returns_member_and_household_rows(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, subject, content)"
            " VALUES ('procedure','member','sub-noah','decision','a','A')"
        )
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, subject, content)"
            " VALUES ('procedure','household','','decision','b','B')"
        )
        await conn.execute(
            "INSERT INTO eve_memory (layer, scope_kind, scope_id, kind, subject, content)"
            " VALUES ('procedure','member','sub-kid','decision','c','C')"
        )
    from eve.memory.store import load_procedures

    rows = await load_procedures("sub-noah")
    assert {r.content for r in rows} == {"A", "B"}


async def test_procedure_by_name_ignores_superseded_rows(pool):
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO eve_memory"
            " (layer, scope_kind, scope_id, kind, subject, content, superseded_why)"
            " VALUES ('procedure','member','sub-noah','decision','a','old','revoked')"
        )
    from eve.memory.store import procedure_by_name

    assert await procedure_by_name("sub-noah", "a") is None


async def test_migration_count_is_five(pool):
    """Exactly at db.py's stated Alembic threshold. Phase 5c crosses it; this
    phase folds three changes into one entry to stay here."""
    from eve.memory import db

    assert len(db.MIGRATIONS) == 5


async def test_the_eval_tables_exist(pool):
    async with pool.connection() as conn:
        for table in ("eve_ambient_decision", "eve_eval_run"):
            cur = await conn.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            assert (await cur.fetchone())[0] == table


async def test_notice_has_replied_at(pool):
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='eve_ambient_notice' AND column_name='replied_at'"
        )
        assert await cur.fetchone() is not None
