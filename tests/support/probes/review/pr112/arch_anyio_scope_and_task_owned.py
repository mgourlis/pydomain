"""ADR-076 decision 3 under anyio: (1) a cancelled move_on_after scope hands the awaiting
releases over and the key frees itself; (2) an anyio.Lock-backed (task-owned) provider works
on every inline path (success, failing handler, single cancel), and (3) refuses only the
handed-over releases, logged by type, with the key left held."""
import asyncio
import sys

import anyio

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch_common import Msg, capture, run  # noqa: E402


class AwaitingAsyncioLocks:
    def __init__(self):
        self.locks = {}

    async def acquire(self, key):
        await self.locks.setdefault(key, asyncio.Lock()).acquire()

    async def release(self, key):
        await asyncio.sleep(0.01)  # a round trip to a lock server
        if self.locks[key].locked():  # tolerate the repeated release
            self.locks[key].release()


class AnyioOwned:
    """Task-owned: anyio.Lock refuses a release from another task."""

    def __init__(self, awaiting):
        self.locks = {}
        self.awaiting = awaiting

    async def acquire(self, key):
        await self.locks.setdefault(key, anyio.Lock()).acquire()

    async def release(self, key):
        if self.awaiting:
            await anyio.sleep(0)
        self.locks[key].release()


async def ok():
    return "done"


async def boom():
    raise ValueError("x")


async def slow():
    await anyio.sleep(10)


async def main():
    cap = capture()
    # (1) anyio scope with an awaiting provider
    p = AwaitingAsyncioLocks()
    with anyio.move_on_after(0.05) as scope:
        await run(p, Msg("k1", "k2"), slow)
    assert scope.cancelled_caught
    assert p.locks["k1"].locked() or p.locks["k2"].locked(), "returned before background finished"
    await asyncio.sleep(0.1)
    assert not p.locks["k1"].locked() and not p.locks["k2"].locked(), "keys must be freed"
    print("(1) anyio scope: keys freed by the background task; logs", cap.records)

    # (2) task-owned provider on inline paths
    for awaiting in (False, True):
        q = AnyioOwned(awaiting)
        assert await run(q, Msg("a", "b"), ok) == "done"
        try:
            await run(q, Msg("a", "b"), boom)
        except ValueError:
            pass
        t = asyncio.create_task(run(q, Msg("a", "b"), slow))
        await asyncio.sleep(0.01)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        assert not q.locks["a"].locked() and not q.locks["b"].locked()
    assert cap.records == [], cap.records
    print("(2) anyio.Lock provider: success / failing handler / single cancel all release inline")

    # (3) task-owned awaiting provider inside a cancelled anyio scope
    q = AnyioOwned(awaiting=True)
    with anyio.move_on_after(0.02):
        await run(q, Msg("a", "b"), slow)
    await asyncio.sleep(0.05)
    print("(3) logs:", cap.records, "held:", q.locks["a"].locked(), q.locks["b"].locked())
    assert q.locks["a"].locked() and q.locks["b"].locked()
    assert cap.records and all(lvl == "WARNING" and m.startswith("Abandoned lock release for Msg failed with ") for lvl, m in cap.records)
    print("OK")


anyio.run(main)
