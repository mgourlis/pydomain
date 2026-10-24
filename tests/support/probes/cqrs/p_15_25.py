"""Probes for CQRS-15..25 (synthetic order data)."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).parent))

from common import *  # noqa: F403
from pydomain.cqrs.behaviors import AggregateLockingBehavior, LoggingBehavior, ValidationBehavior
from pydomain.cqrs.commands import Command, EmptyCommandResult
from pydomain.cqrs.event_bus import EventBus
from pydomain.cqrs.exceptions import CommandExecutionError, NoHandlerRegisteredError
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry
from pydomain.cqrs.queries import Query, QueryResult
from pydomain.cqrs.query_bus import QueryBus
from pydomain.cqrs.locking import DictLockKeyResolver
from pydomain.testing import FakeLockProvider, FakeUnitOfWork


class Captured(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        out = []
        for r in self.records:
            out.append(self.format(r))
            out.append(repr(getattr(r, "payload", "")))
        return "\n".join(out)


cap = Captured()
cap.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
for name in ("pydomain.pipeline", "pydomain.event_bus", "pydomain.message_bus"):
    lg = logging.getLogger(name)
    lg.addHandler(cap)
    lg.setLevel(logging.DEBUG)


class OrderPlacedV1(IntegrationEvent):
    order_id: str


class PlaceRushOrder(PlaceOrder):
    pass


class OrderPlacedExpress(OrderPlaced):
    pass


async def c17() -> None:
    eb = EventBus()
    hit: list[str] = []

    async def h(ev):
        hit.append(type(ev).__name__)

    eb.register(OrderPlaced, h)
    await eb.dispatch(OrderPlacedExpress(order_id=new_id()))
    print(f"CQRS-17 EventBus subclass event: handlers ran={hit} (silent)")

    vb = ValidationBehavior()
    vb.register(PlaceOrder, lambda c: (_ for _ in ()).throw(ValueError("invalid")))
    res = DictLockKeyResolver()
    res.register(PlaceOrder, lambda c: [f"order:{c.order_id}"])
    locks = FakeLockProvider()
    acquired: list[str] = []
    orig = locks.acquire

    async def spy_acquire(key: str) -> None:
        acquired.append(key)
        await orig(key)

    locks.acquire = spy_acquire  # type: ignore[method-assign]

    async def ok(cmd, uow):
        return PlaceOrderResult(order_id=cmd.order_id)

    bus = CommandBus()
    bus.register(PlaceRushOrder, ok, lambda: SpyUoW([], {}),
                 behaviors=[vb, AggregateLockingBehavior(locks, res)])
    r, _ = await bus.dispatch(PlaceRushOrder(order_id=new_id()))
    print(f"CQRS-17 subclass command: validator for base skipped (result={type(r).__name__}), "
          f"locks acquired={acquired}, resolver keys={res.resolve(PlaceRushOrder(order_id=new_id()))}")
    bus2 = CommandBus()
    bus2.register(PlaceOrder, ok, lambda: SpyUoW([], {}))
    try:
        await bus2.dispatch(PlaceRushOrder(order_id=new_id()))
    except NoHandlerRegisteredError as x:
        print(f"CQRS-17 subclass command, base handler only: raises {type(x).__name__} (not silent)")
    reg = OutboundEventRegistry()
    reg.register_translation(OrderPlaced, OrderPlacedV1, "orders.placed", lambda e: OrderPlacedV1(order_id=str(e.order_id)))
    print(f"CQRS-17 outbox subclass event -> entry={reg.to_entry(OrderPlacedExpress(order_id=new_id()))} (silently unpublished)")


async def c18() -> None:
    eb = EventBus()
    order: list[str] = []

    async def late(ev):
        order.append("late")

    async def first(ev):
        order.append("first")
        eb.register(OrderPlaced, late)

    eb.register(OrderPlaced, first)
    await eb.dispatch(OrderPlaced(order_id=new_id()))
    print(f"CQRS-18 handler registered during dispatch ran in same dispatch: {order}")

    eb2 = EventBus()
    ran: list[str] = []

    async def cancels(ev):
        ran.append("cancels")
        raise asyncio.CancelledError

    async def after(ev):
        ran.append("after")

    eb2.register(OrderPlaced, cancels)
    eb2.register(OrderPlaced, after)
    try:
        await eb2.dispatch(OrderPlaced(order_id=new_id()))
    except asyncio.CancelledError:
        pass
    print(f"CQRS-18 CancelledError in handler 1: ran={ran} (handler 2 skipped, propagates)")



class GetOrderResult(QueryResult):
    order_id: UUID


class GetOrder(Query[GetOrderResult]):
    order_id: UUID


async def c19() -> None:
    seen: dict[str, Any] = {}

    class Peek:
        async def handle(self, ctx, next):
            seen["corr"] = ctx.correlation_id
            return await next()

    async def h(q):
        return GetOrderResult(order_id=q.order_id)

    qb = QueryBus()
    qb.register(GetOrder, h, behaviors=[Peek()])
    q = GetOrder(order_id=new_id())
    await qb.dispatch(q)
    print(f"CQRS-19 query ctx.correlation_id == query_id: {seen['corr'] == q.query_id}; "
          f"Query has correlation field: {'correlation_id' in GetOrder.model_fields}")
    try:
        GetOrder(order_id=new_id(), correlation_id=uuid4())  # type: ignore[call-arg]
    except Exception as x:  # noqa: BLE001
        print(f"CQRS-19 caller cannot pass correlation_id: {type(x).__name__} (extra=forbid)")


class Rename(Command[EmptyCommandResult]):
    order_id: UUID
    new_title: str


async def c20() -> None:
    sentinel = "SENTINEL-XYZ"

    async def h(cmd, uow):
        raise ValueError(f"cannot rename to {cmd.new_title}")

    bus = CommandBus()
    bus.register(Rename, h, lambda: SpyUoW([], {}), behaviors=[LoggingBehavior()])
    cap.records.clear()
    try:
        await bus.dispatch(Rename(order_id=new_id(), new_title=sentinel))
    except CommandExecutionError as e:
        print(f"CQRS-20 str(CommandExecutionError)={str(e)!r}; sentinel in it: {sentinel in str(e)}")
    print(f"CQRS-20 default LoggingBehavior: {len(cap.records)} records, sentinel logged: "
          f"{sentinel in cap.text()}")

    # cancellation: entry logged, nothing after
    async def slow(cmd, uow):
        await asyncio.sleep(1)

    bus2 = CommandBus()
    bus2.register(Rename, slow, lambda: SpyUoW([], {}), behaviors=[LoggingBehavior()])
    cap.records.clear()
    t = asyncio.create_task(bus2.dispatch(Rename(order_id=new_id(), new_title="t")))
    await asyncio.sleep(0.01)
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    print(f"CQRS-20 on cancellation: log levels={[r.levelname + ':' + r.getMessage()[:10] for r in cap.records]}")

    # raising payload formatter fails the command
    def bad_fmt(m):
        raise KeyError("formatter bug")

    ran = []

    async def ok(cmd, uow):
        ran.append(1)
        return EmptyCommandResult()

    bus3 = CommandBus()
    bus3.register(Rename, ok, lambda: SpyUoW([], {}), behaviors=[LoggingBehavior(payload_formatter=bad_fmt)])
    try:
        await bus3.dispatch(Rename(order_id=new_id(), new_title="t"))
    except CommandExecutionError as e:
        print(f"CQRS-20 raising payload_formatter: command failed ({type(e.__cause__).__name__}), handler ran={len(ran)}")


class Invoice(Order):
    def issue(self) -> None:
        self._add_event(InvoiceIssued(invoice_id=self.id))


async def c23() -> None:
    class TwoRepoUoW(SpyUoW):
        def __init__(self) -> None:
            super().__init__([], {})
            self.invoices = BufferRepo()
            self._repos = {"orders": self.orders, "invoices": self.invoices}

    async def h(cmd, uow):
        inv = Invoice(id=new_id())
        inv.issue()                 # raised first
        await uow.invoices.save(inv)
        o = Order(id=cmd.order_id)
        o.place()                   # raised second
        await uow.orders.save(o)
        return PlaceOrderResult(order_id=cmd.order_id)

    bus = CommandBus()
    bus.register(PlaceOrder, h, lambda: TwoRepoUoW())
    _, ev = await bus.dispatch(PlaceOrder(order_id=new_id()))
    print(f"CQRS-23 raise order [InvoiceIssued, OrderPlaced] -> collected {[type(e).__name__ for e in ev]}")


async def c24() -> None:
    u = SpyUoW([], {})
    async with u:
        o = Order(id=new_id())
        o.place()
        await u.orders.save(o)
    print(f"CQRS-24 clean exit without commit: rollbacks={u.rollbacks}, staged left={len(u.orders.staged)}, "
          f"buffered events left={len(u.orders.buffer)}")
    f = FakeUnitOfWork()
    async with f:
        pass
    print(f"CQRS-24 FakeUnitOfWork clean exit without commit: _rolled_back={f._rolled_back}")


async def main() -> None:
    await c17()
    await c18()
    await c19()
    await c20()
    await c23()
    await c24()


asyncio.run(main())
