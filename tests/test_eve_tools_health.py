"""The fan-out layer: which providers a member has, merging their answers,
clamping `days`, and the `unconfigured` key.

Both clients are stubbed. Their wire formats are tested in
test_eve_tools_whoop.py and test_eve_tools_oura.py; what matters here is the
merge and the envelope.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def stub(monkeypatch):
    """Both clients plus the store, with recorded calls."""
    calls = {"whoop": [], "oura": [], "providers": []}

    def _client(name, entries):
        async def _get(member_sub, days):
            calls[name].append((member_sub, days))
            return list(entries)

        return _get

    def _configure(providers, whoop_entries=(), oura_entries=()):
        async def _configured(member_sub):
            calls["providers"].append(member_sub)
            return list(providers)

        monkeypatch.setattr(
            "eve_tools.health.oauth_store.configured_providers", _configured
        )
        for metric in ("get_recovery", "get_sleep", "get_activity"):
            monkeypatch.setattr(
                f"eve_tools.health.whoop.{metric}",
                _client("whoop", whoop_entries),
            )
            monkeypatch.setattr(
                f"eve_tools.health.oura.{metric}",
                _client("oura", oura_entries),
            )
        return calls

    return _configure


async def test_a_member_with_one_device_gets_that_devices_entries(stub):
    calls = stub(
        ["whoop"],
        whoop_entries=[{"date": "2026-09-01", "source": "whoop", "score_0_100": 68}],
    )
    from eve_tools import health

    result = await health.get_recovery("sub-noah", days=1)
    assert result == {
        "recovery": [{"date": "2026-09-01", "source": "whoop", "score_0_100": 68}],
        # Oura has no row for this member, and the coach saying so is more
        # useful than silence. Spec 4.3.4.
        "unconfigured": ["oura"],
    }
    assert calls["oura"] == [], "must not call a provider the member has not connected"


async def test_a_member_with_both_devices_gets_both_labelled_by_source(stub):
    """Spec 4: the specialist reports both rather than silently preferring
    one. Two entries per day, each with its own `source`."""
    stub(
        ["oura", "whoop"],
        whoop_entries=[{"date": "2026-09-01", "source": "whoop", "score_0_100": 68}],
        oura_entries=[{"date": "2026-09-01", "source": "oura", "score_0_100": 81}],
    )
    from eve_tools import health

    result = await health.get_recovery("sub-noah", days=1)
    assert "unconfigured" not in result
    assert {e["source"] for e in result["recovery"]} == {"oura", "whoop"}
    assert len(result["recovery"]) == 2


async def test_a_member_with_no_device_gets_an_empty_list_and_both_providers(stub):
    stub([])
    from eve_tools import health

    assert await health.get_recovery("sub-noah", days=1) == {
        "recovery": [],
        "unconfigured": ["oura", "whoop"],
    }


async def test_entries_are_sorted_newest_first_across_providers(stub):
    stub(
        ["oura", "whoop"],
        whoop_entries=[
            {"date": "2026-08-31", "source": "whoop"},
            {"date": "2026-09-01", "source": "whoop"},
        ],
        oura_entries=[{"date": "2026-09-01", "source": "oura"}],
    )
    from eve_tools import health

    result = await health.get_recovery("sub-noah", days=2)
    assert [e["date"] for e in result["recovery"]] == [
        "2026-09-01", "2026-09-01", "2026-08-31",
    ]


@pytest.mark.parametrize(
    "requested,expected",
    [(0, 1), (-5, 1), (1, 1), (14, 14), (15, 14), (900, 14), (None, 1), ("3", 3)],
)
async def test_days_is_clamped_rather_than_trusted(stub, requested, expected):
    """Spec 4: 1..14, enforced here, not by the caller. A model that asks for
    900 days must not turn into 900 days of provider traffic."""
    calls = stub(["whoop"])
    from eve_tools import health

    await health.get_recovery("sub-noah", days=requested)
    assert calls["whoop"] == [("sub-noah", expected)]


async def test_sleep_and_activity_use_their_own_envelope_keys(stub):
    stub(["whoop"], whoop_entries=[{"date": "2026-09-01", "source": "whoop"}])
    from eve_tools import health

    assert "sleep" in await health.get_sleep("sub-noah", days=1)
    assert "activity" in await health.get_activity("sub-noah", days=1)


async def test_a_broken_credential_surfaces_rather_than_reading_as_no_data(
    stub, monkeypatch
):
    """The one failure that must NOT degrade to an empty list. Broken auth
    reported as "no sleep data" would have the coach describing a quiet night
    that never happened."""
    stub(["whoop", "oura"], oura_entries=[{"date": "2026-09-01", "source": "oura"}])
    from eve_tools import health, oauth_store

    async def _boom(member_sub, days):
        raise oauth_store.ReconnectRequired("whoop refused to refresh")

    monkeypatch.setattr("eve_tools.health.whoop.get_recovery", _boom)

    result = await health.get_recovery("sub-noah", days=1)
    # The healthy provider still answers - one broken device must not take
    # down the other.
    assert [e["source"] for e in result["recovery"]] == ["oura"]
    assert "whoop" in result["errors"][0]


async def test_one_providers_transport_failure_does_not_lose_the_others_data(
    stub, monkeypatch
):
    stub(["whoop", "oura"], oura_entries=[{"date": "2026-09-01", "source": "oura"}])
    from eve_tools import health

    async def _boom(member_sub, days):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("eve_tools.health.whoop.get_recovery", _boom)

    result = await health.get_recovery("sub-noah", days=1)
    assert [e["source"] for e in result["recovery"]] == ["oura"]
    assert "whoop" in result["errors"][0]
