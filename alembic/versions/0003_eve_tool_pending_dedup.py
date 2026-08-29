"""Backstop the interrupt-replay dedup invariant at the database level.

Revision ID: 0003_eve_tool_pending_dedup
Revises: 0002_eve_tool

`store.propose()`'s existence-check-then-insert is a friendly-result guard,
not the invariant: it runs with no `SELECT ... FOR UPDATE`, so two genuinely
concurrent proposals with the same dedup key could both pass the SELECT
before either commits, still producing a duplicate pending row. The approved
case already has exactly this shape of protection (`eve_tool_live_name`, a
real partial unique index) - this gives the pending/undecided case the same
guarantee.

`source_run` is deliberately not part of the index: Aegra mints a fresh run
id for every HTTP submission, including the resume that carries
`Command(resume=...)`, so the pre-interrupt call and the replayed call never
share a run id. `source_thread` is what actually survives the pause/resume
boundary, matching the WHERE-clause change in `store.propose()`.
"""
from alembic import op

revision = "0003_eve_tool_pending_dedup"
down_revision = "0002_eve_tool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX eve_tool_pending_dedup"
        " ON eve_tool (name, source_sha256, source_thread)"
        " WHERE approved_at IS NULL AND rejected_why IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX eve_tool_pending_dedup")
