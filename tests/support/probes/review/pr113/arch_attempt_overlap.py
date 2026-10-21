"""Probe: how far attempts can exceed max_attempts when k relays overlap on one entry through stale claims."""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore

now = [datetime(2026, 1, 1, tzinfo=UTC)]
clock = lambda: now[0]

class D(DomainEvent): pass
class I(IntegrationEvent): pass

class Gate:
    """Broker whose publish blocks until released, then fails."""
    def __init__(self): self.release = asyncio.Event()
    async def publish(self, *a, **k):
        await self.release.wait(); raise ConnectionError()

async def run(k, max_attempts=3):
    reg = OutboundEventRegistry(); reg.register_translation(D, I, "t", lambda e: I())
    s = FakeOutboxStore(clock=clock, claim_timeout=timedelta(seconds=1)); mid = str(uuid4())
    await s.append([OutboxEntry(message_id=mid, topic="t", payload=I().model_dump(), occurred_at=clock())])
    ok = OutboundEventGateway(s, type("F", (), {"publish": lambda self, *a, **kw: (_ for _ in ()).throw(ConnectionError())})(), reg,
                              clock=clock, max_attempts=max_attempts, base_delay=timedelta(0), max_delay=timedelta(0))
    # bring attempts to max-1 with a single relay (async publish needed)
    class F:
        async def publish(self, *a, **kw): raise ConnectionError()
    single = OutboundEventGateway(s, F(), reg, clock=clock, max_attempts=max_attempts, base_delay=timedelta(0), max_delay=timedelta(0))
    for _ in range(max_attempts - 1): await single.run_once()
    gate = Gate(); tasks = []
    for _ in range(k):
        g = OutboundEventGateway(s, gate, reg, clock=clock, max_attempts=max_attempts, base_delay=timedelta(0), max_delay=timedelta(0))
        tasks.append(asyncio.create_task(g.run_once())); await asyncio.sleep(0)
        now[0] += timedelta(seconds=2)  # claim goes stale before the next relay fetches
    gate.release.set(); await asyncio.gather(*tasks)
    print(f"{k} overlapping relays, max_attempts={max_attempts}: attempts={s.attempts[mid]}, dead={mid in s.dead_lettered}")

async def main():
    for k in (1, 2, 3, 4): await run(k)

asyncio.run(main())
