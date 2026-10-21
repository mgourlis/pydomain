import asyncio, logging
from typing import ClassVar
from common import LineItemAdded
from pydomain.es.projection import EventSourcedProjection
from pydomain.infrastructure.subscription import Subscription, SubscriptionRunner
from pydomain.testing.fake_event_store import FakeEventStore
from pydomain.testing.fake_checkpoint_store import FakeCheckpointStore
logging.disable(logging.CRITICAL)

class P(EventSourcedProjection):
    name: ClassVar[str] = "p"; version: ClassVar[int] = 1

class Poison(SubscriptionRunner):
    async def process_batch(self, events, sub): raise RuntimeError("poison")

class Counting(FakeEventStore):
    reads = 0
    async def read_all(self, from_version=0):
        Counting.reads += 1; return await super().read_all(from_version)

async def main():
    store = Counting(); await store.append_to_stream("p", [LineItemAdded(order_id="p", item_name="x", price=1)], 0)
    r = Poison(store, FakeCheckpointStore(), poll_interval_seconds=1.0, failure_backoff_seconds=0.1)
    r.add_subscription(Subscription("s", P(), (LineItemAdded,)))
    t = asyncio.create_task(r.run()); await asyncio.sleep(1.0); r.stop(); await t
    print("ES-19 run() with poll_interval=1.0s: read_all calls in 1s =", Counting.reads)
asyncio.run(main())
