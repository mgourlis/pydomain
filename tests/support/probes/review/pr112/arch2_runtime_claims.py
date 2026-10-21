"""Round-2 runtime claims: (1) converted and swallowed cancellations hand over (converted:
interrupted key retried; swallowed: only later keys), the caller gets CancelledError;
(2) asyncio.timeout firing during a release -> TimeoutError at once, keys freed in background;
(3) B-69: no asyncio loop (coroutine driven by hand) -> inline, a thrown CancelledError during a
release is that release's failure, the rest are released, then it is re-raised;
(4) B-68 fake: stale token no-op, missing token RuntimeError with no key, KeyError for unknown key."""
import asyncio
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from arch_common import Msg, behavior, capture, run  # noqa: E402

from pydomain.cqrs.behaviors import MessageContext  # noqa: E402
from pydomain.testing import FakeLockProvider  # noqa: E402

S = "SENTINEL-KEY-31"


class Mode:
    def __init__(self, mode):
        self.mode, self.calls, self.gate = mode, [], asyncio.Event()

    async def acquire(self, k):
        pass

    async def release(self, k):
        self.calls.append((k, asyncio.current_task()))
        if k.endswith("b") and len(self.calls) == 1:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                if self.mode == "convert":
                    raise ConnectionError()
                if self.mode == "swallow":
                    return
                raise


async def handed_over(mode):
    p = Mode(mode)
    started = asyncio.Event()

    async def nxt():
        started.set()
        return "r"

    t = asyncio.create_task(run(p, Msg("a", "b"), nxt))
    await asyncio.sleep(0.01)  # handler done, release(b) waiting
    t.cancel()
    res = await asyncio.gather(t, return_exceptions=True)
    p.gate.set()
    await asyncio.sleep(0.01)
    return type(res[0]).__name__, [(k, task is t) for k, task in p.calls]


class SlowRelease:
    def __init__(self):
        self.held = set()

    async def acquire(self, k):
        self.held.add(k)

    async def release(self, k):
        await asyncio.sleep(0.05)
        self.held.discard(k)


def drive_by_hand():
    class P:
        released = []

        async def acquire(self, k):
            pass

        async def release(self, k):
            P.released.append(k)
            if k == "b":
                await _Yield()

    class _Yield:
        def __await__(self):
            yield "suspend"

    async def nxt():
        return "r"

    coro = behavior(P()).handle(MessageContext(message=Msg("a", "b", "c")), nxt)
    assert coro.send(None) == "suspend"  # suspended inside release("b"), no loop running
    try:
        coro.throw(asyncio.CancelledError())
    except asyncio.CancelledError:
        return P.released
    raise AssertionError("not re-raised")


async def main():
    cap = capture()
    for mode in ("raise", "convert", "swallow"):
        print(mode, await handed_over(mode))
    a = await handed_over("convert")
    assert a == ("CancelledError", [("b", True), ("b", False), ("a", False)]), a
    s = await handed_over("swallow")
    assert s == ("CancelledError", [("b", True), ("a", False)]), s

    p = SlowRelease()

    async def ok():
        return 1

    try:
        async with asyncio.timeout(0.02):
            await run(p, Msg("k1", "k2"), ok)
    except TimeoutError:
        print("asyncio.timeout -> TimeoutError; held right after:", sorted(p.held))
    await asyncio.sleep(0.15)
    assert p.held == set(), p.held

    f = FakeLockProvider()
    await f.acquire(f"{S}")
    await f.release(f"{S}")
    try:
        await f.release(f"{S}")  # B-81: second release in one context raises
        raise AssertionError("no raise")
    except RuntimeError as e:
        assert S not in str(e)
    await f.acquire(f"{S}")
    err = await asyncio.gather(asyncio.create_task(_bare_release(f)), return_exceptions=True)
    print("unrelated task release:", type(err[0]).__name__, S in str(err[0]))
    assert isinstance(err[0], RuntimeError) and S not in str(err[0])
    try:
        await f.release("unknown")
    except KeyError as e:
        print("unknown key:", type(e).__name__, "names key:", "unknown" in str(e))
    print("logs:", cap.records)


async def _bare_release(f):
    # a task started while the key is held *by main* sees main's token; start from a clean context
    import contextvars
    ctx = contextvars.Context()
    return await asyncio.get_running_loop().create_task(f.release(S), context=ctx)


print("B-69 inline release order:", drive_by_hand())
asyncio.run(main())
print("OK")
