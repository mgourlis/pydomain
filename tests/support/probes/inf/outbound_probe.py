"""INF-09..INF-13 and A4 probes: real OutboundEventGateway with shipped fakes."""

import asyncio
import io
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry, OutboxWriter
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker

log_buf = io.StringIO()
h = logging.StreamHandler(log_buf)
h.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
root = logging.getLogger()
root.addHandler(h)
root.setLevel(logging.DEBUG)

SENTINEL = "SENTINEL-XYZ"


class OrderPlacedIE(IntegrationEvent):
    order_id: str
    total: int


class OrderPlaced(DomainEvent):
    order_id: UUID
    total: int


def registry() -> OutboundEventRegistry:
    r = OutboundEventRegistry()
    r.register_translation(
        OrderPlaced, OrderPlacedIE, "orders.placed",
        lambda e: OrderPlacedIE(order_id=str(e.order_id), total=e.total),  # type: ignore[attr-defined]
    )
    return r


def entry(n: int, payload=None, **kw) -> OutboxEntry:
    return OutboxEntry(
        message_id=f"m{n}", topic="orders.placed",
        payload=payload or {"order_id": f"o{n}", "total": n},
        occurred_at=datetime.now(UTC), **kw,
    )


async def inf09() -> None:
    print("--- INF-09 ---")

    class MarkFailedRaises(FakeOutboxStore):
        armed = True

        async def mark_failed(self, message_id, *, error, next_attempt_at):
            if self.armed:
                self.armed = False
                raise ConnectionError("outbox DB blip")
            await super().mark_failed(message_id, error=error, next_attempt_at=next_attempt_at)

    class FlakyBroker(InMemoryMessageBroker):
        async def publish(self, topic, event, **kw):
            if event.order_id == "o2":
                raise ConnectionError("nack")
            await super().publish(topic, event, **kw)

    store = MarkFailedRaises()
    broker = FlakyBroker()
    await store.append([entry(1), entry(2), entry(3)])
    gw = OutboundEventGateway(store, broker, registry())
    try:
        await gw.run_once()
    except ConnectionError:
        pass
    await gw.run_once()
    ids = [e.order_id for _, e, _ in broker.published]
    print("INF-09a mark_failed raise mid-batch: publishes =", ids)

    # Cancellation mid-batch
    class HangOn3(InMemoryMessageBroker):
        async def publish(self, topic, event, **kw):
            if event.order_id == "o3":
                await asyncio.Event().wait()
            await super().publish(topic, event, **kw)

    store2 = FakeOutboxStore()
    broker2 = HangOn3()
    await store2.append([entry(1), entry(2), entry(3)])
    gw2 = OutboundEventGateway(store2, broker2, registry())
    t = asyncio.create_task(gw2.run_once())
    await asyncio.sleep(0.05)
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        pass
    print("INF-09b cancel mid-batch: published before cancel =", [e.order_id for _, e, _ in broker2.published],
          "| marked published =", store2.published)


async def inf10() -> None:
    print("--- INF-10 ---")
    store = FakeOutboxStore()
    await store.append([entry(1, payload={"order_id": "o1", "total": SENTINEL})])
    gw = OutboundEventGateway(store, InMemoryMessageBroker(), registry(), max_attempts=2)
    log_buf.truncate(0); log_buf.seek(0)
    await gw.run_once()
    print("INF-10 mark_failed error contains sentinel:", SENTINEL in store.failures[0][1])
    print("INF-10   stored error excerpt:", store.failures[0][1].splitlines()[2].strip()[:90])
    # Second attempt exhausts -> dead-letter ERROR log
    store._retry_at.clear()
    await gw.run_once()
    print("INF-10 dead_lettered error contains sentinel:", SENTINEL in store.dead_lettered["m1"])
    logs = log_buf.getvalue()
    lines = [ln for ln in logs.splitlines() if SENTINEL in ln]
    print("INF-10 log lines containing sentinel:", len(lines), "| first:", lines[0][:110] if lines else None)

    # Broker exception text
    class LeakyBroker(InMemoryMessageBroker):
        async def publish(self, topic, event, **kw):
            raise RuntimeError(f"rejected message body {event.model_dump()}")

    store3 = FakeOutboxStore()
    await store3.append([entry(3, payload={"order_id": SENTINEL, "total": 3})])
    log_buf.truncate(0); log_buf.seek(0)
    await OutboundEventGateway(store3, LeakyBroker(), registry()).run_once()
    print("INF-10 broker-exc path: stored error has sentinel:", SENTINEL in store3.failures[0][1],
          "| logs have sentinel:", SENTINEL in log_buf.getvalue())

    # Inbound path (message_subscriber.py:231 logger.exception on ValidationError)
    from pydomain.infrastructure.message_bus import MessageBus
    from pydomain.infrastructure.message_subscriber import InboundEventGateway
    from pydomain.testing import InMemoryMessageSubscriber

    sub = InMemoryMessageSubscriber()
    igw = InboundEventGateway(sub, MessageBus())
    igw.register_translation("orders.placed", OrderPlacedIE, lambda ie: OrderPlaced(order_id=uuid4(), total=1))
    log_buf.truncate(0); log_buf.seek(0)
    await sub.simulate_message("orders.placed", {"order_id": "o1", "total": SENTINEL})
    print("INF-10 inbound validation failure: logs have sentinel:", SENTINEL in log_buf.getvalue())

    # Saga command hydration
    from pydomain.cqrs.commands import Command
    from pydomain.cqrs.saga.hydration import hydrate_command
    import sys, types
    mod = types.ModuleType("probe_cmds")

    class ChargeCard(Command):  # type: ignore[type-arg]
        amount: int

    mod.ChargeCard = ChargeCard
    sys.modules["probe_cmds"] = mod
    log_buf.truncate(0); log_buf.seek(0)
    r = hydrate_command("probe_cmds", "ChargeCard", {"amount": SENTINEL})
    print("INF-10 saga hydrate_command: result =", r, "| logs have sentinel:", SENTINEL in log_buf.getvalue())

    # Upcaster
    from pydomain.es.upcasting import EventUpcaster

    class Up(EventUpcaster):
        source_type = "OrderPlaced"; source_version = 1; target_version = 2

        def _transform(self, event):
            return {**event, "total": int(event["total"])}

    try:
        Up().upcast({"total": SENTINEL})
    except Exception as exc:  # noqa: BLE001
        print("INF-10 UpcastError message has sentinel:", SENTINEL in str(exc))


