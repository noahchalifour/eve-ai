"""tests/test_images_migration.py"""
import importlib.util
from pathlib import Path


def _migration():
    path = Path("alembic/versions/0014_eve_image.py")
    spec = importlib.util.spec_from_file_location("m0014", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0014_follows_0013_and_creates_the_table(monkeypatch):
    migration = _migration()
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert migration.down_revision == "0013_eve_coding_session_review"
    sql = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS eve_image" in sql
    assert "bytes bytea NOT NULL" in sql
    assert "UNIQUE (member_sub, source_ref)" in sql
    assert "eve_image_expires_at_idx" in sql
