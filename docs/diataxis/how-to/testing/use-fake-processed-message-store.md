# How to Use a Fake Processed Message Store

<!-- docs-test: runnable -->

<!-- docs-test: setup
from pydomain.cqrs import Command, CommandResult
from pydomain.infrastructure import bootstrap
from pydomain.testing import FakeUnitOfWork


class PlaceOrderResult(CommandResult):
    order_id: str


class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: str


class PlaceOrderHandler:
    def __init__(self, repo: object) -> None:
        self._repo = repo
        self.calls = 0

    async def __call__(self, command: PlaceOrder, uow: object) -> PlaceOrderResult:
        self.calls += 1
        return PlaceOrderResult(order_id="abc-123")


repo = object()
uow_factory = FakeUnitOfWork
app = await bootstrap()
-->

> **Prerequisites:** [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md), [Add Idempotency](../cqrs/add-idempotency.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test command idempotency — ensuring that replaying a command with the same `command_id` returns the cached result instead of executing the handler again. You need an in-memory store for tests.

## Solution

Use `FakeProcessedMessageStore` from `pydomain.testing` — an in-memory implementation of the `ProcessedMessageStore` protocol that returns the `MISSING` sentinel for unknown message IDs. It also implements `MessageReservationStore`, so `IdempotencyBehavior` reserves each command and refuses a concurrent duplicate with `CommandInProgressError`. It was called `FakeProcessedCommandStore` before [ADR-062](../../../adr/ADR-062-eventbus-application-layer-peer.md) renamed it.

## Steps

### 1. Import FakeProcessedMessageStore

```python
from pydomain.testing import FakeProcessedMessageStore
```

### 2. Create a fake processed message store

```python
store = FakeProcessedMessageStore()
```

### 3. Check for unknown commands

```python
from uuid import uuid4

from pydomain.cqrs import MISSING

cmd_id = uuid4()
result = await store.get(cmd_id)
assert result is MISSING
```

### 4. Store a processed command result

```python
await store.set(cmd_id, {"order_id": "abc-123"})
```

### 5. Retrieve a cached result

```python
result = await store.get(cmd_id)
print(result)  # {'order_id': 'abc-123'}
```

### 6. Check existence

```python
exists = await store.contains(cmd_id)
print(exists)  # True
```

### 7. Use with IdempotencyBehavior

```python
from pydomain.cqrs import IdempotencyBehavior


store = FakeProcessedMessageStore()
behavior = IdempotencyBehavior(store)
handler = PlaceOrderHandler(repo)

# Register on the application's command bus
app.message_bus.register_command(
    PlaceOrder,
    handler,
    uow_factory,
    behaviors=[behavior],
)

command = PlaceOrder(customer_id="customer-1")
first = await app.dispatch(command)
second = await app.dispatch(command)  # same command_id

assert second == first
assert handler.calls == 1
await app.shutdown()
```

When a command is dispatched:
- `behavior` calls `store.get(command.command_id)`
- If `MISSING`: it reserves the id with `store.reserve(command.command_id)`, the handler runs, and its result is stored via `store.set(command.command_id, result)`, which completes the reservation
- If cached: the handler is skipped and the cached result is returned; nothing is raised
- If another dispatch holds the reservation: the handler is skipped and the dispatch raises `CommandExecutionError` caused by `CommandInProgressError`
- If the handler or the commit fails: the reservation is released with its token, so a retry runs

### 8. Reserve and release directly

`reserve` returns a token, or `None` while the id is reserved; `release` frees the id only for the token that holds it:

```python
order_id = uuid4()

token = await store.reserve(order_id)
assert token is not None
assert await store.reserve(order_id) is None  # still reserved
assert await store.get(order_id) is MISSING  # a reservation is not a result

await store.release(order_id, token)
assert await store.reserve(order_id) is not None  # free again
```

### 9. Test a reservation that expires

A reservation that is never released, as after a crash or a failed `release`, blocks its id until it expires. Pass `reservation_ttl` (seconds) to make the fake's reservations expire; without it they never do:

```python
import asyncio

expiring = FakeProcessedMessageStore(reservation_ttl=0.01)
crashed_id = uuid4()

await expiring.reserve(crashed_id)  # never released
await asyncio.sleep(0.02)
assert await expiring.reserve(crashed_id) is not None  # the TTL freed it
```

Once `set` completed a reservation, the id stays reserved, whatever the TTL.

## Complete Example

```python
import pytest
from uuid import uuid4

from pydomain.testing import FakeProcessedMessageStore
from pydomain.cqrs import MISSING

pytestmark = pytest.mark.anyio  # the async tests run on the anyio pytest plugin


class TestFakeProcessedMessageStore:
    @pytest.fixture
    def store(self) -> FakeProcessedMessageStore:
        return FakeProcessedMessageStore()

    async def test_unknown_command_returns_missing(self, store):
        result = await store.get(uuid4())
        assert result is MISSING

    async def test_store_and_retrieve_result(self, store):
        cmd_id = uuid4()
        await store.set(cmd_id, {"status": "ok", "order_id": "abc"})

        result = await store.get(cmd_id)
        assert result == {"status": "ok", "order_id": "abc"}

    async def test_contains(self, store):
        cmd_id = uuid4()
        assert not await store.contains(cmd_id)

        await store.set(cmd_id, "result")
        assert await store.contains(cmd_id)

    async def test_overwrite(self, store):
        cmd_id = uuid4()
        await store.set(cmd_id, "first")
        await store.set(cmd_id, "second")

        result = await store.get(cmd_id)
        assert result == "second"

    async def test_independent_command_ids(self, store):
        id1 = uuid4()
        id2 = uuid4()

        await store.set(id1, "result-1")
        await store.set(id2, "result-2")

        assert await store.get(id1) == "result-1"
        assert await store.get(id2) == "result-2"

    async def test_idempotency_behavior_integration(self, store):
        """Simulate what IdempotencyBehavior does."""
        cmd_id = uuid4()

        # First dispatch
        result = await store.get(cmd_id)
        assert result is MISSING  # not processed yet
        # Handler would run here...
        await store.set(cmd_id, PlaceOrderResult(order_id="abc-123"))

        # Second dispatch (duplicate)
        result = await store.get(cmd_id)
        assert result is not MISSING  # already processed
        # Handler is skipped, cached result returned
        assert result.order_id == "abc-123"
```

<!-- docs-test: check
from tests.docs.doc_examples import run_tests

await run_tests(TestFakeProcessedMessageStore)
-->

## Expected Outcome

Your tests use `FakeProcessedMessageStore` to verify command idempotency. Unknown commands return `MISSING`, cached results are retrieved, duplicate commands are detected, and concurrent duplicates are refused. To check your own reserving store against the same promises, run [`MessageReservationStoreContract`](run-contract-suites.md). The store integrates with `IdempotencyBehavior` for full pipeline testing.

## See Also

- [Idempotency & Locking concept](../../concepts/cqrs/idempotency-and-locking.md)
- [Add Idempotency](../cqrs/add-idempotency.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
