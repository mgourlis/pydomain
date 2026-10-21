"""Probes for CQRS-10..14 and A16 (synthetic order data)."""

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from common import *  # noqa: F403
from pydomain.cqrs.behaviors import MessageContext
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxWriter
from pydomain.cqrs.unit_of_work import UnitOfWork
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError, DomainError
from pydomain.testing import FakeOutboxStore


class OrderPlacedV1(IntegrationEvent):
    order_id: str


def registry() -> OutboundEventRegistry:
    r = OutboundEventRegistry()
    r.register_translation(OrderPlaced, OrderPlacedV1, "orders.placed",
                           lambda e: OrderPlacedV1(order_id=str(e.order_id)))
    return r


async def c10() -> None:
    store = FakeOutboxStore()
    u = SpyUoW([], {}, outbox_writer=OutboxWriter(store, registry()), fail_commit=1)
    async with u:
        o = Order(id=new_id())
        o.place()
        await u.orders.save(o)
        try:
            await u.commit()
        except RuntimeError:
            pass
        n1 = len(store.entries)
        await u.commit()  # retry without rollback
    ids = [e.message_id for e in store.entries]
    print(f"CQRS-10 retry commit() without rollback: entries after failed commit={n1}, "
          f"after retry={len(store.entries)}, distinct message_ids={len(set(ids))}")
    # second successful commit in one UoW scope
    store2 = FakeOutboxStore()
    u2 = SpyUoW([], {}, outbox_writer=OutboxWriter(store2, registry()))
    async with u2:
        for _ in range(2):
            o = Order(id=new_id())
            o.place()
            await u2.orders.save(o)
            await u2.commit()
    print(f"CQRS-10 two commits in one scope, one event each: outbox entries={len(store2.entries)} "
          f"(expected 2)")


async def c11() -> None:
    seen: dict[str, Any] = {}

    class Peek:
        async def handle(self, ctx: MessageContext, next: Any) -> Any:
            r = await next()
            seen["new_events_after_next"] = list(ctx.new_events)
            return r

    async def h(cmd, uow):
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        return PlaceOrderResult(order_id=cmd.order_id)

    bus = CommandBus()
    bus.register(PlaceOrder, h, lambda: SpyUoW([], {}), behaviors=[Peek()])
    _, ev = await bus.dispatch(PlaceOrder(order_id=new_id()))
    print(f"CQRS-11 behavior sees ctx.new_events after next() = {seen['new_events_after_next']}; "
          f"dispatch returned {len(ev)} events")


class ProtoUoW:
    """Satisfies the UnitOfWork Protocol without the ABC."""

    def __init__(self) -> None:
        self.orders = BufferRepo()
        self._events: list[DomainEvent] = []

    async def __aenter__(self) -> "ProtoUoW":
        return self

    async def __aexit__(self, *a: Any) -> None:
        return None

    async def commit(self) -> None:
        self._events = self.orders.pull_events()

    async def rollback(self) -> None:
        self._events = []

    def collect_events(self) -> list[DomainEvent]:
        return self._events


class SlotUoW(ProtoUoW):
    __slots__ = ("orders", "_events")


async def c12() -> None:
    async def h(cmd, uow):
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        return PlaceOrderResult(order_id=cmd.order_id)

    u = ProtoUoW()
    print("CQRS-12 isinstance(ProtoUoW(), UnitOfWork) =", isinstance(u, UnitOfWork))
    bus = CommandBus()
    bus.register(PlaceOrder, h, lambda: u)
    cmd = PlaceOrder(order_id=new_id())
    _, ev = await bus.dispatch(cmd)
    print(f"CQRS-12 protocol-only UoW: events={len(ev)} correlation={ev[0].correlation_id} "
          f"causation={ev[0].causation_id}; bus set private attr: {getattr(u, '_causation_id', None) == cmd.command_id}")


async def c13_a16() -> None:
    async def conflict(cmd, uow):
        raise ConcurrencyError("synthetic version conflict")

    bus = CommandBus()
    bus.register(PlaceOrder, conflict, lambda: SpyUoW([], {}))
    try:
        await bus.dispatch(PlaceOrder(order_id=new_id()))
    except ConcurrencyError:
        print("CQRS-13 caught ConcurrencyError directly")
    except CommandExecutionError as e:
        print(f"CQRS-13 caller gets CommandExecutionError; __cause__={type(e.__cause__).__name__}; "
              f"isinstance(e, DomainError)={isinstance(e, DomainError)}")

    # A16: the exact shape of handle-domain-errors.md:101-111, HTTPException stubbed
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str) -> None:
            super().__init__(status_code, detail)

    async def place_order() -> Any:
        try:
            result = await bus.dispatch(PlaceOrder(order_id=new_id()))
            return result
        except ConcurrencyError:
            raise HTTPException(status_code=409, detail="Conflict")
        except DomainError as e:
            raise HTTPException(status_code=422, detail=str(e))

    try:
        await place_order()
    except HTTPException as h:
        print(f"A16 doc snippet on a version conflict -> HTTP {h.args[0]} detail={h.args[1]!r}")

    async def ok(cmd, uow):
        return PlaceOrderResult(order_id=cmd.order_id)

    bus2 = CommandBus()
    bus2.register(PlaceOrder, ok, lambda: SpyUoW([], {}))
    r = await bus2.dispatch(PlaceOrder(order_id=new_id()))
    print(f"A16 doc snippet success path returns {type(r).__name__} of "
          f"({type(r[0]).__name__}, {type(r[1]).__name__})")


async def c14() -> None:
    store = FakeOutboxStore()
    u = SpyUoW([], {}, outbox_writer=OutboxWriter(store, registry()))
    captured: list[DomainEvent] = []

    async def h(cmd, uow):
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        return PlaceOrderResult(order_id=cmd.order_id)

    bus = CommandBus()
    bus.register(PlaceOrder, h, lambda: u)
    cmd = PlaceOrder(order_id=new_id())
    _, ev = await bus.dispatch(cmd)
    e = store.entries[0]
    print(f"CQRS-14 OutboxEntry.causation_id == command_id: {e.causation_id == str(cmd.command_id)}; "
          f"== domain event_id: {e.causation_id == str(ev[0].event_id)}; "
          f"payload causation_id={e.payload.get('causation_id')}")
    stamped = OrderPlacedV1(order_id="x").stamp_from(ev[0])
    print(f"CQRS-14 ADR-061 stamp_from causation == event_id: {stamped.causation_id == str(ev[0].event_id)}")


async def main() -> None:
    await c10()
    await c11()
    await c12()
    await c13_a16()
    await c14()


asyncio.run(main())
