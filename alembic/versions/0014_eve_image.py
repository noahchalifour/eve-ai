"""eve_image: image bytes addressed by opaque id (EVE-21, spec 2.1).

bytea in Eve's own Postgres rather than SeaweedFS or a PVC: nothing new to
deploy, CNPG backups already cover it, and retention is a DELETE. Revisit
if the table passes a few GB.

`UNIQUE (member_sub, source_ref)` is the Immich dedupe. Postgres treats NULLs
as distinct, so uploads (source_ref NULL) never collide with each other.
"""

from alembic import op

revision = "0014_eve_image"
down_revision = "0013_eve_coding_session_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS eve_image (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            member_sub text NOT NULL,
            thread_id text,
            origin text NOT NULL CHECK (origin IN ('upload', 'immich')),
            source_ref text,
            content_type text NOT NULL,
            bytes bytea NOT NULL,
            width integer NOT NULL,
            height integer NOT NULL,
            caption text,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            UNIQUE (member_sub, source_ref)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_image_expires_at_idx ON eve_image (expires_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_image_member_thread_idx"
        " ON eve_image (member_sub, thread_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eve_image")