async def inf11() -> None:
    print("--- INF-11 ---")
    store = FakeOutboxStore()
    gw = OutboundEventGateway(store, InMemoryMessageBroker(), registry(), poll_interval_seconds=0.01)
    await gw.start()
    await gw.stop()  # before the task ever ran
    await asyncio.sleep(0.05)
    print("INF-11a stop() right after start(): task still running =", not gw._task.done())
    await store.append([entry(1)])
    await asyncio.sleep(0.05)
    print("INF-11a   ...and it keeps publishing after stop(): published =", store.published)
    gw._stop_requested = True; await asyncio.sleep(0.05)  # cleanup

    # Dead task: cancel the relay task externally -> start() is a no-op forever
    gw2 = OutboundEventGateway(FakeOutboxStore(), InMemoryMessageBroker(), registry(), poll_interval_seconds=0.01)
    await gw2.start()
    await asyncio.sleep(0.02)
    gw2._task.cancel()
    await asyncio.sleep(0.02)
    await gw2.start()
    print("INF-11b after task died: task done =", gw2._task.done(), "| start() restarted it:", not gw2._task.done())

    # Hung publish blocks stop()
    class Hang(InMemoryMessageBroker):
        async def publish(self, topic, event, **kw):
            await asyncio.Event().wait()

    s3 = FakeOutboxStore(); await s3.append([entry(1)])
    gw3 = OutboundEventGateway(s3, Hang(), registry(), poll_interval_seconds=0.01)
    await gw3.start(); await asyncio.sleep(0.02)
    try:
        await asyncio.wait_for(gw3.stop(), 0.2)
        print("INF-11c hung publish: stop returned")
    except TimeoutError:
        print("INF-11c hung publish: stop() still blocked after 0.2s")
    gw3._task.cancel()


async def inf12() -> None:
    print("--- INF-12 ---")
    for ma in (0, -5):
        class Fail(InMemoryMessageBroker):
            async def publish(self, topic, event, **kw):
                raise ConnectionError("down")

        s = FakeOutboxStore(); await s.append([entry(1)])
        gw = OutboundEventGateway(s, Fail(), registry(), max_attempts=ma)
        await gw.run_once()
        print(f"INF-12 max_attempts={ma} accepted; after ONE failure dead-lettered =", s.is_dead_lettered("m1"))
    s = FakeOutboxStore(); await s.append([entry(1)])
    n = await OutboundEventGateway(s, InMemoryMessageBroker(), registry(), batch_size=0).run_once()
    print("INF-12 batch_size=0 accepted; published per pass =", n, "(fake treats limit 0 as unlimited? ",
          len(s.published), ")")


async def inf13_a4() -> None:
    print("--- INF-13 / A4 ---")
    corr, cmd_id = uuid4(), uuid4()
    ev = OrderPlaced(order_id=uuid4(), total=5).stamp(correlation_id=corr, causation_id=cmd_id)
    store = FakeOutboxStore()
    await OutboxWriter(store, registry()).write([ev])
    e = store.entries[0]
    print("A4 OutboxEntry.causation_id == command id:", e.causation_id == str(cmd_id),
          "| == domain event id:", e.causation_id == str(ev.event_id))
    print("INF-13 payload tracing:", e.payload["correlation_id"], e.payload["causation_id"])
    broker = InMemoryMessageBroker()
    await OutboundEventGateway(store, broker, registry()).run_once()
    pub = broker.published[0][1]
    print("INF-13 entry.correlation_id =", e.correlation_id is not None,
          "| published event correlation_id =", pub.correlation_id, "causation_id =", pub.causation_id,
          "| headers =", broker.published[0][2])


async def main():
    await inf09()
    await inf10()
    await inf11()
    await inf12()
    await inf13_a4()


asyncio.run(main())
