"""``FakeLockProvider`` refuses a same-task re-acquire (DCE-123, ADR-076, B-52)."""

from __future__ import annotations

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


class TestSameTaskReacquire:
    @pytest.mark.xfail(
        strict=True,
        raises=TimeoutError,
        reason="DCE-123 CQRS-03: a same-task re-acquire deadlocks",
    )
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

    @pytest.mark.xfail(
        strict=True,
        raises=TimeoutError,
        reason="DCE-123 CQRS-03: a same-task re-acquire deadlocks",
    )
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
