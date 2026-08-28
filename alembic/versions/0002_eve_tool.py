"""Eve-authored executable tools.

Revision ID: 0002_eve_tool
Revises: 0001_baseline

A real table rather than a memory layer, unlike Phase 5a's rules and
procedures: this row is executable, its approval binds to a hash, it needs a
uniqueness constraint, and it must never be reachable by semantic recall into
a prompt. A text `content` column with an embedding is the wrong shape in
every one of those respects.
"""
from alembic import op

revision = "0002_eve_tool"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_tool (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          name           text        NOT NULL,
          description    text        NOT NULL,
          args_schema    jsonb       NOT NULL,
          source         text        NOT NULL,
          source_sha256  text        NOT NULL,
          proposed_by    text        NOT NULL,
          proposed_at    timestamptz NOT NULL DEFAULT now(),
          source_thread  text,
          source_run     text,
          approved_by    text,
          approved_at    timestamptz,
          rejected_why   text,
          revoked_at     timestamptz,
          revoked_why    text,
          invocations    bigint      NOT NULL DEFAULT 0,
          last_used_at   timestamptz
        )
        """
    )
    # One live approved version per name, while unapproved proposals and
    # revoked history accumulate freely. A revoked name can be reused by a
    # replacement - the same pattern eve_pat_active_label uses for tokens.
    op.execute(
        "CREATE UNIQUE INDEX eve_tool_live_name ON eve_tool (name)"
        " WHERE approved_at IS NOT NULL AND revoked_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_tool")
