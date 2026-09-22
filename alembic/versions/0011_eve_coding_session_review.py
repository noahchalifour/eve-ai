"""Review columns on eve_coding_session (EVE-27).

A review is a coding session with a different beginning and a different
ending, so it is a `kind` on the existing row rather than a second table:
every query, the supervisor loop, the stale timeout, and the ambient source
are identical for both, and a parallel table would duplicate all of it to
express one enum.

A review triggered by a GitHub webhook has no member-owned conversation
thread to attach to - nobody was chatting when GitHub fired the hook - so
`thread_id` becomes nullable, guarded by a check constraint: every `code`
session still needs a thread, only `review` sessions may go without one.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_eve_coding_session_review"
down_revision = "0010_eve_widget_resource"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "eve_coding_session",
        sa.Column("kind", sa.Text(), nullable=False, server_default="code"),
    )
    op.add_column(
        "eve_coding_session", sa.Column("pr_number", sa.Integer(), nullable=True)
    )
    op.add_column(
        "eve_coding_session", sa.Column("head_sha", sa.Text(), nullable=True)
    )
    # The idempotence lookup: "has this exact commit already been reviewed".
    # Partial, because it answers a question only review rows can be asked.
    op.create_index(
        "eve_coding_session_review_commit",
        "eve_coding_session",
        ["pr_number", "head_sha"],
        postgresql_where=sa.text("kind = 'review'"),
    )
    op.alter_column("eve_coding_session", "thread_id", nullable=True)
    op.create_check_constraint(
        "eve_coding_session_review_or_threaded",
        "eve_coding_session",
        "kind = 'review' OR thread_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("eve_coding_session_review_or_threaded", "eve_coding_session", type_="check")
    op.alter_column("eve_coding_session", "thread_id", nullable=False)
    op.drop_index("eve_coding_session_review_commit", table_name="eve_coding_session")
    op.drop_column("eve_coding_session", "head_sha")
    op.drop_column("eve_coding_session", "pr_number")
    op.drop_column("eve_coding_session", "kind")
