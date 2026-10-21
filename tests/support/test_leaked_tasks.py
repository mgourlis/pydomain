"""Tests for the leaked-task check."""

from __future__ import annotations

import asyncio

import pytest

from tests.support.leaked_tasks import LeakedTaskCheck, leak_check_enabled


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


# ── Wiring: the autouse fixture in tests/conftest.py ──────────────────────

_INNER_CONFTEST = """
import pytest

from tests.support.leaked_tasks import check_leaked_tasks, leaked_task_check


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_leaked_tasks(reason): opt out")


@pytest.fixture
def anyio_backend():
    return "asyncio"
"""


def _run_inner(pytester: pytest.Pytester, source: str) -> pytest.RunResult:
    pytester.makeconftest(_INNER_CONFTEST)
    pytester.makepyfile(source)
    return pytester.runpytest("-p", "no:cacheprovider", "--no-cov")


def test_a_test_that_leaks_a_task_errors(pytester: pytest.Pytester) -> None:
    result = _run_inner(
        pytester,
        """
import asyncio
import pytest

@pytest.mark.anyio
async def test_leaks():
    asyncio.create_task(asyncio.Event().wait(), name="stuck-relay")
""",
    )

    result.assert_outcomes(passed=1, errors=1)  # the test body passed; teardown fails
    result.stdout.fnmatch_lines(["*left pending*stuck-relay*"])


def test_tests_that_leave_no_task_pass(pytester: pytest.Pytester) -> None:
    result = _run_inner(
        pytester,
        """
import asyncio
import pytest

@pytest.mark.anyio
async def test_awaits_its_task():
    await asyncio.create_task(asyncio.sleep(0))

def test_sync():
    pass
""",
    )

    result.assert_outcomes(passed=2)


def test_a_task_stopped_in_fixture_teardown_is_not_a_leak(
    pytester: pytest.Pytester,
) -> None:
    result = _run_inner(
        pytester,
        """
import asyncio
import contextlib
import pytest

@pytest.fixture
async def relay():
    task = asyncio.create_task(asyncio.Event().wait())
    yield task
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

@pytest.mark.anyio
async def test_uses_relay(relay):
    assert not relay.done()
""",
    )

    result.assert_outcomes(passed=1)


def test_the_marker_opts_a_test_out(pytester: pytest.Pytester) -> None:
    result = _run_inner(
        pytester,
        """
import asyncio
import pytest

@pytest.mark.anyio
@pytest.mark.allow_leaked_tasks(reason="synthetic opt-out")
async def test_leaks():
    asyncio.create_task(asyncio.Event().wait())
""",
    )

    result.assert_outcomes(passed=1)
