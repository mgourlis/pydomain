"""Run LockProviderContract against the how-to's providers (should pass), an idempotent-only
provider (late-release case must fail) and a provider raising its own reentry error (shows the
narrowed B-52 case now fails it)."""
import asyncio
import sys

import pytest

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch2_doc_providers import FakeRedisServer, InMemoryLockProvider, RedisShapedProvider  # noqa: E402

from pydomain.testing.contracts import LockProviderContract  # noqa: E402


class TestHowToInMemory(LockProviderContract):
    @pytest.fixture
    def lock_provider(self):
        return InMemoryLockProvider()


class TestHowToRedisShaped(LockProviderContract):
    @pytest.fixture
    def lock_provider(self):
        return RedisShapedProvider(FakeRedisServer(reply_delay=0))


class IdempotentOnly:
    def __init__(self):
        self.locks = {}

    async def acquire(self, k):
        await self.locks.setdefault(k, asyncio.Lock()).acquire()

    async def release(self, k):
        if self.locks[k].locked():
            self.locks[k].release()


class TestIdempotentOnly(LockProviderContract):
    @pytest.fixture
    def lock_provider(self):
        return IdempotentOnly()


class OwnReentryError(InMemoryLockProvider):
    holder = {}

    async def acquire(self, key):
        if key in self._owners and self.holder.get(key) is asyncio.current_task():
            raise RuntimeError("reentry")  # its own error, not LockReentryError
        await super().acquire(key)
        self.holder[key] = asyncio.current_task()


class TestOwnReentryError(LockProviderContract):
    @pytest.fixture
    def lock_provider(self):
        return OwnReentryError()
