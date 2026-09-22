"""One row per standing instruction Eve runs on a schedule.

Revision ID: 0011_eve_routine
Revises: 0010_eve_widget_resource

`cadence` is jsonb and validated in Python (`eve.routines.cadence`) rather
than by columns, the same choice `0010_eve_widget_resource` made for the
widget recipe and for the same reason: the set of legal shapes grows, and a
migration per shape is the cost that avoids.

`timezone` is snapshotted from the member at creation rather than looked up
from family.yaml at fire time, so a member who moves keeps their existing
routines firing at the local times they chose.

`next_run_at` is stored rather than computed so that firing is a
compare-and-advance: the source claims a row by advancing this column before
emitting a signal, which is what stops a slow compose turn overlapping the
next tick from firing the same occurrence twice.
"""
from alembic import op

revision = "0011_eve_routine"
down_revision = "0010_eve_widget_resource"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_routine (
          id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub           text        NOT NULL,
          title                text        NOT NULL,
          instruction          text        NOT NULL,
          cadence              jsonb       NOT NULL,
          timezone             text        NOT NULL,
          status               text        NOT NULL DEFAULT 'active',
          next_run_at          timestamptz NOT NULL,
          last_run_at          timestamptz,
          last_outcome         text,
          consecutive_failures int         NOT NULL DEFAULT 0,
          expires_at           timestamptz,
          revision             bigint      NOT NULL DEFAULT 1,
          created_at           timestamptz NOT NULL DEFAULT now(),
          updated_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # The ambient tick's only query: "every active routine that is due."
    op.execute(
        "CREATE INDEX eve_routine_due ON eve_routine (status, next_run_at)"
    )
    # The screen's and list_routines' query, newest first.
    op.execute(
        "CREATE INDEX eve_routine_owner"
        " ON eve_routine (member_sub, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_routine")
