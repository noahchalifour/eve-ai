"""Regression coverage for the immutable wardrobe migration chain."""
import importlib.util
from pathlib import Path


def _migration(filename):
    path = Path("alembic/versions") / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_asset_state_revision_upgrades_from_0005_and_backfills_existing_assets(monkeypatch):
    migration = _migration("0006_eve_wardrobe_asset.py")
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert migration.down_revision == "0005_eve_wardrobe_item"
    assert "CREATE TABLE IF NOT EXISTS eve_wardrobe_asset" in statements[0]
    assert "SELECT DISTINCT member_sub, asset_id FROM eve_wardrobe_item" in statements[1]


def test_asset_state_revision_replays_when_round_one_already_created_the_table(monkeypatch):
    migration = _migration("0006_eve_wardrobe_asset.py")
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert "CREATE TABLE IF NOT EXISTS eve_wardrobe_asset" in statements[0]
    assert "ON CONFLICT (member_sub, asset_id) DO NOTHING" in statements[1]


def test_0005_remains_the_immutable_garment_only_schema(monkeypatch):
    migration = _migration("0005_eve_wardrobe_item.py")
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert all("eve_wardrobe_asset" not in statement for statement in statements)
