"""Tests for the leaked-task check."""

from __future__ import annotations

import asyncio

import pytest

from tests.support.leaked_tasks import LeakedTaskCheck, leak_check_enabled

pytestmark = pytest.mark.xfail(
    strict=True, raises=NotImplementedError, reason="DCE-108 leaked-task check"
)


async def _wait_forever(event: asyncio.Event) -> None:
    await event.wait()


async def _finish_after_yields(n: int) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


@pytest.mark.anyio
async def test_finish_passes_when_every_task_completed() -> None:
    check = LeakedTaskCheck()
    check.start()

    await asyncio.create_task(_finish_after_yields(1))

    await check.finish()


@pytest.mark.anyio
async def test_finish_fails_on_a_pending_task_and_cancels_it() -> None:
    check = LeakedTaskCheck()
    check.start()
    task = asyncio.create_task(_wait_forever(asyncio.Event()), name="stuck-relay")

    with pytest.raises(AssertionError, match="stuck-relay"):
        await check.finish()

    assert task.cancelled()


@pytest.mark.anyio
async def test_pending_lists_tracked_tasks_still_running() -> None:
    check = LeakedTaskCheck()
    check.start()
    event = asyncio.Event()
    task = asyncio.create_task(_wait_forever(event))

    assert await check.pending() == [task]

    event.set()
    await task
    await check.finish()


@pytest.mark.anyio
async def test_tasks_created_before_start_are_not_tracked() -> None:
    event = asyncio.Event()
    earlier = asyncio.create_task(_wait_forever(event))
    check = LeakedTaskCheck()
    check.start()

    await check.finish()

    event.set()
    await earlier


@pytest.mark.anyio
async def test_task_that_finishes_while_the_loop_settles_is_not_a_leak() -> None:
    check = LeakedTaskCheck(settle_rounds=10)
    check.start()

    asyncio.create_task(_finish_after_yields(3))

    await check.finish()


@pytest.mark.anyio
async def test_finish_restores_the_previous_task_factory() -> None:
    loop = asyncio.get_running_loop()
    before = loop.get_task_factory()
    check = LeakedTaskCheck()
    check.start()

    await check.finish()

    assert loop.get_task_factory() is before


def test_check_is_enabled_by_default(request: pytest.FixtureRequest) -> None:
    assert leak_check_enabled(request.node) is True


@pytest.mark.allow_leaked_tasks(reason="synthetic opt-out")
def test_marker_disables_the_check(request: pytest.FixtureRequest) -> None:
    assert leak_check_enabled(request.node) is False
