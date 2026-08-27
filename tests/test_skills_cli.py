import pytest


async def test_revoke_supersedes_and_never_deletes(monkeypatch):
    """The row is the audit trail. forget() is a hard DELETE and is the wrong
    verb here."""
    from eve.skills import cli

    calls = []

    async def supersede(old, new, why):
        calls.append((old, new, why))

    async def forget(mid):
        raise AssertionError("revoke must not hard-delete")

    monkeypatch.setattr(cli, "supersede", supersede)
    monkeypatch.setattr(cli, "forget", forget, raising=False)

    await cli.revoke("abc-123", "noisy")
    assert calls == [("abc-123", None, "revoked by operator: noisy")]


async def test_authored_lists_rules_and_procedures(monkeypatch):
    from datetime import UTC, datetime

    from eve.memory.types import Memory
    from eve.skills import cli

    now = datetime(2026, 8, 27, tzinfo=UTC)
    rows = [
        Memory(
            id="r1", layer="rule", scope_kind="member", scope_id="sub-noah",
            kind="preference", subject=None, content="Lead with the number.",
            confidence=0.8, salience=0.5, created_at=now, last_seen_at=now,
        )
    ]

    async def fetch(sql, params):
        assert "rule" in sql and "procedure" in sql
        return rows

    monkeypatch.setattr(cli, "_fetch", fetch)
    assert await cli.authored() == rows
