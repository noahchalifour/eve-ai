"""Rejoin the two migration heads that EVE-4 and EVE-20 grew independently.

Revision ID: 0008_merge_coding_and_wardrobe
Revises: 0005_eve_coding_session, 0007_eve_wardrobe_asset

`0005_eve_coding_session` (the coding sessions branch) and
`0005_eve_oauth_token -> 0006_eve_wardrobe_item -> 0007_eve_wardrobe_asset`
(the wardrobe branch) both name `0004_eve_computer_task` as their parent, so
after both merged to main `alembic upgrade head` had two heads to choose
between and refused - which is a startup crash in `eve-migrate`, not a
degraded feature, because nothing runs until the schema is settled.

Empty on purpose: the two branches touch disjoint tables and neither depends
on the other's schema, so there is nothing to reconcile. This revision only
gives the graph a single head again.
"""

revision = "0008_merge_coding_and_wardrobe"
down_revision = ("0005_eve_coding_session", "0007_eve_wardrobe_asset")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
