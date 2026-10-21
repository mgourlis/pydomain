"""FAKE-05b: two OutboundEventGateway relays over one FakeOutboxStore; batch_size=0. Synthetic."""
import asyncio
from datetime import UTC, datetime
from uuid import UUID
from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker

class ThingIE(IntegrationEvent):
    n: int
class Thing(DomainEvent):
    n: int

def reg():
    r = OutboundEventRegistry()
    r.register_translation(Thing, ThingIE, "t.synthetic", lambda e: ThingIE(n=e.n))
    return r

def entry(i):
    return OutboxEntry(message_id=f"m{i}", topic="t.synthetic", payload=ThingIE(n=i).model_dump(mode="json"), occurred_at=datetime.now(UTC))

class SlowBroker(InMemoryMessageBroker):
    async def publish(self, topic, event, **kw):
        await asyncio.sleep(0)  # yield, like a network call
        await super().publish(topic, event, **kw)

async def main():
    store = FakeOutboxStore(); broker = SlowBroker()
    await store.append([entry(1), entry(2)])
    g1 = OutboundEventGateway(store, broker, reg()); g2 = OutboundEventGateway(store, broker, reg())
    r = await asyncio.gather(g1.run_once(), g2.run_once())
    print("two relays run_once ->", r, "broker publishes:", len(broker.published), "published marks:", store.published)
    store2 = FakeOutboxStore(); b2 = InMemoryMessageBroker()
    await store2.append([entry(i) for i in range(5)])
    g0 = OutboundEventGateway(store2, b2, reg(), batch_size=0)
    print("batch_size=0 run_once ->", await g0.run_once(), "published:", len(b2.published))
asyncio.run(main())
