"""Rejoin the two migration heads that EVE-25 and EVE-26 grew independently.

Revision ID: 0012_merge_routines_and_linear
Revises: 0011_eve_routine, 0011_eve_coding_session_linear

`0011_eve_routine` (the scheduled-routines branch) and
`0011_eve_coding_session_linear` (the Linear-agent branch) both name
`0010_eve_widget_resource` as their parent, so once both merged to main
`alembic upgrade head` had two heads to choose between and refused - which
is a startup crash in `eve-migrate`, not a degraded feature, because
nothing runs until the schema is settled. The same collision `0008` already
documents once.

Empty on purpose: the two branches touch disjoint tables (`eve_routine` and
`eve_coding_session`'s new Linear columns) and neither depends on the
other's schema, so there is nothing to reconcile. This revision only gives
the graph a single head again.
"""

revision = "0012_merge_routines_and_linear"
down_revision = ("0011_eve_routine", "0011_eve_coding_session_linear")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
