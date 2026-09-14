"""One row per saved, refreshable widget.

Revision ID: 0010_eve_widget_resource
Revises: 0009_eve_record

`recipe` is the declarative read the snapshot route executes: which sources
to read and how to shape them. It is jsonb and validated in Python
(`eve.widgets.recipe`) rather than by columns, because the set of legal
sources grows and a migration per source is the per-domain cost this whole
feature exists to avoid.

`revision` is optimistic concurrency. Two devices can edit the same widget's
filters, and a slow refresh must not be able to overwrite a newer choice.

`kind` is the renderable family (`chart`), NOT a domain. Nothing here knows
what the recipe reads.
"""
from alembic import op

revision = "0010_eve_widget_resource"
down_revision = "0009_eve_record"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_widget_resource (
          id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub  text        NOT NULL,
          kind        text        NOT NULL,
          title       text        NOT NULL,
          recipe      jsonb       NOT NULL,
          filters     jsonb       NOT NULL DEFAULT '{}'::jsonb,
          revision    bigint      NOT NULL DEFAULT 1,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX eve_widget_resource_member"
        " ON eve_widget_resource (member_sub, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_widget_resource")