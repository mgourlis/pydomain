"""Run the ``LockProvider`` contract suite against ``FakeLockProvider``."""

from __future__ import annotations

import asyncio

import pytest

from pydomain.testing import FakeLockProvider
from pydomain.testing.contracts import LockProviderContract


class TestFakeLockProviderContract(LockProviderContract):
    @pytest.fixture
    def lock_provider(self) -> FakeLockProvider:
        return FakeLockProvider()


class _IdempotentOnlyLocks:
    """Releases whatever lock the key has: idempotent, not ownership-safe."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    async def acquire(self, key: str) -> None:
        await self._locks.setdefault(key, asyncio.Lock()).acquire()

    async def release(self, key: str) -> None:
        if key in self._locks and self._locks[key].locked():
            self._locks[key].release()


@pytest.mark.anyio
async def test_late_release_case_fails_an_idempotent_only_provider() -> None:
    """The contract case catches a release that frees the next holder's lock."""
    case = LockProviderContract()
    with pytest.raises(AssertionError):
        await case.test_late_release_after_another_holder_took_the_key_does_not_free_it(
            _IdempotentOnlyLocks()
        )


class _OwnReentryErrorLocks(_IdempotentOnlyLocks):
    """Detects reentry with its own error type and keeps the first hold."""

    def __init__(self, *, drop_hold_on_reentry: bool = False) -> None:
        super().__init__()
        self._holders: dict[str, object] = {}
        self._drop = drop_hold_on_reentry

    async def acquire(self, key: str) -> None:
        if self._holders.get(key) is asyncio.current_task():
            if self._drop:  # a broken provider that loses the hold
                del self._holders[key]
                self._locks[key].release()
            raise RuntimeError("reentry")
        await super().acquire(key)
        self._holders[key] = asyncio.current_task()

    async def release(self, key: str) -> None:
        self._holders.pop(key, None)
        await super().release(key)


@pytest.mark.anyio
async def test_reentry_case_accepts_a_provider_with_its_own_error() -> None:
    """The loose form (B-52): the second acquire may raise any exception."""
    case = LockProviderContract()
    await case.test_same_task_cannot_acquire_a_held_key_again(_OwnReentryErrorLocks())


@pytest.mark.anyio
async def test_reentry_case_fails_a_provider_that_loses_the_first_hold() -> None:
    case = LockProviderContract()
    with pytest.raises(AssertionError):
        await case.test_same_task_cannot_acquire_a_held_key_again(
            _OwnReentryErrorLocks(drop_hold_on_reentry=True)
        )
