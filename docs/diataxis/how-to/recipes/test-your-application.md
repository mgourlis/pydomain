# Recipe: Test Your Application

> **Adoption Level:** All levels · Prerequisites: All module concepts and how-tos, [Testing philosophy concept](../../concepts/testing/testing-philosophy.md)

This recipe catalogs every fake and in-memory double in `pydomain.testing`, shows when to use each one, and provides complete test examples for each architectural layer.

## Fake Inventory

| Class | Implements | Use For |
|-------|-----------|---------|
| `FakeRepository` | `Repository[T, TId]` | Testing command handlers that persist aggregates |
| `FakeUnitOfWork` | `AbstractUnitOfWork` | Wrapping transaction boundaries with `FakeRepository` |
| `FakeEventStore` | `EventStore` | Testing event-sourced aggregates and ES repositories |
| `FakeSnapshotStore` | `SnapshotStore` | Testing snapshot creation and recovery |
| `FakeSagaRepository` | `SagaRepository` | Testing saga lifecycle, state hydration, recovery |
| `FakeCheckpointStore` | `CheckpointStore` | Testing catch-up subscriptions and projections |
| `FakeLockProvider` | `LockProvider` | Testing distributed locking behaviors |
| `FakeProcessedCommandStore` | `ProcessedCommandStore` | Testing idempotency behavior |
| `InMemoryMessageBroker` | (duck-typed) | Testing integration event publishing |
| `InMemoryMessageSubscriber` | `MessageSubscriber` | Testing external message consumption |
| `InMemoryProjectionStore` | `ProjectionStore` | Testing read model projections |

All fakes are **in-memory** — no database, no broker, no external process. Tests run in milliseconds and are fully deterministic.

## DDD Layer Testing

Test entities, value objects, and aggregates in isolation with `FakeRepository`.

```python
import pytest
from uuid import uuid4
from pydomain.testing import FakeRepository
from pydomain.ddd.exceptions import ConcurrencyError, DomainError


class TestOrderAggregate:
    """Domain-layer tests — no fakes needed for aggregate behavior."""

    def test_place_records_event(self):
        order = Order(id=uuid4(), customer_id=uuid4())
        order.add_item("Widget", quantity=2, unit_price=500)
        order.place()

        assert order.status == "placed"
        events = order.pull_events()
        assert len(events) == 1
        assert events[0].total_amount == 1000

    def test_mutation_guards_raise_domain_errors(self):
        order = Order(id=uuid4(), customer_id=uuid4())
        order.add_item("Widget", quantity=1, unit_price=100)
        order.place()

        with pytest.raises(OrderNotModifiable):
            order.add_item("Gadget", quantity=1, unit_price=200)


class TestWithFakeRepository:
    """Repository integration tests with FakeRepository."""

    async def test_save_and_retrieve(self):
        repo: FakeRepository[Order, UUID] = FakeRepository()
        order = Order(id=uuid4(), customer_id=uuid4())

        await repo.save(order)
        found = await repo.get_by_id(order.id)

        assert found is not None
        assert found.id == order.id

    async def test_concurrency_conflict(self):
        order = Order(id=uuid4(), customer_id=uuid4())
        repo = FakeRepository(aggregates=[order])

        a1 = await repo.get_by_id(order.id)
        a2 = await repo.get_by_id(order.id)
        a1.add_item("Widget", 1, 100)
        await repo.save(a1)
        a2.add_item("Gadget", 1, 50)

        with pytest.raises(ConcurrencyError):
            await repo.save(a2)

    async def test_event_collection(self):
        repo: FakeRepository[Order, UUID] = FakeRepository()
        order = Order(id=uuid4(), customer_id=uuid4())
        order.add_item("Widget", 1, 100)
        order.place()

        await repo.save(order)
        events = repo.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], OrderPlaced)

        # Buffer is drained after pull
        assert repo.pull_events() == []
```

## CQRS Layer Testing

