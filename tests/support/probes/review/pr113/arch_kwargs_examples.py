"""Probe: the docs' TracingPublisherMiddleware and RabbitMQBroker examples accept and forward the relay's headers.

usage: python arch_kwargs_examples.py <worktree>
"""
import asyncio, re, sys, types
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))

def block(page, cls):
    text = (root / page).read_text()
    for b in re.findall(r"```python\n(.*?)```", text, re.S):
        if f"class {cls}" in b:
            return b
    raise SystemExit(f"{cls} not found in {page}")

# fake aio_pika
sent = []
aio = types.ModuleType("aio_pika")
class ExchangeType: TOPIC = "topic"
class Message:
    def __init__(self, body, content_type, headers=None): self.body, self.headers = body, headers
class Exchange:
    async def publish(self, msg, routing_key): sent.append((routing_key, msg.headers))
class Channel:
    async def declare_exchange(self, name, kind): return Exchange()
aio.ExchangeType, aio.Message = ExchangeType, Message
sys.modules["aio_pika"] = aio

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore

class Shipped(IntegrationEvent):
    ref: str
class D(DomainEvent):
    pass
reg = OutboundEventRegistry(); reg.register_translation(D, Shipped, "t", lambda e: Shipped(ref="x"))

async def drive(page, broker_factory):
    store = FakeOutboxStore()
    c, k = str(uuid4()), str(uuid4())
    ev = Shipped(ref="r")
    await store.append([
        OutboxEntry(message_id=ev.event_id, topic="t", payload=ev.model_dump(), occurred_at=datetime.now(UTC), correlation_id=c, causation_id=k),
        OutboxEntry(message_id=str(uuid4()), topic="t", payload=Shipped(ref="u").model_dump(), occurred_at=datetime.now(UTC)),
    ])
    n = await OutboundEventGateway(store, broker_factory(), reg).run_once()
    print(page, "published", n, "failures", [f[1] for f in store.failures])
    return c, k

for page in ("docs/diataxis/how-to/infrastructure/configure-message-broker.md",):
    ns = {"aio_pika": aio}  # the page's first block uses aio_pika without importing it
    exec(block(page, "RabbitMQBroker"), ns)
    def make():
        b = ns["RabbitMQBroker"].__new__(ns["RabbitMQBroker"]); b._channel = Channel(); return b
    sent.clear(); c, k = asyncio.run(drive(page, make))
    print("  rabbit headers:", sent, "ok" if sent[0][1] == {"correlation_id": c, "causation_id": k} and sent[1][1] is None else "WRONG")

class Inner:
    def __init__(self): self.calls = []
    async def publish(self, topic, event, **kw): self.calls.append(kw)

for page in ("docs/diataxis/concepts/infrastructure/tracing.md", "docs/diataxis/how-to/infrastructure/configure-tracing.md"):
    ns = {}
    exec(block(page, "TracingPublisherMiddleware"), ns)
    inner = Inner()
    c, k = asyncio.run(drive(page, lambda: ns["TracingPublisherMiddleware"](inner)))
    print("  middleware forwarded:", inner.calls, "ok" if inner.calls[0] == {"headers": {"correlation_id": c, "causation_id": k}} and inner.calls[1] == {} else "WRONG")
    # extra kwargs pass through, caller's dict not mutated
    h = {"a": "1"}; inner.calls.clear()
    asyncio.run(ns["TracingPublisherMiddleware"](inner).publish("t", Shipped(ref="z"), headers=h, priority=5))
    print("  extra kwargs:", inner.calls, "caller dict intact:", h == {"a": "1"})
