"""Placeholder pending Task 12 - real body driven by claude-agent-sdk lands
in this same file shortly. Exists only so eve_computer.app can import
run_task while Task 11's tests (which monkeypatch this name) are verified.
"""

from __future__ import annotations


async def run_task(task_id: str, goal: str) -> dict:
    raise NotImplementedError("eve_computer.harness.run_task is not yet implemented")
