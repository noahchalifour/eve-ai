"""The migration graph must have exactly one head.

Two branches that each add a migration name the same parent, merge cleanly
in git (different files, no textual conflict), and leave `alembic upgrade
head` with two heads to choose between - which it refuses to do. That is a
crash in `eve-migrate`, so Eve does not start at all. It happened once
already, between EVE-4's coding sessions and EVE-20's wardrobe.

Nothing else catches it: the unit tier never runs Alembic, and CI has no
database.
"""
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_the_migration_graph_has_exactly_one_head():
    heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
    assert len(heads) == 1, (
        f"{len(heads)} heads: {sorted(heads)}. Two branches added migrations "
        "from the same parent - add a merge revision "
        "(`uv run alembic merge -m 'merge' <rev> <rev>`)."
    )
