"""eve_routine.kind: routines and reminders share one table (ENG-372).

A reminder is a one-shot row (`cadence = {"once_at": ...}`) whose message is
delivered verbatim with no model call. It reuses the routine's claim lease,
failure counter, expiry and Routines screen rather than a second scheduler;
`kind` is what lets the tools and the screen tell the two apart.

Defaulted so every existing row reads as the routine it already is.
"""
from alembic import op

revision = "0016_eve_routine_kind"
down_revision = "0015_eve_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE eve_routine ADD COLUMN kind text NOT NULL DEFAULT 'routine'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE eve_routine DROP COLUMN kind")