Test command handlers, queries, and pipeline behaviors with `FakeUoW`, `FakeProcessedCommandStore`, and `FakeLockProvider`.

```python
import pytest
from uuid import uuid4
from pydomain.testing import (
    FakeRepository,
    FakeUnitOfWork,
    FakeProcessedCommandStore,
    FakeLockProvider,
)


class FakeCommandBus:
    """Minimal fake for handler tests — records dispatched commands."""

    def __init__(self):
        self.dispatched: list = []

    async def dispatch(self, command):
        self.dispatched.append(command)
        return command  # Echo back for idempotency


class TestPlaceOrderHandler:
    @pytest.fixture
    def repo(self) -> FakeRepository[Order, UUID]:
        return FakeRepository()

    @pytest.fixture
    def uow(self, repo) -> FakeUnitOfWork:
        return FakeUnitOfWork(repository=repo)

    @pytest.fixture
    def handler(self, repo) -> PlaceOrderHandler:
        return PlaceOrderHandler(repository=repo)

    async def test_place_order_returns_result(self, handler, uow, repo):
        cmd = PlaceOrder(
            command_id=uuid4(),
            customer_id=uuid4(),
            items=[OrderLineItem(product_name="Widget", quantity=1, unit_price=500)],
        )
        result = await handler(cmd, uow)

        assert result.status == "placed"
        saved = await repo.get_by_id(result.order_id)
        assert saved is not None

    async def test_domain_events_collected_via_uow(self, handler, uow, repo):
        cmd = PlaceOrder(
            command_id=uuid4(),
            customer_id=uuid4(),
            items=[OrderLineItem(product_name="Widget", quantity=1, unit_price=500)],
        )
        await handler(cmd, uow)

        # UoW.commit() pulls events from all repos
        await uow.commit()
        assert len(uow._collected_events) > 0


class TestIdempotencyBehavior:
    async def test_duplicate_command_returns_cached_result(self):
        store = FakeProcessedCommandStore()
        behavior = IdempotencyBehavior(store)

        cmd = PlaceOrder(command_id=uuid4(), customer_id=uuid4(), items=[])
        result = PlaceOrderResult(order_id=uuid4(), status="placed")

        # First call — store the result
        await behavior.handle(cmd, lambda _: result)  # handler returns result

        # Second call — should find cached result
        assert await store.contains(cmd.command_id)
        cached = await store.get(cmd.command_id)
        assert cached.status == "placed"


class TestDistributedLocking:
    async def test_locking_prevents_concurrent_handler_invocation(self):
        lock_provider = FakeLockProvider()
        behavior = DistributedLockingBehavior(lock_provider)

        key = "order-123"
        await lock_provider.acquire(key)
        assert key in lock_provider._locks

        # Release
        await lock_provider.release(key)
        # Lock is released — can be re-acquired
        await lock_provider.acquire(key)
```

## Event Sourcing Layer Testing

Test event-sourced aggregates and projections with `FakeEventStore`, `FakeSnapshotStore`, and `FakeCheckpointStore`.

