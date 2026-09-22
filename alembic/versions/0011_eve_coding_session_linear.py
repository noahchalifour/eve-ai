"""The Linear side of a coding session.

Revision ID: 0011_eve_coding_session_linear
Revises: 0010_eve_widget_resource

TWO COLUMNS RATHER THAN A TABLE. The relationship is strictly one to one: a
Linear agent session drives exactly one coding session, and a coding session
has at most one Linear session. A separate table buys a join on that, in
exchange for a generalization that pays off only when a second ticketing
integration exists.

THE UNIQUE CONSTRAINT IS THE IDEMPOTENCY KEY, not an access path. Linear
retries on a 5xx and on a timeout; a retried `created` that dispatches a
second session spends real money and opens a second pull request for one
request. The insert conflict is the dedup, and unlike an in-memory guard it
survives a restart. Postgres treats NULLs as distinct in a unique index, so
every chat-dispatched session (which has no Linear side) is unaffected.
"""
from alembic import op

revision = "0011_eve_coding_session_linear"
down_revision = "0010_eve_widget_resource"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE eve_coding_session
          ADD COLUMN linear_session_id text,
          ADD COLUMN linear_issue_id   text,
          ADD COLUMN linear_emitted_at timestamptz
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX eve_coding_session_linear_session"
        " ON eve_coding_session (linear_session_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS eve_coding_session_linear_session")
    op.execute(
        """
        ALTER TABLE eve_coding_session
          DROP COLUMN linear_session_id,
          DROP COLUMN linear_issue_id,
          DROP COLUMN linear_emitted_at
        """
    )
