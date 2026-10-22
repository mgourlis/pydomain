# How to Run a Contract Suite Against Your Adapter

<!-- docs-test: runnable -->

<!-- docs-test: setup
import pytest

from pydomain.testing.contracts import CheckpointStoreContract
-->

> **Prerequisites:** [Implement a Repository](../ddd/implement-repository.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md), and the testing extra: `pip install "pydomain-lib[testing]"`

## Problem

You wrote your own adapter for a pydomain Protocol, such as a SQLAlchemy `Repository` or a Postgres `CheckpointStore`. You need to check that it keeps the promises the Protocol makes, the same promises the shipped fakes are tested against, without writing those tests yourself.

## Solution

Subclass the contract suite for the Protocol from `pydomain.testing.contracts`. Each suite is a pytest base class whose tests come from the Protocol's docstring and its ADR. Override its fixture to return your adapter, and pytest runs every case against it.

| Suite | Protocol | Override |
|-------|----------|----------|
| `RepositoryContract` | `Repository` | `repository` fixture, `new_aggregate()`, `change(aggregate)` |
| `UnitOfWorkContract` | `UnitOfWork` | `unit_of_work` fixture, `stage_change(uow)` |
| `EventStoreContract` | `EventStore` | `event_store` fixture; optional `new_event()`, `new_stream_id()` |
| `SnapshotStoreContract` | `SnapshotStore` | `snapshot_store` fixture |
| `CheckpointStoreContract` | `CheckpointStore` | `checkpoint_store` fixture |
| `OutboxStoreContract` | `OutboxStore` | `outbox_store` fixture |
| `ProcessedMessageStoreContract` | `ProcessedMessageStore` | `processed_message_store` fixture |
| `LockProviderContract` | `LockProvider` | `lock_provider` fixture |
| `SagaRepositoryContract` | `SagaRepository` | `saga_repository` fixture; optional `new_state(**fields)` |
| `MessageBrokerContract` | `MessageBroker` | `broker` fixture; optional `topic` class attribute, `new_event()` |
| `MessageSubscriberContract` | `MessageSubscriber` | `subscriber` fixture, `deliver(subscriber, topic, payload)` |
| `ProjectionStoreContract` | `ProjectionStore` | `projection_store` fixture |

## Steps

### 1. Install the testing extra

The suites import `pytest`, which `pydomain` itself never needs, and `anyio`, which it installs as a runtime dependency:

```bash
pip install "pydomain-lib[testing]"
```

`import pydomain` and `import pydomain.testing` do not load the suites, so production code never imports pytest.

### 2. Subclass the suite in a test module

Name the subclass `Test*` so pytest collects it. The suite's own name ends in `Contract`, so pytest does not collect the base class on its own.

<!-- docs-test: skip -->
```python
import pytest

from pydomain.testing.contracts import CheckpointStoreContract

from myapp.infrastructure import PostgresCheckpointStore


class TestPostgresCheckpointStore(CheckpointStoreContract):
    @pytest.fixture
    def checkpoint_store(self, db_session) -> PostgresCheckpointStore:
        return PostgresCheckpointStore(db_session)
```

The fixture is an ordinary pytest fixture. It can depend on your own fixtures (`db_session` above), `yield` to clean up, or be `async`.

### 3. Give every test a fresh, empty adapter

Cases such as `read_all` on an empty `EventStore` or `fetch_unpublished` on an empty `OutboxStore` assume the adapter starts empty. Return a new instance per test, and for a database adapter, roll back or truncate between tests:

<!-- docs-test: skip -->
```python
class TestPostgresOutboxStore(OutboxStoreContract):
    @pytest.fixture
    async def outbox_store(self, engine):
        async with engine.connect() as conn:
            transaction = await conn.begin()
            yield PostgresOutboxStore(conn)
            await transaction.rollback()
```

### 4. Provide the domain objects the suite cannot build

Some Protocols are generic over your domain types, so the suite asks you for them.

`RepositoryContract.new_aggregate()` returns a never-saved aggregate with a unique id and at least one pending event. `RepositoryContract.change(aggregate)` calls a behaviour method that records another pending event:

<!-- docs-test: skip -->
```python
class TestSqlOrderRepository(RepositoryContract):
    @pytest.fixture
    def repository(self, session) -> SqlOrderRepository:
        return SqlOrderRepository(session)

    def new_aggregate(self) -> Order:
        return Order.place(customer_id="customer-1")

    def change(self, aggregate: Order) -> None:
        aggregate.confirm()
```

