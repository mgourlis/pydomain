"""FAKE-06 / FAKE-07 probes. Synthetic."""
import asyncio
from uuid import uuid4
from pydomain.cqrs.idempotency import MISSING, ProcessedMessageStore
from pydomain.cqrs.locking import LockProvider
from pydomain.testing import FakeLockProvider, FakeProcessedMessageStore

async def f06():
    s = FakeProcessedMessageStore(); cid = uuid4()
    print("06 isinstance:", isinstance(s, ProcessedMessageStore))
    await s.set(cid, {"result": "R"})
    print("06 command result cached, then event check_and_set(same id) ->", await s.check_and_set(cid))
    s2 = FakeProcessedMessageStore(); cid2 = uuid4()
    print("06 event check_and_set first ->", await s2.check_and_set(cid2), "; then command get ->", await s2.get(cid2), "(is MISSING:", (await s2.get(cid2)) is MISSING, ")")
    s3 = FakeProcessedMessageStore(); k = uuid4()
    async def handler_that_fails():
        if await s3.check_and_set(k):
            return "skipped"
        raise RuntimeError("handler failed after mark")
    try: await handler_that_fails()
    except RuntimeError as e: print("06 first attempt:", e)
    print("06 retry after failure ->", await handler_that_fails(), "(mark not rolled back)")

async def f07():
    p = FakeLockProvider()
    try: await p.release("never")
    except Exception as e: print("07 release never-acquired:", type(e).__name__, e)
    await p.acquire("k"); await p.release("k")
    try: await p.release("k")
    except Exception as e: print("07 release twice:", type(e).__name__, e)
    # owner check
    p = FakeLockProvider(); log = []
    a_holds = asyncio.Event(); done = asyncio.Event()
    async def A():
        await p.acquire("x"); log.append("A acquired"); a_holds.set(); await done.wait()
    async def B():
        await a_holds.wait(); await p.release("x"); log.append("B released A's lock")
    async def C():
        await a_holds.wait(); await asyncio.sleep(0); await asyncio.sleep(0)
        await p.acquire("x"); log.append("C acquired while A still in critical section")
        done.set()
    await asyncio.wait_for(asyncio.gather(A(), B(), C()), 2)
    print("07 owner:", log)
    # timeout
    p = FakeLockProvider(); await p.acquire("t")
    try:
        await asyncio.wait_for(p.acquire("t"), 0.1)
    except TimeoutError: print("07 second acquire blocks (no built-in timeout; caller wait_for works)")
    # cancellation of a waiter then release: lock usable?
    t = asyncio.create_task(p.acquire("t")); await asyncio.sleep(0); t.cancel()
    try: await t
    except asyncio.CancelledError: pass
    await p.release("t"); await asyncio.wait_for(p.acquire("t"), 0.1); print("07 after cancelled waiter, acquire OK")
    print("07 locks dict size after keys:", len(p._locks))
    print("07 isinstance LockProvider:", isinstance(p, LockProvider))

asyncio.run(f06()); asyncio.run(f07())
