"""One row per member-recorded entry, in any domain.

Revision ID: 0009_eve_record
Revises: 0008_merge_coding_and_wardrobe

There is deliberately no per-domain table. A workout set, a reading session
and a finished chore are the same shape - "this member recorded this, then" -
and differ only by `collection` and the keys inside `payload`. ADR 0008 made
the same call for authored behaviour, storing rules and procedures as
`eve_memory` layers rather than building a store per kind; ADR 0019 records
this one.

`occurred_at` is a real column rather than a payload key because the whole
reason this table exists instead of `eve_memory` is range aggregation: a
chart over ninety days must be an index scan, not a sequential scan that
parses jsonb. `payload` holds everything domain-specific and is never
queried structurally here.

`key` is optional and, when present, unique per (member, collection): it is
how a re-submitted append is recognised as the same entry rather than
inserted twice.
"""
from alembic import op

revision = "0009_eve_record"
down_revision = "0008_merge_coding_and_wardrobe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_record (
          id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub  text        NOT NULL,
          collection  text        NOT NULL,
          key         text,
          payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Every read is "this member's entries in this collection, over a window".
    op.execute(
        "CREATE INDEX eve_record_member_collection_time"
        " ON eve_record (member_sub, collection, occurred_at DESC)"
    )
    # Idempotency, only for appends that supplied a key.
    op.execute(
        "CREATE UNIQUE INDEX eve_record_member_collection_key"
        " ON eve_record (member_sub, collection, key)"
        " WHERE key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE eve_record")