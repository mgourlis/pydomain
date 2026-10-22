"""Probes for CQRS-05..09 (synthetic order data); CQRS-04 fixed by DCE-146."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from common import *  # noqa: F403
from pydomain.cqrs.behaviors import (
    EventIdempotencyBehavior,
    IdempotencyBehavior,
)
from pydomain.cqrs.commands import Command, EmptyCommandResult
from uuid import UUID
from pydomain.cqrs.exceptions import CommandExecutionError
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.testing import FakeProcessedMessageStore


class AddTwo(Command[EmptyCommandResult]):  # type: ignore[misc]
    order_id: UUID


class Follow(Command[EmptyCommandResult]):  # type: ignore[misc]
    order_id: UUID


# ---------------------------------------------------------------- CQRS-05
async def c05() -> None:
    # (a) one command raises two events of the same type
    store = FakeProcessedMessageStore()
    seen: list[str] = []

    async def h_two(cmd, uow):
        o = Order(id=cmd.order_id)
        o.add_item("SKU-A")
        o.add_item("SKU-B")
        await uow.orders.save(o)
        return EmptyCommandResult()

    async def on_item(ev):
        seen.append(ev.sku)

    mb = MessageBus()
    mb.register_command(AddTwo, h_two, lambda: SpyUoW([], {}))
    mb.register_event(ItemAdded, on_item, behaviors=[EventIdempotencyBehavior(store)])
    cmd = AddTwo(order_id=new_id())
    await mb.dispatch(cmd)
    print(f"CQRS-05a two ItemAdded from one command, guarded handler saw={seen} (expected 2)")

    # (b) two handlers of one event type sharing one store
    store2 = FakeProcessedMessageStore()
    hits: list[str] = []

    async def h_place(cmd, uow):
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        return PlaceOrderResult(order_id=cmd.order_id)

    async def proj(ev):
        hits.append("projection")

    async def mailer(ev):
        hits.append("mailer")

    mb2 = MessageBus()
    mb2.register_command(PlaceOrder, h_place, lambda: SpyUoW([], {}))
    mb2.register_event(OrderPlaced, proj, behaviors=[EventIdempotencyBehavior(store2)])
    mb2.register_event(OrderPlaced, mailer, behaviors=[EventIdempotencyBehavior(store2)])
    await mb2.dispatch(PlaceOrder(order_id=new_id()))
    print(f"CQRS-05b two handlers, shared store: ran={hits} (expected both)")

    # (c) store shared with IdempotencyBehavior
    store3 = FakeProcessedMessageStore()
    hits3: list[str] = []
    captured = {}

    async def proj3(ev):
        captured["causation"] = ev.causation_id
        hits3.append("projection")

    mb3 = MessageBus()
    mb3.register_command(PlaceOrder, h_place, lambda: SpyUoW([], {}),
                         behaviors=[IdempotencyBehavior(store3)])
    mb3.register_event(OrderPlaced, proj3, behaviors=[EventIdempotencyBehavior(store3)])
    c3 = PlaceOrder(order_id=new_id())
    await mb3.dispatch(c3)
    print(f"CQRS-05c store shared with IdempotencyBehavior: event handler ran={hits3} (expected 1)")

    # docstring claim: in-process events have no causation_id
    store4 = FakeProcessedMessageStore()
    mb4 = MessageBus()
    mb4.register_command(PlaceOrder, h_place, lambda: SpyUoW([], {}))
    mb4.register_event(OrderPlaced, proj3, behaviors=[EventIdempotencyBehavior(store4)])
    c4 = PlaceOrder(order_id=new_id())
    await mb4.dispatch(c4)
    print(f"CQRS-05 docstring: in-process event causation_id={captured['causation']} "
          f"== command_id: {captured['causation'] == c4.command_id}; store4 keys={len(store4._store)}")


# ---------------------------------------------------------------- CQRS-06
async def c06() -> None:
    async def failing(cmd, uow):
        raise ValueError("synthetic handler failure")

    async def ok(cmd, uow):
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        return PlaceOrderResult(order_id=cmd.order_id)

    for label, handler, kw in [
        ("handler-fails", failing, {}),
        ("commit-fails", ok, {"fail_commit": 1}),
        ("handler-fails, 2nd rollback raises", failing, {"rollback_raises_after": 1}),
        ("handler-fails, 1st rollback raises", failing, {"rollback_raises_after": 0}),
    ]:
        log: list[str] = []
        u = SpyUoW(log, {}, **kw)
        bus = CommandBus()
        bus.register(PlaceOrder, handler, lambda u=u: u)
        try:
            await bus.dispatch(PlaceOrder(order_id=new_id()))
        except BaseException as e:  # noqa: BLE001
            ctx = e.__context__
            print(f"CQRS-06 {label}: rollbacks={u.rollbacks} log={log} caller sees "
                  f"{type(e).__name__}({e}) context={type(ctx).__name__ if ctx else None}")


# ---------------------------------------------------------------- CQRS-07
class CommitThenAwait(SpyUoW):
    """_commit writes the 'database' first, then awaits (e.g. connection release)."""

    async def _commit(self) -> None:
        for agg in self.orders.staged:
            self.db[str(agg.id)] = agg
        self.orders.staged.clear()
        self.log.append("db-committed")
        await asyncio.sleep(0.05)
        self.log.append("commit-returned")


async def c07() -> None:
    for label, cls, kw in [("cancel during awaited _commit (before db write)", SpyUoW, {"commit_delay": 0.05}),
                           ("cancel after db write, inside _commit", CommitThenAwait, {})]:
        log: list[str] = []
        db: dict = {}
        u = cls(log, db, **kw)
        handled: list[object] = []

        async def ok(cmd, uow):
            o = Order(id=cmd.order_id)
            o.place()
            await uow.orders.save(o)
            return PlaceOrderResult(order_id=cmd.order_id)

        async def on_placed(ev):
            handled.append(ev)

        mb = MessageBus()
        mb.register_command(PlaceOrder, ok, lambda u=u: u)
        mb.register_event(OrderPlaced, on_placed)
        cmd = PlaceOrder(order_id=new_id())
        t = asyncio.create_task(mb.dispatch(cmd))
        await asyncio.sleep(0.02)
        t.cancel()
        try:
            await t
        except BaseException as e:  # noqa: BLE001
            outcome = type(e).__name__
        print(f"CQRS-07 {label}: caller={outcome} log={log} rollbacks={u.rollbacks} "
              f"committed_flag={u._committed} row_in_db={str(cmd.order_id) in db} "
              f"uow._events={len(u._events)} event_handlers_ran={len(handled)}")

    # rollback with an await inside, double cancel: is __aexit__'s rollback shielded?
    class SlowRollback(SpyUoW):
        async def rollback(self) -> None:
            self.log.append("rollback-start")
            await asyncio.sleep(0.05)
            self.log.append("rollback-end")
            await super().rollback()

    log = []
    u = SlowRollback(log, {}, commit_delay=0.05)

    async def ok2(cmd, uow):
        return PlaceOrderResult(order_id=cmd.order_id)

    bus = CommandBus()
    bus.register(PlaceOrder, ok2, lambda: u)
    t = asyncio.create_task(bus.dispatch(PlaceOrder(order_id=new_id())))
    await asyncio.sleep(0.02)
    t.cancel()
    await asyncio.sleep(0.04)  # now inside rollback
    t.cancel()
    try:
        await t
    except BaseException as e:  # noqa: BLE001
        print(f"CQRS-07 second cancel during awaited rollback: caller={type(e).__name__} log={log}")


# ---------------------------------------------------------------- CQRS-08
async def c08() -> None:
    repo = BufferRepo()
    u = SpyUoW([], {}, repo=repo)
    handled: list[str] = []

    async def h_two(cmd, uow):
        o = Order(id=cmd.order_id)
        o.add_item("SKU-1")
        o.add_item("SKU-2")
        o.add_item("SKU-3")
        await uow.orders.save(o)
        return EmptyCommandResult()

    async def h_follow(cmd, uow):
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        return EmptyCommandResult()

    mb = MessageBus()

    async def on_item(ev):
        handled.append(f"ItemAdded:{ev.sku}")
        if ev.sku == "SKU-1":
            await mb.dispatch(Follow(order_id=ev.order_id))  # nested dispatch

    async def on_placed(ev):
        handled.append("OrderPlaced")

    mb.register_command(AddTwo, h_two, lambda: u)       # shared instance, as docs teach
    mb.register_command(Follow, h_follow, lambda: u)
    mb.register_event(ItemAdded, on_item)
    mb.register_event(OrderPlaced, on_placed)
    await mb.dispatch(AddTwo(order_id=new_id()))
    print(f"CQRS-08 shared factory + nested dispatch: handled={handled} "
          f"(expected 3 ItemAdded + 1 OrderPlaced)")

    # fresh factory control
    handled.clear()
    mb2 = MessageBus()

    async def on_item2(ev):
        handled.append(f"ItemAdded:{ev.sku}")
        if ev.sku == "SKU-1":
            await mb2.dispatch(Follow(order_id=ev.order_id))

    mb2.register_command(AddTwo, h_two, lambda: SpyUoW([], {}))
    mb2.register_command(Follow, h_follow, lambda: SpyUoW([], {}))
    mb2.register_event(ItemAdded, on_item2)
    mb2.register_event(OrderPlaced, on_placed)
    await mb2.dispatch(AddTwo(order_id=new_id()))
    print(f"CQRS-08 control fresh factory: handled={handled}")

    # aliasing: returned events list is the UoW's internal list
    u3 = SpyUoW([], {})
    bus = CommandBus()
    bus.register(AddTwo, h_two, lambda: u3)
    first = AddTwo(order_id=new_id())
    _, ev = await bus.dispatch(first)
    n = len(ev)
    await bus.dispatch(AddTwo(order_id=new_id()))
    print("CQRS-08 aliasing: first result's events still belong to first command:",
          all(e.order_id == first.order_id for e in ev))
    print(f"CQRS-08 aliasing: first dispatch returned {n} events; after a second dispatch on "
          f"the same UoW that list now has {len(ev)} items, is uow._events: {ev is u3._events}")


# ---------------------------------------------------------------- CQRS-09
async def c09() -> None:
    u = SpyUoW([], {})

    async def slow(cmd, uow):
        o = Order(id=cmd.order_id)
        o.place()
        await uow.orders.save(o)
        await asyncio.sleep(0.01 if cmd.order_id == a.order_id else 0.001)
        return PlaceOrderResult(order_id=cmd.order_id)

    bus = CommandBus()
    bus.register(PlaceOrder, slow, lambda: u)
    a = PlaceOrder(order_id=new_id())
    b = PlaceOrder(order_id=new_id())
    (ra, ea), (rb, eb) = await asyncio.gather(bus.dispatch(a), bus.dispatch(b))
    def desc(evs):
        return [("A" if e.order_id == a.order_id else "B",
                 "causedByA" if e.causation_id == a.command_id else "causedByB") for e in evs]
    print(f"CQRS-09 concurrent on shared UoW: A got events={desc(ea)}; B got events={desc(eb)}")


async def main() -> None:
    await c05()
    await c06()
    await c07()
    await c08()
    await c09()


asyncio.run(main())
