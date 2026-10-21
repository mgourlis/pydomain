"""``FakeLockProvider`` refuses a same-task re-acquire (DCE-123, ADR-076, B-52)."""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import AsyncIterator

import anyio
import pytest

from pydomain.cqrs import CQRSError, LockReentryError
from pydomain.testing import FakeLockProvider
from tests.support.content_sentinel import CONTENT_SENTINEL

pytestmark = pytest.mark.anyio

_TIMEOUT_SECONDS = 1
_MESSAGE = (
    "FakeLockProvider: the current task already holds this lock key; "
    "locks are not reentrant (ADR-076)"
)
_NO_HOLD_MESSAGE = (
    "FakeLockProvider: the current context holds no token for this lock key; "
    "release from the task that acquired it, or from a task it started while "
    "holding it (ADR-076)"
)


class TestSameTaskReacquire:
    async def test_same_task_reacquire_raises_lock_reentry_error(self) -> None:
        locks = FakeLockProvider()
        key = f"order:{CONTENT_SENTINEL}"
        await locks.acquire(key)

        with anyio.fail_after(_TIMEOUT_SECONDS):
            with pytest.raises(LockReentryError) as raised:
                await locks.acquire(key)

        assert str(raised.value) == _MESSAGE
        assert CONTENT_SENTINEL not in str(raised.value)
        assert isinstance(raised.value, CQRSError)

    async def test_refused_reacquire_keeps_the_key_held_until_released(self) -> None:
        locks = FakeLockProvider()
        await locks.acquire("order-1")
        with anyio.fail_after(_TIMEOUT_SECONDS):
            with pytest.raises(LockReentryError):
                await locks.acquire("order-1")

        acquired = anyio.Event()

        async def contender() -> None:
            await locks.acquire("order-1")
            acquired.set()
            await locks.release("order-1")

        async with anyio.create_task_group() as tg:
            tg.start_soon(contender)
            await anyio.wait_all_tasks_blocked()
            assert not acquired.is_set()
            await locks.release("order-1")

        assert acquired.is_set()

    async def test_released_key_can_be_acquired_again_by_the_same_task(self) -> None:
        locks = FakeLockProvider()
        with anyio.fail_after(_TIMEOUT_SECONDS):
            await locks.acquire("order-1")
            await locks.release("order-1")
            await locks.acquire("order-1")
            await locks.release("order-1")


class TestSeveralTasks:
    async def test_other_task_waits_and_a_task_started_by_the_holder_releases(
        self,
    ) -> None:
        """Another task's acquire waits rather than raising; a task the holder
        started while holding may release for it; after the next holder took
        the key, the first task's own acquire waits, not raises."""
        locks = FakeLockProvider()
        await locks.acquire("order-1")
        got = anyio.Event()
        leave = anyio.Event()

        async def other() -> None:
            await locks.acquire("order-1")  # a different task: waits
            got.set()
            await leave.wait()
            await locks.release("order-1")

        async def releaser() -> None:
            await locks.release("order-1")

        async with anyio.create_task_group() as tg:
            tg.start_soon(other)
            await anyio.wait_all_tasks_blocked()
            assert not got.is_set()

            tg.start_soon(releaser)
            await got.wait()

            with anyio.move_on_after(0.05) as scope:
                await locks.acquire("order-1")  # `other` holds it now
            assert scope.cancelled_caught
            leave.set()

        with anyio.fail_after(_TIMEOUT_SECONDS):
            await locks.acquire("order-1")
            with pytest.raises(LockReentryError):
                await locks.acquire("order-1")
            await locks.release("order-1")

    async def test_reentry_error_and_logs_carry_no_key(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("DEBUG")
        locks = FakeLockProvider()
        await locks.acquire(CONTENT_SENTINEL)
        with pytest.raises(LockReentryError) as raised:
            await locks.acquire(CONTENT_SENTINEL)

        assert CONTENT_SENTINEL not in str(raised.value) + repr(raised.value)
        await locks.release(CONTENT_SENTINEL)
        assert CONTENT_SENTINEL not in caplog.text


class TestHoldTokens:
    """B-68: a release frees only the hold its context took."""

    async def test_two_providers_keep_separate_tokens_for_one_key(self) -> None:
        first, second = FakeLockProvider(), FakeLockProvider()
        with anyio.fail_after(_TIMEOUT_SECONDS):
            await first.acquire("order-1")
            await second.acquire("order-1")
            await first.release("order-1")
            await second.release("order-1")

            for locks in (first, second):
                await locks.acquire("order-1")
                await locks.release("order-1")

    async def test_release_from_a_task_started_before_the_acquire_raises(
        self,
    ) -> None:
        """A context that never saw the acquire holds no token for the key, so
        its release raises; the holder keeps the key (B-68)."""
        locks = FakeLockProvider()
        key = f"order:{CONTENT_SENTINEL}"
        go = anyio.Event()
        refused: list[BaseException] = []

        async def stranger() -> None:
            await go.wait()
            try:
                await locks.release(key)
            except RuntimeError as error:
                refused.append(error)

        async with anyio.create_task_group() as tg:
            tg.start_soon(stranger)  # started before the acquire
            await anyio.wait_all_tasks_blocked()
            await locks.acquire(key)
            go.set()
            await anyio.wait_all_tasks_blocked()
            with pytest.raises(LockReentryError):  # still held by this task
                await locks.acquire(key)
            await locks.release(key)

        if not refused:
            pytest.fail("the release without a token did not raise")
        assert str(refused[0]) == _NO_HOLD_MESSAGE
        assert CONTENT_SENTINEL not in str(refused[0])

    async def test_release_from_a_context_that_never_acquired_raises(self) -> None:
        locks = FakeLockProvider()
        acquired = anyio.Event()
        leave = anyio.Event()

        async def holder() -> None:
            await locks.acquire("order-1")
            acquired.set()
            await leave.wait()
            await locks.release("order-1")

        refused: list[BaseException] = []
        async with anyio.create_task_group() as tg:
            tg.start_soon(holder)
            await acquired.wait()
            try:
                await locks.release("order-1")  # this context never acquired
            except RuntimeError as error:
                refused.append(error)
            leave.set()

        if not refused:
            pytest.fail("the release without a token did not raise")

    @pytest.mark.xfail(
        strict=True,
        raises=pytest.fail.Exception,
        reason="DCE-123 B-81: a second release in the same context is a no-op",
    )
    async def test_second_release_of_a_hold_in_the_same_context_raises(
        self,
    ) -> None:
        """A matching release drops its token from the context (B-81), so a
        second release of the same hold finds none and raises (B-68)."""
        locks = FakeLockProvider()
        await locks.acquire("order-1")
        await locks.release("order-1")

        with pytest.raises(RuntimeError) as raised:
            await locks.release("order-1")
        assert str(raised.value) == _NO_HOLD_MESSAGE

        with anyio.fail_after(_TIMEOUT_SECONDS):
            await locks.acquire("order-1")
            await locks.release("order-1")

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-123 B-81: tokens accumulate in the context, one per key",
    )
    async def test_tokens_in_the_context_stay_bounded_over_many_keys(self) -> None:
        """One long-lived task acquires and releases many keys; no mapping in
        its context grows with the number of keys (B-81)."""
        locks = FakeLockProvider()
        for index in range(500):
            await locks.acquire(f"order:{index}")
            await locks.release(f"order:{index}")

        sizes = [
            len(value)
            for value in contextvars.copy_context().values()
            if isinstance(value, dict)
        ]
        assert max(sizes, default=0) <= 1


