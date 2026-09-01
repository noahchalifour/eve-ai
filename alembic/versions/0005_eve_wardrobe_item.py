"""One row per garment, catalogued from an Immich asset by a vision pass.

Revision ID: 0005_eve_wardrobe_item
Revises: 0004_eve_computer_task

`item_index` exists because one photograph is not always one garment: a rail
of shirts or a folded stack is a natural thing to shoot, and a schema keyed
on the asset alone silently discards everything but the first item.

`attrs` is jsonb rather than columns because nothing queries on fabric. The
whole table is read at once and rendered as text for a prompt, and the vision
prompt's vocabulary will drift as it is tuned; a migration per drift buys
nothing. `name` and `category` stay real columns - they are the two stable
axes, used for grouping the rendered catalogue and for `eve-wardrobe list`.
"""
from alembic import op

revision = "0005_eve_wardrobe_item"
down_revision = "0004_eve_computer_task"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Asset state is deliberately separate from garment rows: a successful
    # vision pass can find no clothes, and that still must stop normal syncs
    # from re-processing the photograph forever.
    op.execute(
        """
        CREATE TABLE eve_wardrobe_asset (
          member_sub     text        NOT NULL,
          asset_id       text        NOT NULL,
          catalogued_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (member_sub, asset_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE eve_wardrobe_item (
          id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub     text        NOT NULL,
          asset_id       text        NOT NULL,
          item_index     int         NOT NULL DEFAULT 0,
          name           text        NOT NULL,
          category       text        NOT NULL,
          attrs          jsonb       NOT NULL DEFAULT '{}'::jsonb,
          catalogued_at  timestamptz NOT NULL DEFAULT now(),
          UNIQUE (member_sub, asset_id, item_index)
        )
        """
    )
    # Every read is "this member's whole wardrobe" (eve.wardrobe.store).
    op.execute(
        "CREATE INDEX eve_wardrobe_item_member"
        " ON eve_wardrobe_item (member_sub, category, name)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_wardrobe_asset")
    op.execute("DROP TABLE eve_wardrobe_item")
