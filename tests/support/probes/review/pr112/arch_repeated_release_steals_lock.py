"""Hazard of the B-51 re-call (ADR-076 decision 3) with a key-owned provider like the
add-distributed-locking Redis example (`DEL lock:<key>` with no token): the interrupted
release had already deleted the key on the server; a contender acquires it; the background
task's repeated release deletes the CONTENDER's lock, so a third command enters while the
contender still holds it. Two holders at once = lost mutual exclusion."""
import asyncio
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch_common import Msg, run  # noqa: E402


class FakeRedis:
    """SET NX / DEL on a dict, each with a network round trip."""

    def __init__(self):
        self.store = {}

    async def set_nx(self, k):
        await asyncio.sleep(0)
        if k in self.store:
            return False
        self.store[k] = "1"
        return True

    async def delete(self, k):
        self.store.pop(k, None)      # server applied the DEL ...
        await asyncio.sleep(0.02)    # ... the reply is still in flight


class RedisLockProvider:  # the how-to's shape: the lock belongs to the key
    def __init__(self, client):
        self.c = client

    async def acquire(self, key):
        while not await self.c.set_nx(f"lock:{key}"):
            await asyncio.sleep(0.001)

    async def release(self, key):
        await self.c.delete(f"lock:{key}")


async def main():
    prov = RedisLockProvider(FakeRedis())
    inside = 0
    peak = 0

    async def critical():
        nonlocal inside, peak
        inside += 1
        peak = max(peak, inside)
        await asyncio.sleep(0.05)
        inside -= 1
        return "ok"

    started = asyncio.Event()

    async def first_handler():
        started.set()
        await asyncio.sleep(10)

    t1 = asyncio.create_task(run(prov, Msg("order-1"), first_handler))
    await started.wait()
    t1.cancel()                      # first cancel: handler ends, release starts
    await asyncio.sleep(0.005)       # DEL applied, reply in flight
    t2 = asyncio.create_task(run(prov, Msg("order-1"), critical))  # contender takes the key
    await asyncio.sleep(0.003)
    t1.cancel()                      # second cancel -> hand over -> background re-DEL
    await asyncio.gather(t1, return_exceptions=True)
    await asyncio.sleep(0.004)
    t3 = asyncio.create_task(run(prov, Msg("order-1"), critical))
    await asyncio.gather(t2, t3)
    print("peak concurrent holders of order-1:", peak)
    assert peak == 1, "mutual exclusion lost: the repeated release freed another command's lock"


asyncio.run(main())
