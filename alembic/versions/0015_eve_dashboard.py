"""eve_dashboard: one agent-authored dashboard per member per device (ENG-72).

`device_id` is minted by the client on first launch. It is a locator scoped
by `member_sub`, never authorization: every query carries both.

`layout` is the grid placement of each member widget, in cells of a grid
`columns` wide. Placement lives here rather than on the widget rows because
it is the dashboard's truth, written in one revision-guarded UPDATE.

Every tile is an ordinary library widget (`eve_widget_resource`),
referenced by id from `layout`. There is deliberately no foreign key: a
dashboard never owns a widget, so resetting one deletes nothing from the
library, and a widget deleted from the library simply drops out of the
layout on the next read.
"""

from alembic import op

revision = "0015_eve_dashboard"
down_revision = "0014_eve_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_dashboard (
          id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub  text        NOT NULL,
          device_id   text        NOT NULL,
          purpose     text        NOT NULL,
          columns     integer     NOT NULL,
          layout      jsonb       NOT NULL DEFAULT '[]'::jsonb,
          revision    bigint      NOT NULL DEFAULT 1,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          UNIQUE (member_sub, device_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eve_dashboard")
