"""Leaked-task check: fail a test that leaves pending asyncio tasks behind.

``tests/conftest.py`` runs :class:`LeakedTaskCheck` around every anyio test:
it tracks the tasks created from the start of the test's fixtures to the end
of their teardown, and fails the test if any is still pending. Opt a test out
with ``@pytest.mark.allow_leaked_tasks(reason="DCE-NN …")``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

ALLOW_LEAKED_TASKS = "allow_leaked_tasks"
"""Marker name that switches the check off for one test."""


class LeakedTaskCheck:
    """Track the tasks created on the running loop between start and finish."""

    def __init__(self, *, settle_rounds: int = 10) -> None:
        raise NotImplementedError

    def start(self) -> None:
        """Start tracking tasks created on the running loop from now on."""
        raise NotImplementedError

    async def pending(self) -> list[asyncio.Task[Any]]:
        """Let the loop settle, then return the tracked tasks still pending."""
        raise NotImplementedError

    async def finish(self) -> None:
        """Stop tracking; cancel and report the tracked tasks still pending.

        Raises
        ------
        AssertionError
            If a tracked task was still pending; it is cancelled first.
        """
        raise NotImplementedError


def leak_check_enabled(node: pytest.Item) -> bool:
    """Whether the leaked-task check applies to *node* (no opt-out marker)."""
    raise NotImplementedError
