"""FAKE-05 probe: FakeOutboxStore vs OutboxStore protocol. Synthetic data."""
import asyncio
from datetime import UTC, datetime, timedelta
from pydomain.cqrs.outbox import OutboxEntry, OutboxStore
from pydomain.testing import FakeOutboxStore

T0 = datetime(2020, 1, 1, tzinfo=UTC)
def e(mid, minutes):
    return OutboxEntry(message_id=mid, topic="t.synthetic", payload={"k": mid}, occurred_at=T0 + timedelta(minutes=minutes))

async def main():
    s = FakeOutboxStore()
    print("isinstance OutboxStore:", isinstance(s, OutboxStore))
    await s.append([e("m1", 10), e("m2", 5), e("m3", 0)])  # inserted newest-occurred first
    a = await s.fetch_unpublished(2)
    b = await s.fetch_unpublished(2)
    print("1 two concurrent-ish fetches (sequential):", [x.message_id for x in a], [x.message_id for x in b])
    ra, rb = await asyncio.gather(s.fetch_unpublished(10), s.fetch_unpublished(10))
    print("1b gather two relays:", [x.message_id for x in ra], [x.message_id for x in rb])
    print("2 limit=0 ->", [x.message_id for x in await s.fetch_unpublished(0)])
    print("2 limit=-1 ->", [x.message_id for x in await s.fetch_unpublished(-1)])
    print("3 order (occurred_at m1=+10,m2=+5,m3=+0):", [x.message_id for x in await s.fetch_unpublished(10)])
    await s.append([e("m1", 99)])
    print("4 duplicate message_id accepted; entries:", len(s.entries), [x.message_id for x in s.entries])
    print("4 fetch after dup:", [(x.message_id, x.occurred_at.minute) for x in await s.fetch_unpublished(10)])
    await s.mark_published(["ghost"])
    print("5 mark unknown published -> no error; is_published(ghost)=", s.is_published("ghost"), "published=", s.published)
    await s.mark_published(["m3"]); await s.mark_published(["m3"])
    print("5 double publish published list:", s.published)
    await s.mark_failed("m3", error="x", next_attempt_at=T0)
    await s.mark_dead_lettered("m3", error="y")
    print("5 failed+dead-lettered a published id: is_published=", s.is_published("m3"), "is_dead_lettered=", s.is_dead_lettered("m3"), "attempts=", s.attempts.get("m3"))
    await s.mark_failed("ghost2", error="x", next_attempt_at=T0)
    print("5 mark_failed unknown -> attempts", s.attempts.get("ghost2"))
    # dead-letter then publish
    await s.mark_dead_lettered("m2", error="z"); await s.mark_published(["m2"])
    print("5 dead-lettered then published: both flags", s.is_dead_lettered("m2"), s.is_published("m2"))
    # crash-recovery: claimed entries never become stale since never claimed -> fine
    # Gateway with batch_size=0
    from pydomain.infrastructure.message_broker import OutboundEventGateway
    import inspect
    print("gateway batch_size validation in __init__:", "batch_size" in inspect.getsource(OutboundEventGateway.__init__).split("self._batch_size")[0].split('"""')[-1])

asyncio.run(main())
