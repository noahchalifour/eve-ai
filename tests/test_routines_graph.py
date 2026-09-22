from __future__ import annotations


def _names(tools):
    return {tool.name for tool in tools}


def test_the_routine_tools_are_bound_when_enabled(monkeypatch):
    monkeypatch.setenv("EVE_ROUTINES_ENABLED", "true")

    from eve.graph import _static_tools
    from eve.settings import get_settings

    get_settings.cache_clear()
    names = _names(_static_tools())

    assert {"schedule_routine", "list_routines", "cancel_routine"} <= names


def test_the_routine_tools_are_absent_when_disabled(monkeypatch):
    """A deployment that has not accepted standing spend must not even be
    able to be asked for it."""
    monkeypatch.setenv("EVE_ROUTINES_ENABLED", "false")

    from eve.graph import _static_tools
    from eve.settings import get_settings

    get_settings.cache_clear()
    names = _names(_static_tools())

    assert not {"schedule_routine", "list_routines", "cancel_routine"} & names


def test_every_routine_tool_has_a_label(monkeypatch):
    """graph.py's own test_every_labelled_tool_is_a_real_tool checks the
    other direction; this checks these three are not missed."""
    monkeypatch.setenv("EVE_ROUTINES_ENABLED", "true")

    from eve.graph import _TOOL_LABELS

    for name in ("schedule_routine", "list_routines", "cancel_routine"):
        assert name in _TOOL_LABELS
        label = _TOOL_LABELS[name]
        assert label == label[0].upper() + label[1:]
        assert not label.endswith((".", "..."))
        assert len(label) <= 60
