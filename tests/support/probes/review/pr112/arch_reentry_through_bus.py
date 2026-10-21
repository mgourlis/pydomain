"""ADR-076 decision 5 / B-48, B-52 doc claims: a same-key nested dispatch through the bus fails
with CommandExecutionError whose cause is the nested CommandExecutionError whose cause is
LockReentryError (message names no key); the outer key is freed afterwards; a nested dispatch
in a new task is not caught by the fake (it waits)."""
import asyncio
from uuid import UUID

from pydomain import LockReentryError as TopLevel
from pydomain.cqrs import AggregateLockingBehavior, Command, CommandResult, LockReentryError
from pydomain.cqrs.exceptions import CQRSError
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.infrastructure import bootstrap
from pydomain.testing import FakeLockProvider, FakeUnitOfWork

S = "SENTINEL-ORDER"


class R(CommandResult):
    ok: bool = True


class Outer(Command[R]):
    order_id: UUID
    spawn: bool = False


class Inner(Command[R]):
    order_id: UUID


async def main():
    assert TopLevel is LockReentryError and issubclass(LockReentryError, CQRSError)
    app = await bootstrap()
    locks = FakeLockProvider()
    res = DictLockKeyResolver()
    res.register(Outer, lambda c: [f"order-{S}-{c.order_id}"])
    res.register(Inner, lambda c: [f"order-{S}-{c.order_id}"])
    beh = [AggregateLockingBehavior(locks, res)]

    class OuterH:
        async def __call__(self, c, uow):
            if c.spawn:
                await asyncio.wait_for(asyncio.create_task(app.dispatch(Inner(order_id=c.order_id))), 0.1)
            else:
                await app.dispatch(Inner(order_id=c.order_id))
            return R()

    class InnerH:
        async def __call__(self, c, uow):
            return R()

    app.message_bus.register_command(Outer, OuterH(), FakeUnitOfWork, behaviors=beh)
    app.message_bus.register_command(Inner, InnerH(), FakeUnitOfWork, behaviors=beh)
    oid = UUID("00000000-0000-4000-8000-000000000001")
    try:
        await app.dispatch(Outer(order_id=oid))
        raise AssertionError("no raise")
    except Exception as e:
        chain = []
        x = e
        while x is not None:
            chain.append(type(x).__name__)
            x = x.__cause__
        print("chain:", chain)
        assert chain == ["CommandExecutionError", "CommandExecutionError", "LockReentryError"], chain
        assert S not in str(e.__cause__.__cause__)
    # key is free again
    await asyncio.wait_for(locks.acquire(f"order-{S}-{oid}"), 0.1)
    await locks.release(f"order-{S}-{oid}")
    # new task: waits instead of raising (here bounded by wait_for -> TimeoutError)
    try:
        await app.dispatch(Outer(order_id=oid, spawn=True))
    except Exception as e:
        print("spawned nested:", type(e).__name__, "<-", type(e.__cause__).__name__)
        assert isinstance(e.__cause__, TimeoutError)
    await app.shutdown()
    print("OK")


asyncio.run(main())
