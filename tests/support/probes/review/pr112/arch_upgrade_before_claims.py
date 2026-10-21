"""Upgrade-note 'Before' column, run against origin/dev: the release error replaced the
handler's error; one raising release skipped later keys; a cancel during the second acquire
kept the first key. Run against the branch it must print the 'Now' behaviour instead."""
import asyncio
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch_common import Msg, run  # noqa: E402


class P:
    def __init__(self, fail=(), block=None):
        self.fail, self.block, self.released = set(fail), block, []

    async def acquire(self, k):
        if k == self.block:
            await asyncio.sleep(10)

    async def release(self, k):
        self.released.append(k)
        if k in self.fail:
            raise ConnectionError()


async def boom():
    raise ValueError()


async def ok():
    return 1


async def main():
    p = P(fail={"b"})
    try:
        await run(p, Msg("a", "b"), boom)
    except BaseException as e:
        print("handler+release raised ->", type(e).__name__)
    p = P(fail={"b"})
    try:
        await run(p, Msg("a", "b"), ok)
    except BaseException as e:
        print("success+release raised ->", type(e).__name__)
    print("released after raising release:", p.released)
    p = P(block="b")
    t = asyncio.create_task(run(p, Msg("a", "b"), ok))
    await asyncio.sleep(0.01)
    t.cancel()
    await asyncio.gather(t, return_exceptions=True)
    print("released after cancel on 2nd acquire:", p.released)


asyncio.run(main())
