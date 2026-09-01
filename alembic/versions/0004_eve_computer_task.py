"""Eve's own record of a dispatched computer task.

Revision ID: 0004_eve_computer_task
Revises: 0003_eve_tool_pending_dedup

Not the box's internal queue - eve-computer tracks its own tasks in memory
and loses them on restart (design doc: "Storage"). This table is Eve's
side of the boundary: what she dispatched, to whom, on which thread, and
what came back. `id` is NOT database-generated: it is minted by
`eve.computer.dispatch` before the box has ever heard of the task, so the
same id can be sent to both sides of the HTTP call.
"""
from alembic import op

revision = "0004_eve_computer_task"
down_revision = "0003_eve_tool_pending_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_computer_task (
          id           text        PRIMARY KEY,
          member_sub   text        NOT NULL,
          thread_id    text        NOT NULL,
          goal         text        NOT NULL,
          status       text        NOT NULL DEFAULT 'running',
          result       jsonb,
          created_at   timestamptz NOT NULL DEFAULT now(),
          updated_at   timestamptz NOT NULL DEFAULT now(),
          finished_at  timestamptz
        )
        """
    )
    # The poller's own query (Task 5): "every task I'm still waiting on."
    op.execute(
        "CREATE INDEX eve_computer_task_status"
        " ON eve_computer_task (status, updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_computer_task")
