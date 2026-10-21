"""ADR-076 decisions 1, 2 and 4 (a)(b)(c) as the docs table states them, plus a provider's
own CancelledError (no cancel requested) being that release's failure under (c), and a cancel
during the second acquire releasing the first key."""
import asyncio
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch_common import Msg, capture, run  # noqa: E402

S = "SENTINEL-KEY-77"


class P:
    def __init__(self, fail=None, block_acquire=None):
        self.fail = fail or {}
        self.block_acquire = block_acquire
        self.released = []

    async def acquire(self, key):
        if key == self.block_acquire:
            await asyncio.sleep(10)

    async def release(self, key):
        self.released.append(key)
        exc = self.fail.get(key)
        if exc is not None:
            raise exc


async def ok():
    return "result"


async def boom():
    raise ValueError("handler")


async def main():
    keys = [f"{S}-a", f"{S}-b", f"{S}-c"]
    # (a)
    cap = capture()
    p = P({keys[1]: ConnectionError(S)})
    assert await run(p, Msg(*keys), ok) == "result"
    assert p.released == keys[::-1]
    assert cap.records == [("WARNING", "Lock release failed for Msg: ConnectionError")], cap.records
    # (b)
    cap.records.clear()
    p = P({keys[2]: ConnectionError(S), keys[0]: TimeoutError(S)})
    try:
        await run(p, Msg(*keys), boom)
    except ValueError as e:
        assert e.__notes__ == ["clean-up also failed: ConnectionError", "clean-up also failed: TimeoutError"], e.__notes__
    assert p.released == keys[::-1] and cap.records == []
    # (c) KeyboardInterrupt from a middle release, after the handler succeeded
    p = P({keys[2]: ConnectionError(S), keys[1]: KeyboardInterrupt()})
    try:
        await run(p, Msg(*keys), ok)
        raise AssertionError("no raise")
    except KeyboardInterrupt as e:
        assert p.released == keys[::-1], "remaining key must still be released"
        assert e.__notes__ == ["clean-up also failed: ConnectionError"], e.__notes__
    # (c) provider's own CancelledError, no cancel requested
    p = P({keys[2]: asyncio.CancelledError()})
    try:
        await run(p, Msg(*keys), ok)
        raise AssertionError("no raise")
    except asyncio.CancelledError:
        assert p.released == keys[::-1], "own CancelledError must not hand over; all released inline"
    # decision 1: cancel during the second acquire releases the first only
    p = P(block_acquire=keys[1])
    t = asyncio.create_task(run(p, Msg(*keys), ok))
    await asyncio.sleep(0.01)
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    assert p.released == [keys[0]], p.released
    print("OK")


asyncio.run(main())
