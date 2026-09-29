# src/eve/widgets/actions/__init__.py
"""Importing this package registers every action."""
from eve.widgets.actions.base import REGISTRY, accepts  # noqa: F401
from eve.widgets.actions import filters, home  # noqa: E402,F401
