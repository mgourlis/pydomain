"""Probes: relay argument checks with bool, float and NaN values; fake limit as a float."""
import asyncio
import math
from datetime import UTC, datetime

from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker

for kw in [dict(batch_size=True), dict(batch_size=1.5), dict(max_attempts=True), dict(poll_interval_seconds=math.nan),
           dict(failure_backoff_seconds=math.nan), dict(poll_interval_seconds=math.inf)]:
    try:
        OutboundEventGateway(FakeOutboxStore(), InMemoryMessageBroker(), OutboundEventRegistry(), **kw)
        print(kw, "accepted")
    except ValueError as exc:
        print(kw, "ValueError:", exc)


async def main():
    s = FakeOutboxStore()
    await s.append([OutboxEntry(message_id=f"m{i}", topic="t", payload={}, occurred_at=datetime.now(UTC)) for i in range(5)])
    print("fetch limit=1.5 returns", len(await s.fetch_unpublished(1.5)))  # type: ignore[arg-type]
    try:
        await asyncio.wait_for(asyncio.sleep(math.nan), 0.1)
        print("sleep(nan) returned")
    except Exception as exc:  # noqa: BLE001
        print("sleep(nan):", type(exc).__name__)

asyncio.run(main())
