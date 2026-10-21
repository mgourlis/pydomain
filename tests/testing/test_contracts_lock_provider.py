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
