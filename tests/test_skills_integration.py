"""tests/test_skills_integration.py"""
import os

import pytest

from eve.skills.registry import load_skills
from eve.skills.search import rank_skills
from eve.settings import get_settings


def test_the_example_skill_loads_from_disk():
    get_settings.cache_clear()
    skills = load_skills()
    names = [s.name for s in skills]
    assert "greet-warmly" in names


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("EVE_LIVE_TESTS") != "1",
    reason="set EVE_LIVE_TESTS=1 to run against the real embedding model",
)
async def test_search_skills_finds_the_example_skill_for_a_relevant_query():
    get_settings.cache_clear()
    skills = load_skills()
    ranked = await rank_skills("how should I say hello", skills, top_k=1)
    assert ranked[0].name == "greet-warmly"
