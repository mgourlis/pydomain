"""Synthetic copies of the two providers add-distributed-locking.md teaches (in-memory token,
Redis-shaped token + compare-and-delete over a fake server), plus FakeLockProvider, run through
the B-51 lock-steal scenario (must keep peak 1) and importable by arch2_test_contract_docs.py."""
import asyncio
import sys
import uuid
from contextvars import ContextVar

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch_common import Msg, run  # noqa: E402

from pydomain.testing import FakeLockProvider  # noqa: E402

_tokens: ContextVar[dict[str, object]] = ContextVar("lock_tokens", default={})


class InMemoryLockProvider:  # verbatim shape from the how-to
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._owners: dict[str, object] = {}

    async def acquire(self, key: str) -> None:
        lock = self._locks.setdefault(key, asyncio.Lock())
        await lock.acquire()
        token = object()
        self._owners[key] = token
        _tokens.set({**_tokens.get(), key: token})

    async def release(self, key: str) -> None:
        token = _tokens.get().get(key)
        if token is None or self._owners.get(key) is not token:
            return
        del self._owners[key]
        self._locks[key].release()


class FakeRedisServer:
    def __init__(self, reply_delay=0.02):
        self.store, self.delay = {}, reply_delay

    async def set_nx(self, k, v):
        await asyncio.sleep(0)
        if k in self.store:
            return None
        self.store[k] = v
        return True

    async def cad(self, k, v):  # the Lua script: atomic on the server
        if self.store.get(k) == v:
            del self.store[k]
        await asyncio.sleep(self.delay)  # reply in flight


_rtokens: ContextVar[dict[str, str]] = ContextVar("redis_lock_tokens", default={})


class RedisShapedProvider:  # the how-to's RedisLockProvider over a fake server
    def __init__(self, server):
        self.s = server

    async def acquire(self, key):
        token = uuid.uuid4().hex
        while not await self.s.set_nx(f"lock:{key}", token):
            await asyncio.sleep(0.001)
        _rtokens.set({**_rtokens.get(), key: token})

    async def release(self, key):
        token = _rtokens.get().get(key)
        if token is not None:
            await self.s.cad(f"lock:{key}", token)


class SlowReleaseFake(FakeLockProvider):
    async def release(self, key):
        await super().release(key)       # takes effect ...
        await asyncio.sleep(0.02)        # ... then the caller is interrupted


async def steal_scenario(prov):
    inside = peak = 0

    async def critical():
        nonlocal inside, peak
        inside += 1
        peak = max(peak, inside)
        await asyncio.sleep(0.05)
        inside -= 1

    started = asyncio.Event()

    async def first():
        started.set()
        await asyncio.sleep(10)

    t1 = asyncio.create_task(run(prov, Msg("order-1"), first))
    await started.wait()
    t1.cancel()
    await asyncio.sleep(0.005)
    t2 = asyncio.create_task(run(prov, Msg("order-1"), critical))
    await asyncio.sleep(0.003)
    t1.cancel()
    await asyncio.gather(t1, return_exceptions=True)
    await asyncio.sleep(0.004)
    t3 = asyncio.create_task(run(prov, Msg("order-1"), critical))
    await asyncio.gather(t2, t3)
    return peak


class SlowInMemory(InMemoryLockProvider):
    async def release(self, key):
        await super().release(key)
        await asyncio.sleep(0.02)


async def main():
    for name, prov in [
        ("how-to Redis CAD", RedisShapedProvider(FakeRedisServer())),
        ("how-to in-memory token", SlowInMemory()),
        ("FakeLockProvider", SlowReleaseFake()),
    ]:
        peak = await steal_scenario(prov)
        print(f"{name}: peak holders {peak}")
        assert peak == 1, name
    print("OK")


if __name__ == "__main__":
    asyncio.run(main())
