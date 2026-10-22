# How to Use a Fake Checkpoint Store

> **Prerequisites:** [Subscriptions concept](../../concepts/es/subscriptions.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test catch-up subscriptions and projection checkpoint tracking without a real database. Tests must verify that subscription position is saved and loaded correctly.

## Solution

Use `FakeCheckpointStore` from `pydomain.testing` — an in-memory implementation of the `CheckpointStore` protocol backed by a `dict[str, int]`.

## Steps

### 1. Import FakeCheckpointStore

```python
from pydomain.testing import FakeCheckpointStore
```

### 2. Create a fake checkpoint store

```python
store = FakeCheckpointStore()
```

### 3. Save a checkpoint

```python
await store.save(subscription_id="order-projection", checkpoint=42)
```

### 4. Load a checkpoint

```python
position = await store.load(subscription_id="order-projection")
print(f"Last processed position: {position}")  # 42
```

Returns `0` for unknown subscription IDs — a new subscription starts from the beginning.

### 5. Use with a subscription runner

`SubscriptionRunner` is abstract: subclass it and implement `process_batch`. Register subscriptions with `add_subscription()`, and use `run_once()` in tests: it makes one catch-up pass and returns, where `run()` polls until `stop()` is called.

```python
from typing import ClassVar

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.projection import EventSourcedProjection
from pydomain.infrastructure.subscription import Subscription, SubscriptionRunner
from pydomain.testing import FakeCheckpointStore, FakeEventStore


class OrderPlaced(DomainEvent):
    order_id: str


class CountingProjection(EventSourcedProjection):
    name: ClassVar[str] = "counting"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.seen = 0

    async def apply(self, event: DomainEvent) -> None:
        self.seen += 1


class ProjectionRunner(SubscriptionRunner):
    async def process_batch(self, events, subscription):
        for event in events:
            await subscription.projection.apply(event)


async def test_runner_saves_its_checkpoint():
    event_store = FakeEventStore()
    checkpoint_store = FakeCheckpointStore()
    await event_store.append_to_stream(
        "o-1",
        [OrderPlaced(order_id="o-1"), OrderPlaced(order_id="o-1")],
        expected_version=0,
    )

    projection = CountingProjection()
    runner = ProjectionRunner(
        event_store=event_store,
        checkpoint_store=checkpoint_store,
    )
    runner.add_subscription(
        Subscription("order-projection", projection, (OrderPlaced,))
    )

    await runner.run_once()
    assert projection.seen == 2
    assert await checkpoint_store.load("order-projection") == 2

    await runner.run_once()  # nothing new: no event is applied twice
    assert projection.seen == 2
```

The `SubscriptionRunner` calls `checkpoint_store.load()` to find the last position and `checkpoint_store.save()` to record progress.

## Complete Example

```python
import pytest

from pydomain.testing import FakeCheckpointStore, FakeEventStore


class TestSubscriptionCheckpoint:
    @pytest.fixture
    def checkpoint_store(self) -> FakeCheckpointStore:
        return FakeCheckpointStore()

    @pytest.fixture
    def event_store(self) -> FakeEventStore:
        return FakeEventStore()

    async def test_new_subscription_starts_at_zero(self, checkpoint_store):
        position = await checkpoint_store.load("new-subscription")
        assert position == 0

    async def test_save_and_load_checkpoint(self, checkpoint_store):
        await checkpoint_store.save("order-projection", 15)
        position = await checkpoint_store.load("order-projection")
        assert position == 15

    async def test_checkpoint_progression(self, checkpoint_store):
        await checkpoint_store.save("order-projection", 10)
        assert await checkpoint_store.load("order-projection") == 10

        await checkpoint_store.save("order-projection", 20)
        assert await checkpoint_store.load("order-projection") == 20

    async def test_independent_subscriptions(self, checkpoint_store):
        await checkpoint_store.save("sub-a", 100)
        await checkpoint_store.save("sub-b", 200)

        assert await checkpoint_store.load("sub-a") == 100
        assert await checkpoint_store.load("sub-b") == 200

    async def test_overwrite_checkpoint(self, checkpoint_store):
        await checkpoint_store.save("projection", 5)
        await checkpoint_store.save("projection", 10)
        # Overwrite with lower value (e.g., reset)
        await checkpoint_store.save("projection", 3)

        assert await checkpoint_store.load("projection") == 3
```

## Expected Outcome

Your subscription tests use `FakeCheckpointStore` to verify checkpoint save/load behavior. New subscriptions start at position 0, checkpoints are persisted and retrieved, and multiple independent subscriptions are tracked separately.

## See Also

- [Subscriptions concept](../../concepts/es/subscriptions.md)
- [Track Checkpoints](../event-sourcing/track-checkpoints.md)
- [Use a Fake Event Store](use-fake-event-store.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
