"""Contract suite for the ``LockProvider`` Protocol (ADR-019)."""

from __future__ import annotations

import pytest

from pydomain.cqrs.locking import LockProvider
from pydomain.testing.contracts._support import pending

__all__ = ["LockProviderContract"]

_TIMEOUT_SECONDS = 5


class LockProviderContract:
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

    def test_satisfies_protocol(self, lock_provider: LockProvider) -> None:
        """The provider is a ``LockProvider``."""
        pending()

    @pytest.mark.anyio
    async def test_free_key_is_acquired_without_waiting(
        self, lock_provider: LockProvider
    ) -> None:
        """A key nobody holds is acquired, then released."""
        pending()

    @pytest.mark.anyio
    async def test_released_key_can_be_acquired_again(
        self, lock_provider: LockProvider
    ) -> None:
        """Releasing a key makes it available to the next ``acquire``."""
        pending()

    @pytest.mark.anyio
    async def test_acquire_waits_until_holder_releases(
        self, lock_provider: LockProvider
    ) -> None:
        """A second ``acquire`` of a held key blocks until the holder releases."""
        pending()

    @pytest.mark.anyio
    async def test_different_keys_do_not_block_each_other(
        self, lock_provider: LockProvider
    ) -> None:
        """Holding one key does not block ``acquire`` of another."""
        pending()
