"""Eve's own record of a delegated coding session.

Revision ID: 0005_eve_coding_session
Revises: 0004_eve_computer_task

The box keeps its sessions in memory and loses them on restart, exactly as
it does for GUI tasks; this table is Eve's side of that boundary. Two
columns exist that `eve_computer_task` has no equivalent for:

`cursor` is how much of the box's turn log Eve has already read. It is
Eve's bookmark, not the box's - a restart on either side must not replay a
conversation she has already reasoned over.

`context` is the recall snapshot taken once, at creation. The supervisor
runs every ~20s and a hybrid recall per tick would be indefensible; taking
it once is what makes running the supervisor in Eve's container - the whole
reason it lives there - affordable.
"""
from alembic import op

revision = "0005_eve_coding_session"
down_revision = "0004_eve_computer_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_coding_session (
          id               text        PRIMARY KEY,
          member_sub       text        NOT NULL,
          thread_id        text        NOT NULL,
          goal             text        NOT NULL,
          agent            text        NOT NULL,
          model            text        NOT NULL,
          repos            jsonb       NOT NULL,
          context          text        NOT NULL DEFAULT '',
          status           text        NOT NULL DEFAULT 'running',
          cursor           integer     NOT NULL DEFAULT 0,
          supervisor_turns integer     NOT NULL DEFAULT 0,
          result           jsonb,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          finished_at      timestamptz
        )
        """
    )
    # The supervisor's own query, every tick: "every session still live."
    op.execute(
        "CREATE INDEX eve_coding_session_status"
        " ON eve_coding_session (status, updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_coding_session")
