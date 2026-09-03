"""Track successfully catalogued Immich assets independently of garments.

Revision ID: 0007_eve_wardrobe_asset
Revises: 0006_eve_wardrobe_item

A valid photo can contain no garments. The per-asset state makes that empty
result durable while the existing item table stays one row per garment.
Existing item rows become state rows during upgrade so databases already at
0006 retain their normal-sync idempotence. Round-one numbering placed this
table at 0005_eve_wardrobe_item / 0006_eve_wardrobe_asset before main's own
0005 landed; the operations are replay-safe for databases stamped at either
numbering.
"""
from alembic import op

revision = "0007_eve_wardrobe_asset"
down_revision = "0006_eve_wardrobe_item"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS eve_wardrobe_asset (
          member_sub     text        NOT NULL,
          asset_id       text        NOT NULL,
          catalogued_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (member_sub, asset_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO eve_wardrobe_asset (member_sub, asset_id)
        SELECT DISTINCT member_sub, asset_id FROM eve_wardrobe_item
        ON CONFLICT (member_sub, asset_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_wardrobe_asset")
