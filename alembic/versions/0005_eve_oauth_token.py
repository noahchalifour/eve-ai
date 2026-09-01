"""Per-member OAuth tokens for the health providers.

Revision ID: 0005_eve_oauth_token
Revises: 0004_eve_computer_task

eve-tools' first piece of persistent state, and the reason ADR 0016 exists.
WHOOP returns a NEW refresh_token on every refresh, so the environment-variable
pattern every other eve-tools credential uses cannot hold one: it would go
stale on first use and auth would break at the next restart.

The DDL is here, in Eve's Alembic history, rather than in eve-tools: ADR 0016
grants eve-tools SELECT/INSERT/UPDATE on this table and nothing more - no DDL,
no other table.

`refresh_token` and `expires_at` are nullable so a non-rotating credential is
an ordinary row whose refresh path is simply never entered.
"""
from alembic import op

revision = "0005_eve_oauth_token"
down_revision = "0004_eve_computer_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_oauth_token (
          provider      text        NOT NULL,
          member_sub    text        NOT NULL,
          access_token  text        NOT NULL,
          refresh_token text,
          expires_at    timestamptz,
          updated_at    timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (provider, member_sub)
        )
        """
    )
    # No secondary index: every read is a point lookup on the full primary key.


def downgrade() -> None:
    op.execute("DROP TABLE eve_oauth_token")
