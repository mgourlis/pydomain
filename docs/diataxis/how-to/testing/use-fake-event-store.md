# How to Use a Fake Event Store

<!-- docs-test: runnable -->

> **Prerequisites:** [Event Store concept](../../concepts/es/event-store.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

<!-- docs-test: setup
from uuid import UUID, uuid4

from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot


class OrderPlaced(DomainEvent):
    order_id: str
    customer_id: str


class ItemAddedToCart(DomainEvent):
    order_id: str
    product_id: str


class Order(EventSourcedAggregateRoot[UUID]):
    customer_id: str = ""
    product_ids: list[str] = []

    @classmethod
    def place(cls, customer_id: str) -> "Order":
        order = cls(id=uuid4())
        order._apply(OrderPlaced(order_id=str(order.id), customer_id=customer_id))
        return order

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self.customer_id = event.customer_id
        elif isinstance(event, ItemAddedToCart):
            self.product_ids = [*self.product_ids, event.product_id]
-->

## Problem

You need to test event-sourced aggregates and projections without connecting to a real event store. Tests must simulate append-only event streams, optimistic concurrency, stream reads, and command idempotency.

## Solution

Use `FakeEventStore` from `pydomain.testing`: an in-memory implementation of the `EventStore` protocol with command-idempotency deduplication and a global event log. It keeps the promises of the `EventStore` Protocol and passes `EventStoreContract` ([ADR-072](../../../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md)), so a test that passes against it fails against a real store only where the real store breaks the same promises. It stores the event objects themselves, with no serialization round trip.

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

`expected_version` is the number of events already in the stream, so the first append to a stream expects `0`:

```python
from uuid import uuid4

await store.append_to_stream(
    aggregate_id="order-1",
    events=[OrderPlaced(order_id="order-1", customer_id="c1")],
    expected_version=0,  # a new stream has no events yet
    command_id=uuid4(),
)
```

If `expected_version` does not match the stream's length, `append_to_stream` raises `ConcurrencyError` and stores nothing, which is how the fake enforces optimistic concurrency:

```python
import pytest

from pydomain.ddd.exceptions import ConcurrencyError

with pytest.raises(ConcurrencyError):
    await store.append_to_stream(
        "order-1",
        [ItemAddedToCart(order_id="order-1", product_id="p1")],
        expected_version=0,  # stale: the stream already has one event
    )
```

### 4. Read a stream

```python
stream = await store.read_stream(aggregate_id="order-1")
assert stream.version == 1
assert [type(event).__name__ for event in stream.events] == ["OrderPlaced"]
```

Reading a stream that was never appended to raises `StreamNotFoundError`. An append with no events counts as no append: it runs the version and `command_id` checks, then stores nothing, so the stream stays missing:

```python
from pydomain.es.exceptions import StreamNotFoundError

await store.append_to_stream("order-9", [], expected_version=0)

with pytest.raises(StreamNotFoundError):
    await store.read_stream("order-9")
```

### 5. Read with a starting version

`from_version` is a zero-based offset. `version` stays the stream's full length:

```python
await store.append_to_stream(
    "order-1",
    [ItemAddedToCart(order_id="order-1", product_id="p1")],
    expected_version=1,
)

stream = await store.read_stream(aggregate_id="order-1", from_version=1)
assert [type(event).__name__ for event in stream.events] == ["ItemAddedToCart"]
assert stream.version == 2
```

A negative `from_version` raises `ValueError`; the fake never reads from the end of the stream:

```python
with pytest.raises(ValueError):
    await store.read_stream("order-1", from_version=-1)
```

### 6. Read all events globally (for catch-up subscriptions)

`read_all` returns every stream's events in append order, with the total count as `version`. It rejects a negative `from_version` too:

```python
all_events = await store.read_all(from_version=0)
assert len(all_events.events) == 2
assert all_events.version == 2
```

### 7. Test command idempotency

A `command_id` that already appended to a stream is rejected with `DuplicateCommandError`, and nothing is appended:

```python
from pydomain.es.exceptions import DuplicateCommandError

cmd_id = uuid4()
await store.append_to_stream(
    "order-2",
    [OrderPlaced(order_id="order-2", customer_id="c2")],
    expected_version=0,
    command_id=cmd_id,
)

with pytest.raises(DuplicateCommandError):
    await store.append_to_stream(
        "order-2",
        [ItemAddedToCart(order_id="order-2", product_id="p2")],
        expected_version=1,
        command_id=cmd_id,  # the same command again
    )

stream = await store.read_stream("order-2")
assert len(stream.events) == 1
```

### 8. Retry a command after a conflict

A `command_id` counts as processed only once its append stores events. After a `ConcurrencyError`, reload and retry the same command at the current version:

```python
retry_id = uuid4()

with pytest.raises(ConcurrencyError):
    await store.append_to_stream(
        "order-2",
        [ItemAddedToCart(order_id="order-2", product_id="p3")],
        expected_version=0,  # stale
        command_id=retry_id,
    )

await store.append_to_stream(
    "order-2",
    [ItemAddedToCart(order_id="order-2", product_id="p3")],
    expected_version=1,  # the current version
    command_id=retry_id,
)
assert (await store.read_stream("order-2")).version == 2
```

## Complete Example

```python
import pytest
from uuid import UUID, uuid4

from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.es.exceptions import DuplicateCommandError
from pydomain.testing import FakeEventStore


class TestEventSourcedAggregate:
    @pytest.fixture
    def store(self) -> FakeEventStore:
        return FakeEventStore()

    @pytest.fixture
    def repo(self, store: FakeEventStore) -> EventSourcedRepository[Order, UUID]:
        return EventSourcedRepository(event_store=store, aggregate_cls=Order)

    @pytest.mark.anyio
    async def test_save_and_load(self, repo: EventSourcedRepository[Order, UUID]) -> None:
        order = Order.place(customer_id="c1")

        await repo.save(order)

        loaded = await repo.get_by_id(order.id)
        assert loaded is not None
        assert loaded.customer_id == "c1"
        assert loaded.version == 1  # one event applied

    @pytest.mark.anyio
    async def test_append_multiple_events(self, store: FakeEventStore) -> None:
        await store.append_to_stream(
            aggregate_id="order-1",
            events=[
                OrderPlaced(order_id="order-1", customer_id="c1"),
                ItemAddedToCart(order_id="order-1", product_id="p1"),
            ],
            expected_version=0,
            command_id=uuid4(),
        )

        stream = await store.read_stream("order-1")
        assert stream.version == 2
        assert len(stream.events) == 2

    @pytest.mark.anyio
    async def test_command_idempotency(self, store: FakeEventStore) -> None:
        cmd_id = uuid4()
        await store.append_to_stream(
            aggregate_id="order-1",
            events=[OrderPlaced(order_id="order-1", customer_id="c1")],
            expected_version=0,
            command_id=cmd_id,
        )

        with pytest.raises(DuplicateCommandError):
            await store.append_to_stream(
                aggregate_id="order-1",
                events=[ItemAddedToCart(order_id="order-1", product_id="p1")],
                expected_version=1,
                command_id=cmd_id,
            )

        stream = await store.read_stream("order-1")
        assert len(stream.events) == 1

    @pytest.mark.anyio
    async def test_global_read_all(self, store: FakeEventStore) -> None:
        for i in range(3):
            await store.append_to_stream(
                aggregate_id=f"order-{i}",
                events=[OrderPlaced(order_id=f"order-{i}", customer_id=f"c{i}")],
                expected_version=0,
                command_id=uuid4(),
            )

        all_events = await store.read_all(from_version=0)
        assert len(all_events.events) == 3
```

## Expected Outcome

Your tests use `FakeEventStore` for event-sourced aggregate and projection tests. Events are stored in memory, streams are readable, and optimistic concurrency and command idempotency are enforced as the `EventStore` Protocol states. No database or broker needed. To check your own production event store against the same promises, run `EventStoreContract` against it ([Run a Contract Suite Against Your Adapter](run-contract-suites.md)).

## See Also

- [Event Store concept](../../concepts/es/event-store.md)
- [Run a Contract Suite Against Your Adapter](run-contract-suites.md)
- [Use a Fake Snapshot Store](use-fake-snapshot-store.md)
- [Use a Fake Checkpoint Store](use-fake-checkpoint-store.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
