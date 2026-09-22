from __future__ import annotations


def test_routines_are_off_by_default(monkeypatch):
    """Standing spend must be opted into, the same posture ambient_enabled,
    sandbox_enabled, computer_enabled and coding_enabled all take."""
    monkeypatch.delenv("EVE_ROUTINES_ENABLED", raising=False)

    from eve.settings import Settings

    assert Settings().routines_enabled is False


def test_the_failure_limit_defaults_to_five(monkeypatch):
    monkeypatch.delenv("EVE_ROUTINE_FAILURE_LIMIT", raising=False)

    from eve.settings import Settings

    assert Settings().routine_failure_limit == 5
