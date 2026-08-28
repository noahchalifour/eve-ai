import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def clean_pool(monkeypatch):
    monkeypatch.setenv(
        "EVE_DATABASE_URL", "postgresql://eve:eve@127.0.0.1:15432/eve"
    )
    from eve.memory import db
    from eve.settings import get_settings

    get_settings.cache_clear()
    await db.close_pool()
    await db.migrate()
    pool = await db.get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "TRUNCATE eve_ambient_decision, eve_eval_run, eve_ambient_notice"
        )
    yield pool
    await db.close_pool()


async def test_a_seeded_regression_fails_the_gate(clean_pool):
    """DoD 5: two runs, the second worse, gate exits non-zero - with Langfuse
    never contacted."""
    from eve.eval.store import gate, record_run
    from eve.eval.types import RunScore

    await record_run(RunScore("ambient", "with-rules", 10, {"notify_agreement": 90.0}), "sha1")
    code, reasons = await gate("ambient")
    assert code == 0

    await record_run(RunScore("ambient", "with-rules", 10, {"notify_agreement": 70.0}), "sha2")
    code, reasons = await gate("ambient")
    assert code == 1
    assert any("notify_agreement" in r for r in reasons)


async def test_a_decision_round_trips_into_a_dataset_item(clean_pool):
    """DoD 0 and 1: recorded verdict -> replayable dataset item."""
    from datetime import UTC, datetime

    from eve.eval.datasets import build_ambient
    from eve_ambient.store import record_decision
    from eve_ambient.types import FilterVerdict, Signal

    await record_decision(
        Signal(
            source="mail", key="k1", occurred_at=datetime(2026, 8, 27, tzinfo=UTC),
            member_sub="sub-noah", summary="A package shipped.", payload={"x": 1},
        ),
        FilterVerdict(notify=True, audience=["sub-noah"], why="worth it"),
    )
    items = await build_ambient(limit=10)

    assert len(items) == 1
    assert items[0].input["signal"]["summary"] == "A package shipped."
    assert items[0].expected["notify"] is True


async def test_the_reply_label_is_stamped_and_read(clean_pool):
    """DoD 3: a member turn in an ambient thread stamps replied_at, and the
    dataset join picks it up."""
    from datetime import UTC, datetime

    from eve.eval.datasets import build_ambient
    from eve_ambient.store import mark_replied, record_decision, record_notice
    from eve_ambient.types import FilterVerdict, Signal

    signal = Signal(
        source="mail", key="k1", occurred_at=datetime(2026, 8, 27, tzinfo=UTC),
        member_sub="sub-noah", summary="A package shipped.",
    )
    await record_decision(signal, FilterVerdict(notify=True, audience=["sub-noah"], why="w"))
    await record_notice("sub-noah", "mail", "k1", False, "thread-1")
    await mark_replied("thread-1")

    items = await build_ambient(limit=10)
    assert items[0].expected["replied"] is True
