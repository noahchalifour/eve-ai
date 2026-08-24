"""Task 13 iterates SOURCES and reads each entry's per_member and
permission; nothing asserted those shapes until now."""

from eve_ambient.sources import SOURCES, Source


def test_sources_is_a_tuple_of_source():
    assert isinstance(SOURCES, tuple)
    assert all(isinstance(source, Source) for source in SOURCES)


def test_every_source_has_a_unique_name():
    names = [source.name for source in SOURCES]
    assert len(names) == len(set(names))


def test_mail_and_calendar_are_per_member_finances_is_household():
    by_name = {source.name: source for source in SOURCES}
    assert by_name["mail"].per_member is True
    assert by_name["calendar"].per_member is True
    assert by_name["finances"].per_member is False


def test_each_source_declares_the_permission_it_polls_under():
    by_name = {source.name: source for source in SOURCES}
    assert by_name["mail"].permission == "mail.read"
    assert by_name["calendar"].permission == "calendar.read"
    assert by_name["finances"].permission == "finances"


def test_each_source_poll_is_an_async_callable():
    import inspect

    assert all(inspect.iscoroutinefunction(source.poll) for source in SOURCES)
