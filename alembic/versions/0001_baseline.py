"""Baseline: Phases 1-5b, reproduced idempotently.

Revision ID: 0001_baseline
Revises: None

A no-op against a database already carrying eve_schema_version's five
entries, because every statement below is IF NOT EXISTS. On a fresh database
it creates everything. That is what lets one image serve both.

`eve_schema_version` is deliberately left in place and unused: dropping it
would make a rollback to the previous image fail on a table it expects.
"""
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # --- 0001_memory ---
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS eve_memory (
          id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          layer          text        NOT NULL,
          scope_kind     text        NOT NULL,
          scope_id       text        NOT NULL,
          kind           text        NOT NULL,
          subject        text,
          content        text        NOT NULL,
          confidence     real        NOT NULL DEFAULT 0.7,
          salience       real        NOT NULL DEFAULT 0.5,
          source_thread  text,
          source_run     text,
          created_at     timestamptz NOT NULL DEFAULT now(),
          last_seen_at   timestamptz NOT NULL DEFAULT now(),
          superseded_by  uuid REFERENCES eve_memory(id) ON DELETE SET NULL,
          superseded_why text,
          embedding      vector(1536),
          content_tsv    tsvector GENERATED ALWAYS AS
                           (to_tsvector('english', content)) STORED
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_memory_tsv"
        " ON eve_memory USING gin (content_tsv)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_memory_embedding"
        " ON eve_memory USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_memory_scope"
        " ON eve_memory (scope_kind, scope_id, layer)"
        " WHERE superseded_why IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_memory_subject"
        " ON eve_memory (subject) WHERE superseded_why IS NULL"
    )
    # --- 0002_ambient ---
    op.execute(
        """
        -- Dedup and cooldown for ambient signals (Phase 4, design section
        -- 4.5). There is deliberately no cursor table: every source is
        -- time-windowed or content-keyed, so this table alone gives
        -- exactly-once delivery.
        CREATE TABLE IF NOT EXISTS eve_ambient_seen (
          source        text        NOT NULL,
          key           text        NOT NULL,
          last_seen_at  timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (source, key)
        )
        """
    )
    op.execute(
        """
        -- Every notification actually sent. This IS the daily-cap counter
        -- (counted per member per local day) and the record of what Eve
        -- chose to interrupt, which is Phase 5's training signal.
        CREATE TABLE IF NOT EXISTS eve_ambient_notice (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          member_sub text        NOT NULL,
          source     text        NOT NULL,
          key        text        NOT NULL,
          urgent     boolean     NOT NULL DEFAULT false,
          thread_id  text,
          sent_at    timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_ambient_notice_member_sent"
        " ON eve_ambient_notice (member_sub, sent_at DESC)"
    )
    # --- 0003_ambient_notice_window ---
    op.execute(
        """
        -- Supports store.already_notified's cooldown-bounded lookup (fix
        -- round 2 on the ambient pipeline task): "has this member already
        -- been told about this (source, key) within its cooldown window",
        -- run once per member per signal. Without this the query would
        -- fall back to a sequential scan of eve_ambient_notice.
        CREATE INDEX IF NOT EXISTS eve_ambient_notice_member_source_key_sent
          ON eve_ambient_notice (member_sub, source, key, sent_at DESC)
        """
    )
    # --- 0004_pat ---
    op.execute(
        """
        -- Personal access tokens: one long-lived credential per scripted
        -- client, individually revocable (eve/pat.py). Not memory, but it
        -- shares this pool and this migration list rather than standing up a
        -- second of each for one table.
        --
        -- The token itself is never stored, only its sha256. A dump of this
        -- table therefore does not yield a working credential. token_hash is
        -- the primary key, which is also the index the auth path reads by.
        CREATE TABLE IF NOT EXISTS eve_pat (
          token_hash   text        PRIMARY KEY,
          sub          text        NOT NULL,
          label        text        NOT NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          last_used_at timestamptz,
          revoked_at   timestamptz
        )
        """
    )
    op.execute(
        """
        -- Revocation is by label, so two live tokens must not share one.
        -- Partial, so a revoked label can be reused for a replacement token.
        CREATE UNIQUE INDEX IF NOT EXISTS eve_pat_active_label
          ON eve_pat (label) WHERE revoked_at IS NULL
        """
    )
    # --- 0005_eval ---
    # Phase 5b. Three changes in ONE entry deliberately: db.py's own
    # guidance says move to Alembic past ~5 entries, and Phase 5c is where
    # that happens. Splitting these into three would cross the line here
    # instead, for no benefit.
    op.execute(
        """
        -- The reply IS the label for notification precision (design 5): a
        -- member who answers found the interruption worth receiving.
        -- Populated only from this deploy forward; earlier rows stay
        -- permanently unlabelled and are excluded from the dataset.
        ALTER TABLE eve_ambient_notice
          ADD COLUMN IF NOT EXISTS replied_at timestamptz
        """
    )
    op.execute(
        """
        -- Every filter verdict, with the Signal that produced it. Phase 4
        -- records neither: eve_ambient_seen keeps only (source, key), and
        -- eve_ambient_notice keeps no signal content, so a replayable
        -- dataset item cannot be reconstructed from either (design 4.2).
        CREATE TABLE IF NOT EXISTS eve_ambient_decision (
          id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          source     text        NOT NULL,
          key        text        NOT NULL,
          signal     jsonb       NOT NULL,
          verdict    jsonb       NOT NULL,
          decided_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_ambient_decision_decided"
        " ON eve_ambient_decision (decided_at DESC)"
    )
    op.execute(
        """
        -- Local and authoritative. The gate reads this, never Langfuse, so a
        -- reporting outage cannot block a regression check (design 7.1).
        -- `scores` is jsonb because the scorer set will change and a
        -- migration per metric is machinery for a table read by one CLI.
        CREATE TABLE IF NOT EXISTS eve_eval_run (
          id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          dataset     text        NOT NULL,
          arm         text        NOT NULL DEFAULT 'with-rules',
          git_sha     text,
          item_count  integer     NOT NULL,
          scores      jsonb       NOT NULL,
          created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS eve_eval_run_dataset_created"
        " ON eve_eval_run (dataset, arm, created_at DESC)"
    )


def downgrade() -> None:
    raise NotImplementedError("the baseline is not reversible")
