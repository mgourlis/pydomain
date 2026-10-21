"""Probe: a stored payload nested deeper than the recursion limit poisons the relay.

The new recursive strict-JSON check raises RecursionError (not ValidationError)
inside model_validate, so _deliver does not catch it: the pass ends, no attempt is
counted, the entry is never dead-lettered, and the entries behind it in the batch wait.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore


class D(DomainEvent):
    pass


class I(IntegrationEvent):
    data: Any = None


class Clock:
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self):
        return self.now


class Broker:
    accepted: list[str] = []

    async def publish(self, topic, event, **kw):
        self.accepted.append(event.event_id)

    async def start(self): ...
    async def stop(self): ...


async def main():
    reg = OutboundEventRegistry()
    reg.register_translation(D, I, "t", lambda e: None)
    clock = Clock()
    store = FakeOutboxStore(clock=clock)
    deep: Any = 1
    for _ in range(3000):
        deep = [deep]
    poison = I().model_dump()
    poison["data"] = deep
    ok = I().model_dump()
    await store.append([
        OutboxEntry(message_id="poison", topic="t", payload=poison, occurred_at=clock.now),
        OutboxEntry(message_id="behind", topic="t", payload=ok | {"event_id": "behind"}, occurred_at=clock.now),
    ])
    broker = Broker()
    gw = OutboundEventGateway(store, broker, reg, clock=clock, max_attempts=2)
    for i in range(5):
        try:
            n = await gw.run_once()
            print("pass", i, "published", n)
        except Exception as exc:  # noqa: BLE001
            print("pass", i, "raised", type(exc).__name__)
        clock.now += timedelta(minutes=6)
    print("attempts", store.attempts, "dead", store.dead_lettered.keys(), "published", store.published)

asyncio.run(main())