```python
import pytest
from uuid import UUID, uuid4
from pydomain.testing import (
    FakeEventStore,
    FakeSnapshotStore,
    FakeCheckpointStore,
)
from pydomain.ddd.exceptions import ConcurrencyError


class TestEventSourcedAggregate:
    """Aggregate behavior tested with FakeEventStore."""

    async def test_append_and_replay(self):
        store = FakeEventStore()
        aggregate_id = str(uuid4())

        order = Order(id=UUID(aggregate_id))
        order.place(customer_id=uuid4(), total_amount=1000, currency="EUR")
        events = order.pull_events()

        await store.append_to_stream(
            aggregate_id=aggregate_id,
            events=events,
            expected_version=0,
            command_id=uuid4(),
        )

        # Replay
        stream = await store.read_stream(aggregate_id)
        assert len(stream.events) == 1
        assert isinstance(stream.events[0], OrderPlaced)

    async def test_concurrency_on_append(self):
        store = FakeEventStore()
        agg_id = str(uuid4())

        await store.append_to_stream(
            aggregate_id=agg_id,
            events=[OrderPlaced(
                event_id=uuid4(), order_id=UUID(agg_id),
                customer_id=uuid4(), total_amount=100, currency="EUR",
                placed_at=datetime.now(UTC),
            )],
            expected_version=0,
        )

        with pytest.raises(ConcurrencyError):
            await store.append_to_stream(
                aggregate_id=agg_id,
                events=[OrderPlaced(
                    event_id=uuid4(), order_id=UUID(agg_id),
                    customer_id=uuid4(), total_amount=200, currency="EUR",
                    placed_at=datetime.now(UTC),
                )],
                expected_version=0,  # Stale — stream already has 1 event
            )

    async def test_deduplication_on_duplicate_command(self):
        store = FakeEventStore()
        agg_id = str(uuid4())
        cmd_id = uuid4()

        await store.append_to_stream(
            aggregate_id=agg_id,
            events=[OrderPlaced(
                event_id=uuid4(), order_id=UUID(agg_id),
                customer_id=uuid4(), total_amount=100, currency="EUR",
                placed_at=datetime.now(UTC),
            )],
            expected_version=0,
            command_id=cmd_id,
        )

        from pydomain.es.exceptions import DuplicateCommandError
        with pytest.raises(DuplicateCommandError):
            await store.append_to_stream(
                aggregate_id=agg_id,
                events=[OrderPlaced(
                    event_id=uuid4(), order_id=UUID(agg_id),
                    customer_id=uuid4(), total_amount=200, currency="EUR",
                    placed_at=datetime.now(UTC),
                )],
                expected_version=1,
                command_id=cmd_id,  # Same command_id
            )


class TestSnapshots:
    async def test_save_and_load_snapshot(self):
        store = FakeSnapshotStore()

        snapshot = Snapshot(
            aggregate_id="order-1",
            version=5,
            state={"status": "placed", "total": 1000},
        )
        await store.save("Order", snapshot)

        loaded = await store.get("Order", "order-1")
        assert loaded is not None
        assert loaded.version == 5
        assert loaded.state["status"] == "placed"

    async def test_missing_snapshot_returns_none(self):
        store = FakeSnapshotStore()
        loaded = await store.get("Order", "nonexistent")
        assert loaded is None


class TestCheckpoints:
    async def test_save_and_load_checkpoint(self):
        store = FakeCheckpointStore()

        assert await store.load("my-subscription") == 0  # default

        await store.save("my-subscription", 42)
        assert await store.load("my-subscription") == 42
```

## Saga Layer Testing

Test saga lifecycles, state hydration, and compensation with `FakeSagaRepository`.