The suite uses `change` to check optimistic concurrency with a lost update: it loads the same aggregate twice, changes both copies, saves the first, and expects `ConcurrencyError` when it saves the second. Your `get_by_id` must return a new object on each call, as a database adapter does, or the two copies are one object and the conflict goes unseen.

`UnitOfWorkContract.stage_change(uow)` makes a change through the unit of work, as a handler does, and returns the number of domain events it records:

<!-- docs-test: skip -->
```python
class TestSqlOrderUnitOfWork(UnitOfWorkContract):
    @pytest.fixture
    def unit_of_work(self, session_factory) -> SqlOrderUnitOfWork:
        return SqlOrderUnitOfWork(session_factory)

    async def stage_change(self, uow) -> int:
        await uow.orders.save(Order.place(customer_id="customer-1"))
        return 1
```

`MessageSubscriberContract.deliver()` puts a message on a topic through your broker, and returns once the subscriber has called the handler:

<!-- docs-test: skip -->
```python
class TestKafkaSubscriber(MessageSubscriberContract):
    @pytest.fixture
    def subscriber(self, kafka) -> KafkaMessageSubscriber:
        return KafkaMessageSubscriber(kafka)

    async def deliver(self, subscriber, topic, payload) -> None:
        # Publish through your broker, then wait until the subscriber
        # has handled the message. The helper is yours to write.
        await publish_and_wait_for_handler(subscriber, topic, payload)
```

The in-memory fake does this with `await subscriber.simulate_message(topic, payload)`.

When your `EventStore` or `MessageBroker` accepts only event types it knows, override `new_event()` to return one of yours. When your broker accepts only known topics, set the `topic` class attribute of your `MessageBrokerContract` subclass, for example `topic = "orders.placed"`.

### 5. Choose the async backend

The cases are marked `@pytest.mark.anyio` and run on asyncio. Each suite defines an `anyio_backend` fixture that returns `"asyncio"`, so a `conftest.py` that parametrizes `anyio_backend` for your other tests does not reach the suites.

Override `anyio_backend` in your subclass to change it, for example to run every case on both asyncio and trio:

<!-- docs-test: skip -->
```python
class TestCheckpointStoreOnEveryBackend(CheckpointStoreContract):
    @pytest.fixture(scope="session", params=["asyncio", "trio"])
    def anyio_backend(self, request: pytest.FixtureRequest) -> str:
        return request.param
```

Keep the override session-scoped, as the suite's own fixture is. `anyio` makes every async fixture depend on `anyio_backend`, so its scope must be at least as wide as the widest async fixture your adapter fixture uses, such as a session-scoped database engine.

While a session-scoped async fixture is alive, anyio (4.15.1 at the time of writing) reuses its asyncio runner, so cases parametrized `[trio]` actually run on asyncio. To test an adapter on trio, use function-scoped async fixtures or run the trio cases in a separate pytest session.

### 6. Run the suite

```bash
pytest tests/test_postgres_checkpoint_store.py -v
```

Each case's name and docstring state the promise it checks, for example `test_load_returns_zero_for_unknown_subscription`.

## Complete Example

This is how pydomain runs the `CheckpointStore` suite against its own fake:

```python
import pytest

from pydomain.testing import FakeCheckpointStore
from pydomain.testing.contracts import CheckpointStoreContract


class TestFakeCheckpointStoreContract(CheckpointStoreContract):
    @pytest.fixture
    def checkpoint_store(self) -> FakeCheckpointStore:
        return FakeCheckpointStore()
```

<!-- docs-test: check
from tests.docs.doc_examples import run_tests

await run_tests(TestFakeCheckpointStoreContract)
-->

Running it collects five cases:

```text
test_satisfies_protocol PASSED
test_load_returns_zero_for_unknown_subscription PASSED
test_load_returns_saved_checkpoint PASSED
test_save_replaces_previous_checkpoint PASSED
test_checkpoints_are_kept_per_subscription PASSED
```

## Expected Outcome

Your adapter passes the same cases as the shipped fake. A failing case names the Protocol promise your adapter breaks. The suites cover only what the Protocols promise today. Concurrent outbox relays and lock cancellation, for example, are left to your adapter's own tests. A few promises are not covered yet, such as a unit of work undoing a change when it exits without `commit`; each suite's docstring lists the gaps.

## See Also

- [Use a Fake Repository](use-fake-repository.md)
- [Use a Fake Event Store](use-fake-event-store.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
- [ADR-001: Protocol over ABC for interfaces](../../../adr/ADR-001-protocol-over-abc-for-interfaces.md)
