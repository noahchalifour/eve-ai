"""tests/test_images_cli.py"""
import sys


def test_sweep_deletes_and_reports(monkeypatch, capsys):
    from eve.images import cli

    closed = []

    async def fake_sweep(now=None):
        return 3

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(cli.store, "sweep", fake_sweep)
    monkeypatch.setattr(cli, "close_pool", fake_close)
    monkeypatch.setattr(sys, "argv", ["eve-images", "sweep"])

    cli.main()

    assert "deleted 3 expired image(s)" in capsys.readouterr().out
    assert closed == [True]