```python
import pytest
from uuid import uuid4
from pydomain.testing import FakeSagaRepository
from pydomain.cqrs.saga import SagaRegistry, SagaManager, SagaStatus


class FakeCommandBus:
    def __init__(self):
        self.dispatched: list = []

    async def dispatch(self, command):
        self.dispatched.append(command)


class TestSagaManager:
    @pytest.fixture
    def repo(self) -> FakeSagaRepository:
        return FakeSagaRepository()

    @pytest.fixture
    def cmd_bus(self) -> FakeCommandBus:
        return FakeCommandBus()

    @pytest.fixture
    def registry(self):
        r = SagaRegistry()
        r.register_saga(OrderFulfillmentSaga)
        return r

    @pytest.fixture
    def manager(self, repo, cmd_bus, registry):
        return SagaManager(
            repository=repo,
            registry=registry,
            command_bus=cmd_bus,
        )

    async def test_full_happy_path(self, manager, repo, cmd_bus):
        correlation_id = uuid4()

        await manager.handle(OrderCreated(
            event_id=uuid4(),
            order_id=uuid4(),
            customer_id=uuid4(),
            correlation_id=correlation_id,
        ))

        state = await repo.find_by_correlation_id(
            correlation_id, "OrderFulfillmentSaga"
        )
        assert state is not None
        assert state.status == SagaStatus.RUNNING
        assert len(cmd_bus.dispatched) > 0

    async def test_state_deep_copy_isolation(self, repo):
        """FakeSagaRepository returns deep copies — mutations don't leak."""
        state = SagaState(
            id=uuid4(),
            saga_type="TestSaga",
            correlation_id=uuid4(),
        )
        await repo.save(state)

        loaded = await repo.get_by_id(state.id)
        loaded.current_step = "modified"

        reloaded = await repo.get_by_id(state.id)
        assert reloaded.current_step == "init"  # Original preserved


class TestSagaStateIsolation:
    """Saga state behavior tested directly — no fakes needed."""

    async def test_mark_event_processed_is_idempotent(self):
        state = SagaState(id=uuid4(), saga_type="TestSaga")
        event_id = uuid4()

        assert not state.is_event_processed(event_id)
        state.mark_event_processed(event_id)
        assert state.is_event_processed(event_id)

    def test_terminal_states(self):
        state = SagaState(id=uuid4(), saga_type="TestSaga")

        state.status = SagaStatus.RUNNING
        assert not state.is_terminal

        state.status = SagaStatus.COMPLETED
        assert state.is_terminal

    def test_step_history_grows_monotonically(self):
        state = SagaState(id=uuid4(), saga_type="TestSaga")

        for i in range(5):
            state.record_step(
                step_name=f"step_{i}",
                event_type=f"Event{i}",
                causation_id=uuid4(),
            )

        assert len(state.step_history) == 5
        assert state.current_step == "step_4"
```

## Infrastructure Layer Testing

Test message publishing and subscription with `InMemoryMessageBroker`, `InMemoryMessageSubscriber`, and `InMemoryProjectionStore`.

```python
import pytest
from uuid import uuid4
from pydomain.testing import (
    InMemoryMessageBroker,
    InMemoryMessageSubscriber,
    InMemoryProjectionStore,
)


class TestMessageBroker:
    async def test_publish_captures_topic_and_event(self):
        broker = InMemoryMessageBroker()

        event = OrderShipped(order_id=uuid4(), tracking_number="TRACK-1")
        await broker.publish("orders", event)

        assert len(broker.published) == 1
        topic, captured = broker.published[0]
        assert topic == "orders"
        assert captured.order_id == event.order_id

    async def test_multiple_publishes_accumulate(self):
        broker = InMemoryMessageBroker()
        await broker.publish("orders", OrderShipped(order_id=uuid4(), tracking_number="A"))
        await broker.publish("orders", OrderShipped(order_id=uuid4(), tracking_number="B"))
        assert len(broker.published) == 2


class TestMessageSubscriber:
    async def test_simulate_message_invokes_handler(self):
        subscriber = InMemoryMessageSubscriber()
        received: list[dict] = []

        async def handler(payload: dict):
            received.append(payload)

        subscriber.subscribe("orders", handler)
        await subscriber.simulate_message("orders", {"order_id": "o1"})

        assert len(received) == 1
        assert received[0]["order_id"] == "o1"

    async def test_start_stop_flags(self):
        subscriber = InMemoryMessageSubscriber()
        assert not subscriber.started

        await subscriber.start()
        assert subscriber.started

        await subscriber.stop()
        assert subscriber.stopped

    async def test_simulate_unregistered_topic_raises(self):
        subscriber = InMemoryMessageSubscriber()
        with pytest.raises(KeyError):
            await subscriber.simulate_message("unknown", {})


class TestProjectionStore:
    async def test_save_and_load(self):
        store = InMemoryProjectionStore()

        await store.save("order-summary", {"total": 42, "count": 7})
        loaded = await store.load("order-summary")

        assert loaded == {"total": 42, "count": 7}

    async def test_missing_projection_returns_none(self):
        store = InMemoryProjectionStore()
        assert await store.load("nonexistent") is None
```

