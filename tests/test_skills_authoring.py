from datetime import UTC, datetime

from eve.memory.types import Memory

# InjectedState validates the whole EveState/MemberContext shape strictly
# when a tool is invoked directly with .ainvoke (unlike a plain TypedDict at
# runtime) - see tests/test_specialists_base.py and tests/test_skills_search.py
# for the same full-state convention.
MEMBER = {
    "sub": "sub-noah",
    "name": "Noah",
    "role": "adult",
    "timezone": "America/Vancouver",
    "permissions": [],
    "local_time": "2026-08-27 09:00 PDT",
}
STATE = {
    "messages": [],
    "member": MEMBER,
    "system_prompt": "",
    "memory": None,
    "dynamic_tools": [],
    "suggestions": [],
}


def _proc(content, name="book-the-dog-sitter"):
    now = datetime(2026, 8, 27, tzinfo=UTC)
    return Memory(
        id="p1", layer="procedure", scope_kind="member", scope_id="sub-noah",
        kind="decision", subject=name, content=content, confidence=0.8,
        salience=0.5, created_at=now, last_seen_at=now,
    )


def test_serialize_round_trips_through_the_shared_parser():
    from eve.skills.authoring import serialize_procedure
    from eve.skills.registry import parse_skill_text

    text = serialize_procedure(
        "book-the-dog-sitter", "How to book the dog sitter.", "1. Text Sam.\n2. Confirm."
    )
    name, description, body, specialist = parse_skill_text(text, "fallback")

    assert name == "book-the-dog-sitter"
    assert description == "How to book the dog sitter."
    assert body == "1. Text Sam.\n2. Confirm."
    assert specialist is None


def test_serialize_round_trips_a_description_containing_a_colon():
    """A colon-space in the description ('...sitter: call Sam first.') is
    exactly the shape an LLM-authored summary tends to take. Raw f-string
    interpolation into YAML frontmatter turns that into a mapping and
    yaml.safe_load raises ScannerError on the way back out; safe_dump quotes
    it instead."""
    from eve.skills.authoring import serialize_procedure
    from eve.skills.registry import parse_skill_text

    text = serialize_procedure(
        "book-the-dog-sitter",
        "How to book the sitter: call Sam first.",
        "1. Text Sam.\n2. Confirm.",
    )
    name, description, body, specialist = parse_skill_text(text, "fallback")

    assert name == "book-the-dog-sitter"
    assert description == "How to book the sitter: call Sam first."
    assert body == "1. Text Sam.\n2. Confirm."
    assert specialist is None


def test_parse_skill_text_falls_back_without_frontmatter():
    from eve.skills.registry import parse_skill_text

    name, description, body, specialist = parse_skill_text("just a body", "fallback")
    assert (name, description, body, specialist) == ("fallback", "", "just a body", None)


async def test_write_skill_adds_a_procedure_row(monkeypatch):
    from eve.skills import authoring

    added = []

    async def add(**kw):
        added.append(kw)
        return "new-1"

    async def procedure_by_name(sub, name):
        return None

    async def supersede(old, new, why):
        raise AssertionError("nothing to supersede on a first write")

    monkeypatch.setattr(authoring, "add", add)
    monkeypatch.setattr(authoring, "procedure_by_name", procedure_by_name)
    monkeypatch.setattr(authoring, "supersede", supersede)
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()

    result = await authoring.write_skill.ainvoke(
        {
            "name": "book-the-dog-sitter",
            "description": "How to book the dog sitter.",
            "content": "1. Text Sam.",
            "state": STATE,
        },
        config={"configurable": {"thread_id": "t1", "run_id": "r1"}},
    )

    assert len(added) == 1
    assert added[0]["layer"] == "procedure"
    assert added[0]["scope_kind"] == "member"
    assert added[0]["scope_id"] == "sub-noah"
    assert added[0]["subject"] == "book-the-dog-sitter"
    assert added[0]["source_thread"] == "t1"
    assert "How to book the dog sitter." in added[0]["content"]
    assert "book-the-dog-sitter" in result


