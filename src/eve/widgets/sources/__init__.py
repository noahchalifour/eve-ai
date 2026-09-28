"""Importing this package registers every source. Adding a source = a new
module here plus one import line below (and its test)."""
from eve.widgets.sources.base import REGISTRY, SourceError, SourceType, parse  # noqa: F401
from eve.widgets.sources import series, health, weather, home  # noqa: E402,F401
