"""Alembic environment for Eve's own schema.

Two things here are load-bearing and neither is default:

- `version_table="eve_alembic_version"`. Aegra runs its own Alembic
  migrations at startup against the same database and the default
  `alembic_version` table. Sharing it would let two independent histories
  stamp over each other.
- The URL comes from eve.settings, not from alembic.ini, so there is one
  source of truth for the connection string and no credential in a file.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from eve.settings import get_settings

VERSION_TABLE = "eve_alembic_version"


def _url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("EVE_DATABASE_URL (or DATABASE_URL) is unset")
    # psycopg 3 driver, matching the runtime pool.
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=None,
        literal_binds=True,
        version_table=VERSION_TABLE,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            version_table=VERSION_TABLE,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
