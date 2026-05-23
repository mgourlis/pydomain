# How to Use a Fake Event Store

> **Prerequisites:** [Event Store concept](../../concepts/es/event-store.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test event-sourced aggregates and projections without connecting to a real event store. Tests must simulate append-only event streams, optimistic concurrency, stream reads, and command idempotency.

## Solution

Use `FakeEventStore` from `pydomain.testing` — an in-memory implementation of the `EventStore` protocol with command-idempotency deduplication and a global event log.

## Steps

### 1. Import FakeEventStore

```python
from pydomain.testing import FakeEventStore
```

### 2. Create a fake event store

```python
store = FakeEventStore()
```

### 3. Append events to a stream

```python
from uuid import UUID, uuid4

from pydomain.es.event_stream import EventStream


event = OrderPlaced(order_id=UUID("..."), customer_id="c1")
stream = EventStream(
    aggregate_id="order-1",
    events=[event],
    version=0,
)

await store.append_to_stream(
    aggregate_id="order-1",
    events=stream.events,
    expected_version=0,  # first write expects version 0
    command_id=uuid4(),
)
```

If `expected_version` doesn't match the current stream version, `append_to_stream` raises an error — enforcing optimistic concurrency.

### 4. Read a stream

```python
stream = await store.read_stream(aggregate_id="order-1")
print(f"Version: {stream.version}")
for event in stream.events:
    print(event.event_type)
```

### 5. Read with a starting version

```python
# Read only events from version 3 onward
stream = await store.read_stream(
    aggregate_id="order-1",
    from_version=3,
)
```

### 6. Read all events globally (for catch-up subscriptions)

```python
all_events = await store.read_all(from_version=0)
print(f"Total events: {len(all_events.events)}")
```

### 7. Test command idempotency

Replaying the same `command_id` is a no-op — the result is returned without appending:

```python
cmd_id = uuid4()

# First append
await store.append_to_stream(
    aggregate_id="order-1",
    events=[OrderPlaced(...)],
    expected_version=0,
    command_id=cmd_id,
)

# Replay with same command_id
await store.append_to_stream(
    aggregate_id="order-1",
    events=[OrderPlaced(...)],  # different event, same command_id
    expected_version=1,
    command_id=cmd_id,
)

# Only one event was actually stored
stream = await store.read_stream("order-1")
assert len(stream.events) == 1
```

## Complete Example

```python
import pytest
from uuid import UUID, uuid4

from pydomain.testing import FakeEventStore
from pydomain.es.event_sourced_repository import EventSourcedRepository


class TestEventSourcedAggregate:
    @pytest.fixture
    def store(self) -> FakeEventStore:
        return FakeEventStore()

    @pytest.fixture
    def repo(self, store) -> EventSourcedRepository[Order, UUID]:
        return EventSourcedRepository(
            event_store=store,
            aggregate_cls=Order,
        )

    async def test_save_and_load(self, store, repo):
        order = Order.create(customer_id="c1", items=[])

        await repo.save(order)

        loaded = await repo.get_by_id(order.id)
        assert loaded is not None
        assert loaded.customer_id == "c1"
        assert loaded.version == 1  # one event applied

    async def test_append_multiple_events(self, store):
        order = Order.create(customer_id="c1", items=[])

        await store.append_to_stream(
            aggregate_id=str(order.id),
            events=[
                OrderPlaced(order_id=order.id, customer_id="c1"),
                ItemAddedToCart(order_id=order.id, product_id="p1"),
            ],
            expected_version=0,
            command_id=uuid4(),
        )

        stream = await store.read_stream(str(order.id))
        assert stream.version == 2
        assert len(stream.events) == 2

    async def test_command_idempotency(self, store):
        cmd_id = uuid4()
        order_id = str(uuid4())

        await store.append_to_stream(
            aggregate_id=order_id,
            events=[OrderPlaced(order_id=UUID(order_id), customer_id="c1")],
            expected_version=0,
            command_id=cmd_id,
        )

        # Same command_id — no new events appended
        await store.append_to_stream(
            aggregate_id=order_id,
            events=[OrderPlaced(order_id=UUID(order_id), customer_id="c1")],
            expected_version=0,
            command_id=cmd_id,
        )

        stream = await store.read_stream(order_id)
        assert len(stream.events) == 1

    async def test_global_read_all(self, store):
        for i in range(3):
            await store.append_to_stream(
                aggregate_id=f"order-{i}",
                events=[OrderPlaced(order_id=UUID(f"00000000-0000-0000-0000-{i:012}"), customer_id=f"c{i}")],
                expected_version=0,
                command_id=uuid4(),
            )

        all_events = await store.read_all(from_version=0)
        assert len(all_events.events) == 3
```

## Expected Outcome

Your tests use `FakeEventStore` for event-sourced aggregate and projection tests. Events are stored in memory, streams are readable, and command idempotency is enforced. No database or broker needed.

## See Also

- [Event Store concept](../../concepts/es/event-store.md)
- [Use a Fake Snapshot Store](use-fake-snapshot-store.md)
- [Use a Fake Checkpoint Store](use-fake-checkpoint-store.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