## Full Integration Test

Assemble all layers in a single test using fakes across DDD, CQRS, and Saga layers.

```python
import pytest
from uuid import uuid4
from pydomain.testing import (
    FakeRepository,
    FakeUnitOfWork,
    FakeSagaRepository,
    InMemoryMessageBroker,
)


@pytest.mark.anyio
class TestOrderProcessingIntegration:
    """End-to-end test across DDD + CQRS + Saga layers using fakes."""

    @pytest.fixture
    def order_repo(self) -> FakeRepository[Order, UUID]:
        return FakeRepository()

    @pytest.fixture
    def saga_repo(self) -> FakeSagaRepository:
        return FakeSagaRepository()

    @pytest.fixture
    def message_broker(self) -> InMemoryMessageBroker:
        return InMemoryMessageBroker()

    @pytest.fixture
    def uow(self, order_repo) -> FakeUnitOfWork:
        return FakeUnitOfWork(repository=order_repo)

    @pytest.fixture
    async def app(self, order_repo, saga_repo, message_broker):
        """Bootstrap the application with all fakes."""
        from pydomain.infrastructure.bootstrap import bootstrap
        from pydomain.cqrs.saga import SagaRegistry, SagaManager

        app = await bootstrap(event_registry=EventRegistry())

        registry = SagaRegistry()
        registry.register_saga(OrderFulfillmentSaga)

        saga_manager = SagaManager(
            repository=saga_repo,
            registry=registry,
            command_bus=app.message_bus,
        )
        saga_manager.bind_to(app.message_bus)

        def order_uow_factory():
            return FakeUnitOfWork(repository=order_repo)

        app.message_bus.register_command(
            command_type=PlaceOrder,
            handler=PlaceOrderHandler(order_repo),
            uow_factory=order_uow_factory,
        )

        return app

    async def test_place_order_triggers_saga(self, app, order_repo, saga_repo):
        correlation_id = uuid4()
        customer_id = uuid4()

        # Place order via command bus
        result, events = await app.dispatch(PlaceOrder(
            command_id=uuid4(),
            customer_id=customer_id,
            correlation_id=correlation_id,
            items=[OrderLineItem(product_name="Widget", quantity=1, unit_price=500)],
        ))

        assert result.status == "placed"

        # Domain event emitted
        placed_events = [e for e in events if isinstance(e, OrderPlaced)]
        assert len(placed_events) == 1

        # Saga started
        saga_state = await saga_repo.find_by_correlation_id(
            correlation_id, "OrderFulfillmentSaga"
        )
        assert saga_state is not None
        assert saga_state.status == SagaStatus.RUNNING
```

## Testing Patterns Summary

| Layer | Arrange | Act | Assert |
|-------|---------|-----|--------|
| **DDD** | `FakeRepository(aggregates=[...])` | `handler(cmd, uow)` | `repo.get_by_id()`, `repo.pull_events()` |
| **CQRS** | `FakeUoW(repository=repo)` | `handler(cmd, uow)` | `uow._collected_events`, handler result |
| **ES** | `FakeEventStore()` | `store.append_to_stream()` | `store.read_stream()`, stream events |
| **Saga** | `FakeSagaRepository()` | `manager.handle(event)` | `repo.find_by_correlation_id()`, state.step_history |
| **Infra** | `InMemoryMessageBroker()` | `broker.publish(topic, event)` | `broker.published` |

## What's Next?

- [All Modules Integration recipe](all-modules.md) — full monolith with DDD + CQRS + ES + Sagas
- [Use a Fake Repository how-to](../testing/use-fake-repository.md) — deep dive on `FakeRepository`
- [Use a Fake Saga Repository how-to](../testing/use-fake-saga-repository.md) — deep dive on `FakeSagaRepository`
- [Handle Domain Errors how-to](../ddd/handle-domain-errors.md) — test error propagation
