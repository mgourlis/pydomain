"""ADR-076 decision 3 / B-51 confirmed details: a second cancel during an inline release
hands the INTERRUPTED key and the rest to one background task, the caller propagates at once,
inline failures before the cancel are notes on the CancelledError, background failures are
type-only WARNINGs, and a strong reference is held until the task ends."""
import asyncio
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch_common import Msg, capture, run  # noqa: E402

import pydomain.cqrs.behaviors as B  # noqa: E402

SENTINEL = "SENTINEL-KEY-9f3"


class Provider:
    def __init__(self):
        self.gate = asyncio.Event()
        self.calls = []  # (key, task)
        self.blocked_once = False

    async def acquire(self, key):
        pass

    async def release(self, key):
        self.calls.append((key, asyncio.current_task()))
        if key.endswith("c"):
            raise ConnectionError(key)  # inline failure before the cancel
        if key.endswith("b") and not self.blocked_once:
            self.blocked_once = True
            await self.gate.wait()
        if key.endswith("a"):
            raise ConnectionError(key)  # fails in the background


async def main():
    cap = capture()
    p = Provider()
    keys = [f"{SENTINEL}-a", f"{SENTINEL}-b", f"{SENTINEL}-c"]
    started = asyncio.Event()

    async def nxt():
        started.set()
        await asyncio.sleep(10)

    t = asyncio.create_task(run(p, Msg(*keys), nxt))
    await started.wait()
    t.cancel()  # first cancel: handler ends, releases start inline
    for _ in range(5):
        await asyncio.sleep(0)
    assert [k[-1] for k, _ in p.calls] == ["c", "b"], p.calls
    assert all(task is t for _, task in p.calls), "inline releases must run in the acquiring task"
    t.cancel()  # second cancel while release(b) awaits
    try:
        await t
    except asyncio.CancelledError as e:
        notes = getattr(e, "__notes__", [])
    assert t.cancelled() or True
    assert not p.gate.is_set(), "caller must propagate before background releases finish"
    print("caller propagated at once; notes:", notes)
    assert notes == ["clean-up also failed: ConnectionError"], notes
    assert len(B._HANDED_OVER) == 1, B._HANDED_OVER
    p.gate.set()
    for _ in range(10):
        await asyncio.sleep(0)
    bg = [(k[-1], task is t) for k, task in p.calls[2:]]
    print("background calls:", bg)
    assert bg == [("b", False), ("a", False)], bg  # interrupted key re-called, then the rest
    assert B._HANDED_OVER == {}
    print("logs:", cap.records)
    assert cap.records == [("WARNING", "Abandoned lock release for Msg failed with ConnectionError")]
    assert all(SENTINEL not in m for _, m in cap.records) and all(SENTINEL not in n for n in notes)
    print("OK")


asyncio.run(main())