@pytest.fixture
async def held_by_fixture() -> AsyncIterator[FakeLockProvider]:
    locks = FakeLockProvider()
    await locks.acquire("stock:fx")  # acquired in the fixture
    yield locks


@pytest.fixture
async def released_in_teardown() -> AsyncIterator[FakeLockProvider]:
    locks = FakeLockProvider()
    yield locks
    await locks.release("stock:td")  # teardown releases what the test took


class TestContextsAndTasks:
    """Which contexts may release a hold (B-68), from the PR #112 probes."""

    async def test_fixture_acquires_and_test_body_releases(
        self, held_by_fixture: FakeLockProvider
    ) -> None:
        await held_by_fixture.release("stock:fx")
        with anyio.fail_after(_TIMEOUT_SECONDS):
            await held_by_fixture.acquire("stock:fx")
            await held_by_fixture.release("stock:fx")

    async def test_test_body_acquires_and_fixture_teardown_releases(
        self, released_in_teardown: FakeLockProvider
    ) -> None:
        await released_in_teardown.acquire("stock:td")

    async def test_acquire_in_gather_children_then_release_in_parent_raises(
        self,
    ) -> None:
        """``gather`` (like ``start_soon``) runs each acquire in a child task
        with its own context copy, so the parent holds no token: its release
        raises. Acquire and release in the same task instead."""
        locks = FakeLockProvider()
        await asyncio.gather(locks.acquire("a"), locks.acquire("b"))

        with pytest.raises(RuntimeError):
            await locks.release("a")
        with pytest.raises(RuntimeError):
            await locks.release("b")

    async def test_unrelated_task_cannot_release_and_a_started_one_can(
        self,
    ) -> None:
        locks = FakeLockProvider()
        await locks.acquire("k")

        async def other() -> None:
            await locks.release("k")

        loop = asyncio.get_running_loop()
        unrelated = loop.create_task(other(), context=contextvars.Context())
        with pytest.raises(RuntimeError):
            await unrelated
        await asyncio.create_task(locks.release("k"))  # started after the acquire

        with anyio.fail_after(_TIMEOUT_SECONDS):
            await locks.acquire("k")
            await locks.release("k")

    async def test_task_started_before_the_acquire_cannot_release(self) -> None:
        locks = FakeLockProvider()
        go = asyncio.Event()

        async def early() -> None:
            await go.wait()
            await locks.release("k")

        task = asyncio.create_task(early())
        await asyncio.sleep(0)
        await locks.acquire("k")
        go.set()
        with pytest.raises(RuntimeError):
            await task
        await locks.release("k")

    async def test_child_started_while_holding_frees_the_parents_hold(
        self,
    ) -> None:
        """A child started while the parent holds may release for it; the
        parent's own later release then has a stale token and does nothing."""
        locks = FakeLockProvider()
        await locks.acquire("k")
        await asyncio.create_task(locks.release("k"))
        got = False

        async def contender() -> None:
            nonlocal got
            await locks.acquire("k")
            got = True
            await locks.release("k")

        await asyncio.create_task(contender())
        assert got
        await locks.release("k")  # stale: a no-op

    async def test_two_providers_with_one_key_are_independent(self) -> None:
        first, second = FakeLockProvider(), FakeLockProvider()
        await first.acquire("k")
        await second.acquire("k")
        await first.release("k")
        with anyio.fail_after(_TIMEOUT_SECONDS):
            await first.acquire("k")  # first is free again; second still held
        await first.release("k")
        await second.release("k")
