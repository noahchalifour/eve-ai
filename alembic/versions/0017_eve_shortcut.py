"""eve_shortcut and eve_shortcut_observation: learned shortcuts (ENG-296).

An observation is one specialist run that reduced to a single allowlisted
eve-tools call (`eve.shortcuts.capture`). When a fingerprint gathers enough
uncontradicted observations inside the window, it is promoted to a shortcut
row, which Eve calls directly with `run_shortcut`.

A table, not a memory layer (ADR 0021): a shortcut is structured, counted and
executed - never rendered as prose the model reads as fact.

`fingerprint` hashes the call with its variant argument removed, so "lights
on" and "lights off" on one entity are one shortcut whose `variants` lists
the observed services. Only observed values are ever legal.
"""
from alembic import op

revision = "0017_eve_shortcut"
down_revision = "0016_eve_routine_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE eve_shortcut_observation (
          id           uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub   text        NOT NULL,
          fingerprint  text        NOT NULL,
          specialist   text        NOT NULL,
          tool         text        NOT NULL,
          variant      text,
          args         jsonb       NOT NULL,
          phrasing     text        NOT NULL DEFAULT '',
          thread_id    text,
          contradicted boolean     NOT NULL DEFAULT false,
          observed_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX eve_shortcut_observation_member_fp"
        " ON eve_shortcut_observation (member_sub, fingerprint, observed_at DESC)"
    )
    op.execute(
        "CREATE INDEX eve_shortcut_observation_thread"
        " ON eve_shortcut_observation (member_sub, thread_id, observed_at DESC)"
    )
    op.execute(
        """
        CREATE TABLE eve_shortcut (
          id                   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub           text        NOT NULL,
          fingerprint          text        NOT NULL,
          name                 text        NOT NULL,
          specialist           text        NOT NULL,
          tool                 text        NOT NULL,
          fixed_args           jsonb       NOT NULL,
          variant_key          text,
          variants             jsonb       NOT NULL DEFAULT '[]'::jsonb,
          phrasings            jsonb       NOT NULL DEFAULT '[]'::jsonb,
          permission           text        NOT NULL,
          hits                 integer     NOT NULL DEFAULT 0,
          consecutive_failures integer     NOT NULL DEFAULT 0,
          status               text        NOT NULL DEFAULT 'active',
          last_used_at         timestamptz,
          created_at           timestamptz NOT NULL DEFAULT now(),
          updated_at           timestamptz NOT NULL DEFAULT now(),
          UNIQUE (member_sub, fingerprint)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX eve_shortcut_member_name"
        " ON eve_shortcut (member_sub, name) WHERE status = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS eve_shortcut")
    op.execute("DROP TABLE IF EXISTS eve_shortcut_observation")