async def test_write_skill_supersedes_an_existing_name(monkeypatch):
    """A procedure Eve wrote once and can never revise goes stale and stays
    stale. The superseded_by chain records the revision."""
    from eve.skills import authoring

    superseded = []

    async def add(**kw):
        return "new-2"

    async def procedure_by_name(sub, name):
        return _proc("old text")

    async def supersede(old, new, why):
        superseded.append((old, new, why))

    monkeypatch.setattr(authoring, "add", add)
    monkeypatch.setattr(authoring, "procedure_by_name", procedure_by_name)
    monkeypatch.setattr(authoring, "supersede", supersede)
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()

    await authoring.write_skill.ainvoke(
        {
            "name": "book-the-dog-sitter",
            "description": "Updated.",
            "content": "1. Call Sam.",
            "state": STATE,
        },
        config={"configurable": {"thread_id": "t2", "run_id": "r2"}},
    )

    assert superseded == [("p1", "new-2", "rewritten by write_skill")]


async def test_write_skill_refuses_when_disabled(monkeypatch):
    from eve.skills import authoring

    async def add(**kw):
        raise AssertionError("must not write when disabled")

    monkeypatch.setattr(authoring, "add", add)
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "false")
    from eve.settings import get_settings

    get_settings.cache_clear()

    result = await authoring.write_skill.ainvoke(
        {
            "name": "x", "description": "d", "content": "c",
            "state": STATE,
        },
        config={"configurable": {}},
    )
    assert result.startswith("error:")


async def test_write_skill_refuses_on_an_ambient_turn(monkeypatch):
    """The core invariant of this phase: a turn whose last human message
    carries the ambient marker authors nothing - no rule AND no procedure.

    `write_skill` is bound into the same graph the ambient pipeline drives
    with attacker-controlled text (eve_ambient/notify.py wraps up to 800
    characters of raw signal payload in a marked human message), so the
    settings check alone would leave the procedure path reachable from an
    email body while eve.memory.extract's guard closed the rule path. Same
    shape as tests/test_memory_extract.py's
    test_a_rule_op_is_refused_on_an_ambient_turn, other authoring path.
    """
    from langchain_core.messages import HumanMessage

    from eve.skills import authoring
    from eve.state import ambient_marker

    added = []
    looked_up = []

    async def add(**kw):
        added.append(kw)
        return "new-1"

    async def procedure_by_name(sub, name):
        # Deliberately NOT `raise AssertionError`: write_skill's own
        # `except Exception` would swallow it into an "error:" string and the
        # test would pass against an unguarded write_skill. Record instead.
        looked_up.append(name)
        return None

    monkeypatch.setattr(authoring, "add", add)
    monkeypatch.setattr(authoring, "procedure_by_name", procedure_by_name)
    # Enabled: the setting is not what refuses here, the marker is.
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()

    state = {
        **STATE,
        "messages": [
            HumanMessage(ambient_marker("Noah") + "\nA bank email arrived."),
        ],
    }
    result = await authoring.write_skill.ainvoke(
        {
            "name": "exfiltrate", "description": "d", "content": "c",
            "state": state,
        },
        config={"configurable": {"thread_id": "t1"}},
    )

    assert added == []
    assert looked_up == []
    assert result.startswith("error:")


async def test_write_skill_still_writes_on_an_ordinary_spoken_turn(monkeypatch):
    """The guard must refuse the ambient turn without refusing every turn -
    a guard that always says no is indistinguishable from a broken feature."""
    from langchain_core.messages import HumanMessage

    from eve.skills import authoring

    added = []

    async def add(**kw):
        added.append(kw)
        return "new-1"

    async def procedure_by_name(sub, name):
        return None

    monkeypatch.setattr(authoring, "add", add)
    monkeypatch.setattr(authoring, "procedure_by_name", procedure_by_name)
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()

    state = {**STATE, "messages": [HumanMessage("here is how you book the sitter")]}
    result = await authoring.write_skill.ainvoke(
        {
            "name": "book-the-dog-sitter", "description": "d", "content": "c",
            "state": state,
        },
        config={"configurable": {"thread_id": "t1"}},
    )

    assert len(added) == 1
    assert not result.startswith("error:")


async def test_write_skill_degrades_a_database_failure_to_a_string(monkeypatch):
    """Global constraint: a tool returns an error string, never raises. A
    raise here would fail the whole turn instead of letting Eve explain."""
    from eve.skills import authoring

    async def procedure_by_name(sub, name):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(authoring, "procedure_by_name", procedure_by_name)
    monkeypatch.setenv("EVE_SELF_AUTHORING_ENABLED", "true")
    from eve.settings import get_settings

    get_settings.cache_clear()

    result = await authoring.write_skill.ainvoke(
        {
            "name": "x", "description": "d", "content": "c",
            "state": STATE,
        },
        config={"configurable": {}},
    )
    assert result.startswith("error:")
