"""Shared test fixtures.

Both `get_settings` and `get_model` are `lru_cache`d process-wide singletons.
Tests that mutate env vars to exercise settings-dependent behavior (e.g.
`test_model_is_pointed_at_litellm`) clear those caches before use but leave
the mutated singleton cached afterward, which would otherwise leak into every
later test in the session. Clearing both caches around every test keeps them
isolated regardless of run order.
"""

from __future__ import annotations

import pytest

from eve.models import get_model
from eve.settings import get_settings


@pytest.fixture(autouse=True)
def _clear_caches():
    get_settings.cache_clear()
    get_model.cache_clear()
    yield
    get_settings.cache_clear()
    get_model.cache_clear()
