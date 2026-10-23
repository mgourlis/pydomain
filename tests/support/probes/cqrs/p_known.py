"""Probes for CQRS-K1..K5 (CQRS-K2 fixed by DCE-150)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import *  # noqa: F403
from pydomain.cqrs.behaviors import (
    AggregateLockingBehavior,
    IdempotencyBehavior,
)
from pydomain.cqrs.exceptions import CommandExecutionError, CQRSError
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.ddd.exceptions import DomainError
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import FakeLockProvider, FakeProcessedMessageStore


async def k1() -> None:
    log: list[str] = []
    db: dict = {}
    store = FakeProcessedMessageStore()
    calls = 0
    uows: list[SpyUoW] = []

    def factory():
        u = SpyUoW(log, db, fail_commit=1 if not uows else 0)
        uows.append(u)
        return u

    async def handler(cmd, uow):
        nonlocal calls
        calls += 1
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        return PlaceOrderResult(order_id=cmd.order_id)

    bus = CommandBus()
    bus.register(PlaceOrder, handler, factory, behaviors=[IdempotencyBehavior(store)])
    cmd = PlaceOrder(order_id=new_id())
    try:
        await bus.dispatch(cmd)
    except CommandExecutionError as e:
        print("K1 first dispatch failed:", type(e.__cause__).__name__)
    res, events = await bus.dispatch(cmd)
    print(f"K1 retry returned {type(res).__name__}, handler calls={calls}, events={len(events)}, "
          f"order in db={str(cmd.order_id) in db}")


class SpyLocks(FakeLockProvider):
    def __init__(self, log):
        super().__init__()
        self.log = log

    async def acquire(self, key):
        await super().acquire(key)
        self.log.append(f"acquire:{key}")

    async def release(self, key):
        self.log.append(f"release:{key}")
        await super().release(key)


async def k3() -> None:
    log: list[str] = []
    db: dict = {}
    locks = SpyLocks(log)
    resolver = DictLockKeyResolver()
    resolver.register(PlaceOrder, lambda c: [f"order:{c.order_id}"])
    bus = CommandBus()

    async def handler(cmd, uow):
        log.append("handler")
        return PlaceOrderResult(order_id=cmd.order_id)

    bus.register(PlaceOrder, handler, lambda: SpyUoW(log, db),
                 behaviors=[AggregateLockingBehavior(locks, resolver)])
    await bus.dispatch(PlaceOrder(order_id=new_id()))
    print("K3 order:", [x.split(":")[0] for x in log])

    # Lost update: read-modify-write of a counter, commit delayed by an await.
    counter = {"n": 0}
    oid = new_id()

    class CounterUoW(SpyUoW):
        async def _commit(self):
            await asyncio.sleep(0.01)
            counter["n"] = self.pending

    async def inc(cmd, uow):
        uow.pending = counter["n"] + 1
        return PlaceOrderResult(order_id=cmd.order_id)

    bus2 = CommandBus()
    locks2 = FakeLockProvider()
    r2 = DictLockKeyResolver()
    r2.register(PlaceOrder, lambda c: [f"order:{c.order_id}"])
    bus2.register(PlaceOrder, inc, lambda: CounterUoW([], {}),
                  behaviors=[AggregateLockingBehavior(locks2, r2)])
    await asyncio.gather(bus2.dispatch(PlaceOrder(order_id=oid)), bus2.dispatch(PlaceOrder(order_id=oid)))
    print(f"K3 two locked increments -> counter={counter['n']} (2 expected if lock covered commit)")


def k4() -> None:
    print("K4 issubclass(CQRSError, DomainError) =", issubclass(CQRSError, DomainError))


async def k5() -> None:
    log: list[str] = []
    db: dict = {}
    shared_repo = BufferRepo()  # repository outliving the UoW
    seen: list[str] = []
    mode = {"fail": True}

    async def handler(cmd, uow):
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        if mode["fail"]:
            raise RuntimeError("synthetic business failure after save")
        return PlaceOrderResult(order_id=cmd.order_id)

    mb = MessageBus()
    mb.register_command(PlaceOrder, handler, lambda: SpyUoW(log, db, repo=shared_repo))

    async def on_placed(ev):
        seen.append(str(ev.order_id))

    mb.register_event(OrderPlaced, on_placed)
    failed = PlaceOrder(order_id=new_id())
    try:
        await mb.dispatch(failed)
    except CommandExecutionError:
        pass
    mode["fail"] = False
    ok = PlaceOrder(order_id=new_id())
    await mb.dispatch(ok)
    print(f"K5 handlers saw rolled-back order: {str(failed.order_id) in seen}; events published={len(seen)}")

    # Same with a repository created per UoW (docstring pattern)
    seen.clear()
    mode["fail"] = True
    mb2 = MessageBus()
    mb2.register_command(PlaceOrder, handler, lambda: SpyUoW(log, db))
    mb2.register_event(OrderPlaced, on_placed)
    failed = PlaceOrder(order_id=new_id())
    try:
        await mb2.dispatch(failed)
    except CommandExecutionError:
        pass
    mode["fail"] = False
    await mb2.dispatch(PlaceOrder(order_id=new_id()))
    print(f"K5 per-UoW repo: rolled-back order leaked = {str(failed.order_id) in seen}")


async def main() -> None:
    await k1()
    await k3()
    k4()
    await k5()


asyncio.run(main())
