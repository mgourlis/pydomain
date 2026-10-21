"""Contract suite for the ``LockProvider`` Protocol (ADR-019)."""

from __future__ import annotations

import anyio
import pytest

from pydomain.cqrs.locking import LockProvider
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["LockProviderContract"]

_TIMEOUT_SECONDS = 5


class LockProviderContract(AsyncioBackend):
    """Cases every :class:`~pydomain.cqrs.locking.LockProvider` passes.

    Override the ``lock_provider`` fixture to return a fresh provider, holding
    no locks, for each test. Every case runs under a timeout, so a provider
    that never grants a lock fails instead of hanging.
    """

    @pytest.fixture
    def lock_provider(self) -> LockProvider:
        """Return a fresh provider holding no locks. Override in your subclass."""
        raise NotImplementedError(
            "Override the `lock_provider` fixture to return your adapter."
        )

    @pytest.mark.anyio
    async def test_satisfies_protocol(self, lock_provider: LockProvider) -> None:
        """The provider is a ``LockProvider``."""
        assert isinstance(lock_provider, LockProvider)

    @pytest.mark.anyio
    async def test_free_key_is_acquired_without_waiting(
        self, lock_provider: LockProvider
    ) -> None:
        """A key nobody holds is acquired, then released."""
        with anyio.fail_after(_TIMEOUT_SECONDS):
            await lock_provider.acquire("contract-order-1")
            await lock_provider.release("contract-order-1")

    @pytest.mark.anyio
    async def test_released_key_can_be_acquired_again(
        self, lock_provider: LockProvider
    ) -> None:
        """Releasing a key makes it available to the next ``acquire``."""
        with anyio.fail_after(_TIMEOUT_SECONDS):
            await lock_provider.acquire("contract-order-1")
            await lock_provider.release("contract-order-1")
            await lock_provider.acquire("contract-order-1")
            await lock_provider.release("contract-order-1")

    @pytest.mark.anyio
    async def test_acquire_waits_until_holder_releases(
        self, lock_provider: LockProvider
    ) -> None:
        """A second ``acquire`` of a held key blocks until the holder releases."""
        key = "contract-order-1"
        acquired = anyio.Event()

        async def contender() -> None:
            await lock_provider.acquire(key)
            acquired.set()
            await lock_provider.release(key)

        with anyio.fail_after(_TIMEOUT_SECONDS):
            await lock_provider.acquire(key)
            async with anyio.create_task_group() as tg:
                tg.start_soon(contender)
                await anyio.wait_all_tasks_blocked()
                assert not acquired.is_set()

                await lock_provider.release(key)
                await acquired.wait()

    @pytest.mark.anyio
    async def test_cancelled_acquire_does_not_take_the_key(
        self, lock_provider: LockProvider
    ) -> None:
        """An ``acquire`` cancelled while it waits never holds the key.

        Once the holder releases, the key is free: the cancelled waiter does
        not take it later (ADR-076).
        """
        key = "contract-order-1"

        async def contender() -> None:
            await lock_provider.acquire(key)

        with anyio.fail_after(_TIMEOUT_SECONDS):
            await lock_provider.acquire(key)
            async with anyio.create_task_group() as tg:
                tg.start_soon(contender)
                await anyio.wait_all_tasks_blocked()
                tg.cancel_scope.cancel()
            await lock_provider.release(key)

            await lock_provider.acquire(key)
            await lock_provider.release(key)

    @pytest.mark.anyio
    async def test_different_keys_do_not_block_each_other(
        self, lock_provider: LockProvider
    ) -> None:
        """Holding one key does not block ``acquire`` of another."""
        with anyio.fail_after(_TIMEOUT_SECONDS):
            await lock_provider.acquire("contract-order-1")
            await lock_provider.acquire("contract-order-2")
            await lock_provider.release("contract-order-2")
            await lock_provider.release("contract-order-1")
