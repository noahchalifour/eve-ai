"""tests/test_wardrobe_cli.py"""
from unittest.mock import AsyncMock

import pytest

from eve.family import Family, Member
from eve.wardrobe import cli


def _family():
    return Family(
        [
            Member("sub-noah", "Noah", "adult", "America/Vancouver", frozenset(), "album-n"),
            Member("sub-kendra", "Kendra", "adult", "America/Vancouver", frozenset(), None),
        ]
    )


async def test_targets_are_only_members_with_an_album(monkeypatch):
    monkeypatch.setattr(cli, "get_family", _family)
    assert [m.name for m in cli._targets(None)] == ["Noah"]


async def test_a_named_member_is_selected_case_insensitively(monkeypatch):
    monkeypatch.setattr(cli, "get_family", _family)
    assert [m.name for m in cli._targets("noah")] == ["Noah"]


async def test_an_unknown_member_name_raises(monkeypatch):
    monkeypatch.setattr(cli, "get_family", _family)
    with pytest.raises(SystemExit):
        cli._targets("nobody")


async def test_sync_reports_counts_per_member(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_family", _family)
    monkeypatch.setattr(
        cli.catalog,
        "sync",
        AsyncMock(
            return_value={
                "catalogued": 7,
                "removed": 1,
                "failed": 0,
                "remaining": 0,
                "error": None,
            }
        ),
    )

    await cli.run_sync(member=None, force=False)

    out = capsys.readouterr().out
    assert "Noah" in out
    assert "7" in out
    assert "removed 1" in out


async def test_sync_reports_an_error_without_crashing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_family", _family)
    monkeypatch.setattr(
        cli.catalog,
        "sync",
        AsyncMock(
            return_value={
                "catalogued": 0,
                "removed": 0,
                "failed": 0,
                "remaining": 0,
                "error": "error: eve-tools unavailable",
            }
        ),
    )

    await cli.run_sync(member=None, force=False)

    assert "eve-tools unavailable" in capsys.readouterr().out


async def test_list_prints_the_rendered_wardrobe(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_family", _family)
    monkeypatch.setattr(
        cli.catalog, "render_wardrobe", AsyncMock(return_value="## top\n- white shirt")
    )

    await cli.run_list(member=None)

    assert "white shirt" in capsys.readouterr().out
