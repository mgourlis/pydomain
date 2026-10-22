"""Leaked-task check: fail a test that leaves pending asyncio tasks behind.

``tests/conftest.py`` runs :class:`LeakedTaskCheck` around every anyio test:
it tracks the tasks created from the start of the test's fixtures to the end
of their teardown, and fails the test if any is still pending. Opt a test out
with ``@pytest.mark.allow_leaked_tasks(reason="DCE-NN …")``.

It covers anyio-marked tests only: a sync test that drives its own loop, such
as the docs-example tests with ``asyncio.Runner``, is not checked.

Without the check, anyio closes the event loop after the test and cancels
the leftover tasks silently.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from typing import Any

import pytest

ALLOW_LEAKED_TASKS = "allow_leaked_tasks"
"""Marker name that switches the check off for one test."""

_CANCEL_TIMEOUT = 1.0
"""Seconds to wait for leaked tasks to finish once cancelled."""


class LeakedTaskCheck:
    """Track the tasks created on the running loop between start and finish.

    Tracking uses the loop's task factory and counts only tasks created while
    the loop runs, so the tasks a test runner makes to drive the loop (anyio
    wraps each call in ``run_until_complete``) are not reported.
    """

    def __init__(self, *, settle_rounds: int = 10) -> None:
        self._settle_rounds = settle_rounds
        self._tracked: list[asyncio.Task[Any]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._previous_factory: Any = None

    def start(self) -> None:
        """Start tracking tasks created on the running loop from now on."""
        loop = asyncio.get_running_loop()
        previous = loop.get_task_factory()
        tracked = self._tracked

        def factory(
            loop: asyncio.AbstractEventLoop,
            coro: Coroutine[Any, Any, Any],
            **kwargs: Any,
        ) -> asyncio.Future[Any]:
            if previous is None:
                task: asyncio.Future[Any] = asyncio.Task(coro, loop=loop, **kwargs)
            else:
                task = previous(loop, coro, **kwargs)
            if loop.is_running() and isinstance(task, asyncio.Task):
                tracked.append(task)
            return task

        self._loop = loop
        self._previous_factory = previous
        loop.set_task_factory(factory)

    async def pending(self) -> list[asyncio.Task[Any]]:
        """Let the loop settle, then return the tracked tasks still pending."""
        current = asyncio.current_task()
        for _ in range(self._settle_rounds):
            if not self._still_pending(current):
                break
            await asyncio.sleep(0)
        return self._still_pending(current)

    async def finish(self) -> None:
        """Stop tracking; cancel and report the tracked tasks still pending.

        Raises
        ------
        AssertionError
            If a tracked task was still pending; it is cancelled first.
        """
        leaked = await self.pending()
        if self._loop is not None:
            self._loop.set_task_factory(self._previous_factory)
            self._loop = None
        self._tracked.clear()
        if not leaked:
            return
        for task in leaked:
            task.cancel()
        await asyncio.wait(leaked, timeout=_CANCEL_TIMEOUT)
        names = ", ".join(_describe(task) for task in leaked)
        raise AssertionError(
            f"{len(leaked)} asyncio task(s) left pending (now cancelled): {names}. "
            f"Await or cancel them, or mark the test "
            f"@pytest.mark.{ALLOW_LEAKED_TASKS}(reason=...)."
        )

    def _still_pending(
        self, current: asyncio.Task[Any] | None
    ) -> list[asyncio.Task[Any]]:
        return [t for t in self._tracked if t is not current and not t.done()]


def leak_check_enabled(node: pytest.Item) -> bool:
    """Whether the leaked-task check applies to *node* (no opt-out marker)."""
    return node.get_closest_marker(ALLOW_LEAKED_TASKS) is None


def _describe(task: asyncio.Task[Any]) -> str:
    coro = task.get_coro()
    return f"{task.get_name()} ({getattr(coro, '__qualname__', type(coro).__name__)})"


@pytest.fixture
async def leaked_task_check() -> AsyncIterator[None]:
    """Run :class:`LeakedTaskCheck` inside the test's event loop."""
    check = LeakedTaskCheck()
    check.start()
    yield
    await check.finish()


@pytest.fixture(autouse=True)
def check_leaked_tasks(request: pytest.FixtureRequest) -> None:
    """Fail an anyio (asyncio) test that leaves pending tasks behind.

    Covers anyio-marked tests only: a sync test that drives its own loop,
    such as the docs-example tests with ``asyncio.Runner``, is outside it.

    Requested first, so its teardown runs after every other fixture's: a task
    that a fixture stops in its teardown is not a leak.
    """
    if "anyio_backend" not in request.fixturenames:
        return
    if not leak_check_enabled(request.node):
        return
    backend = request.getfixturevalue("anyio_backend")
    name = backend if isinstance(backend, str) else backend[0]
    if name == "asyncio":
        request.getfixturevalue("leaked_task_check")
