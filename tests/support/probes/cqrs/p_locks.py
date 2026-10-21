"""Probes for CQRS-01, 02, 03 (lock behavior)."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import *  # noqa: F403
from pydomain.cqrs.behaviors import AggregateLockingBehavior
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import FakeLockProvider


class TwoKeys(PlaceOrder):
    pass


async def is_held(locks, key) -> bool:
    try:
        await asyncio.wait_for(locks.acquire(key), 0.05)
    except TimeoutError:
        return True
    await locks.release(key)
    return False


async def p01() -> None:
    locks = FakeLockProvider()
    r = DictLockKeyResolver()
    r.register(PlaceOrder, lambda c: ["order:a", "order:b"])
    bus = CommandBus()

    async def handler(cmd, uow):
        return PlaceOrderResult(order_id=cmd.order_id)

    bus.register(PlaceOrder, handler, lambda: SpyUoW([], {}), behaviors=[AggregateLockingBehavior(locks, r)])
    await locks.acquire("order:b")  # another holder of lock 2
    t = asyncio.create_task(bus.dispatch(PlaceOrder(order_id=new_id())))
    await asyncio.sleep(0.02)  # t now holds a, waits on b
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        print("01 dispatch task cancelled while waiting on lock b")
    await locks.release("order:b")
    print("01 lock a still held after cancel =", await is_held(locks, "order:a"))

    # Control: cancellation while inside next() (handler) releases fine.
    locks2 = FakeLockProvider()
    bus2 = CommandBus()

    async def slow(cmd, uow):
        await asyncio.sleep(1)

    bus2.register(PlaceOrder, slow, lambda: SpyUoW([], {}), behaviors=[AggregateLockingBehavior(locks2, r)])
    t2 = asyncio.create_task(bus2.dispatch(PlaceOrder(order_id=new_id())))
    await asyncio.sleep(0.02)
    t2.cancel()
    try:
        await t2
    except asyncio.CancelledError:
        pass
    print("01 control (cancel in handler): a held =", await is_held(locks2, "order:a"),
          "b held =", await is_held(locks2, "order:b"))


class FailingRelease(FakeLockProvider):
    async def release(self, key):
        if key == "order:b":
            raise ConnectionError("synthetic release failure")
        await super().release(key)


async def p02() -> None:
    locks = FailingRelease()
    r = DictLockKeyResolver()
    r.register(PlaceOrder, lambda c: ["order:a", "order:b"])
    bus = CommandBus()

    async def handler(cmd, uow):
        return PlaceOrderResult(order_id=cmd.order_id)

    bus.register(PlaceOrder, handler, lambda: SpyUoW([], {}), behaviors=[AggregateLockingBehavior(locks, r)])
    try:
        await bus.dispatch(PlaceOrder(order_id=new_id()))
        print("02 dispatch succeeded")
    except CommandExecutionError as e:
        print("02 dispatch raised CommandExecutionError from", type(e.__cause__).__name__,
              "- handler result lost although handler succeeded")
    print("02 lock a still held =", await is_held(locks, "order:a"))


async def p03() -> None:
    locks = FakeLockProvider()
    r = DictLockKeyResolver()
    r.register(PlaceOrder, lambda c: [f"order:{c.order_id}"])
    r.register(TwoKeys, lambda c: [f"order:{c.order_id}"])
    mb = MessageBus()

    async def inner(cmd, uow):
        return PlaceOrderResult(order_id=cmd.order_id)

    async def outer(cmd, uow):
        await mb.dispatch(TwoKeys(order_id=cmd.order_id))  # nested, same key
        return PlaceOrderResult(order_id=cmd.order_id)

    beh = [AggregateLockingBehavior(locks, r)]
    mb.register_command(PlaceOrder, outer, lambda: SpyUoW([], {}), beh)
    mb.register_command(TwoKeys, inner, lambda: SpyUoW([], {}), beh)
    try:
        await asyncio.wait_for(mb.dispatch(PlaceOrder(order_id=new_id())), 0.5)
        print("03 nested dispatch completed")
    except TimeoutError:
        print("03 nested dispatch with same lock key deadlocked (timeout 0.5s)")


async def main() -> None:
    await p01()
    await p02()
    await p03()


asyncio.run(main())
