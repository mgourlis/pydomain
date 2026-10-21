"""B-81: a matching FakeLockProvider release drops its token (context mapping stays bounded);
a copy taken before the release keeps a stale token whose late release is a no-op.
B-82: the background retry of a release that already took effect is refused with the
missing-token RuntimeError, logged as a type-only WARNING, and the next holder keeps the key."""
import asyncio
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch_common import Msg, capture, run  # noqa: E402

import pydomain.testing.fake_lock_provider as F  # noqa: E402
from pydomain.testing import FakeLockProvider  # noqa: E402


class EffectiveThenSlow(FakeLockProvider):
    async def release(self, key):
        await super().release(key)   # takes effect, drops the token
        await asyncio.sleep(0.05)    # the caller is interrupted here


async def main():
    f = FakeLockProvider()
    for i in range(500):
        await f.acquire(f"k{i}")
        await f.release(f"k{i}")
    assert len(F._TOKENS.get()) == 0, len(F._TOKENS.get())

    # stale copy: a task started while holding, released late after another holder took it
    await f.acquire("s")
    go = asyncio.Event()

    async def late():
        await go.wait()
        await f.release("s")  # stale token -> no-op

    t = asyncio.create_task(late())
    await asyncio.sleep(0)
    await f.release("s")
    other_in = asyncio.Event()
    other_out = asyncio.Event()

    async def other():
        await f.acquire("s")
        other_in.set()
        await other_out.wait()
        await f.release("s")

    o = asyncio.create_task(other())
    await other_in.wait()
    go.set()
    await t
    assert f._locks["s"].locked(), "late stale release freed the other holder"
    other_out.set()
    await o

    # B-82
    cap = capture()
    p = EffectiveThenSlow()
    started = asyncio.Event()

    async def first():
        started.set()
        await asyncio.sleep(10)

    t1 = asyncio.create_task(run(p, Msg("order-1"), first))
    await started.wait()
    t1.cancel()
    await asyncio.sleep(0.01)  # release took effect, now sleeping
    holder_in = asyncio.Event()
    leave = asyncio.Event()

    async def next_holder():
        await p.acquire("order-1")
        holder_in.set()
        await leave.wait()
        await FakeLockProvider.release(p, "order-1")

    h = asyncio.create_task(next_holder())
    await holder_in.wait()
    t1.cancel()
    await asyncio.gather(t1, return_exceptions=True)
    await asyncio.sleep(0.02)
    print("logs:", cap.records)
    assert cap.records == [("WARNING", "Abandoned lock release for Msg failed with RuntimeError")]
    assert p._locks["order-1"].locked(), "next holder lost the key"
    leave.set()
    await h
    print("OK")


asyncio.run(main())
