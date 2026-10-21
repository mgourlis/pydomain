# How to Use a Fake Saga Repository

<!-- docs-test: runnable -->

> **Prerequisites:** [Saga concept](../../concepts/sagas/saga.md), [Saga Repository concept](../../concepts/sagas/saga-repository.md), [Testing philosophy](../../concepts/testing/testing-philosophy.md)

## Problem

You need to test sagas — their state persistence, correlation-based lookup, stalled-saga recovery, and event collection — without a real database.

## Solution

Use `FakeSagaRepository` from `pydomain.testing` — an in-memory implementation of the `SagaRepository` protocol that stores `SagaState` objects keyed by their `id`, supports correlation ID lookup, stalled, suspended and expired saga queries, and collects domain events via `pull_events()`. It passes `SagaRepositoryContract` ([run the contract suites](run-contract-suites.md)).

## Steps

### 1. Import FakeSagaRepository

```python
from pydomain.testing import FakeSagaRepository
```

### 2. Create a fake saga repository

```python
repo = FakeSagaRepository()
```

### 3. Save and retrieve saga state

```python
from uuid import uuid4

from pydomain.cqrs.saga.state import SagaState, SagaStatus

state = SagaState(saga_type="OrderSaga", correlation_id=uuid4())
await repo.save(state)

loaded = await repo.get_by_id(state.id)
assert loaded is not None
assert loaded is not state
```

The fake stores a deep copy on `save` and returns a deep copy on every read, so a change to a loaded state is not visible until you save it.

### 4. Find by correlation ID

```python
found = await repo.find_by_correlation_id(state.correlation_id, saga_type="OrderSaga")
assert found is not None
assert found.id == state.id
```

### 5. Find stalled sagas

A saga is stalled when it is not terminal and it either has `pending_commands` (a dispatch that has not finished) or is in `COMPENSATING` (a compensation that was interrupted). `SagaManager.recover_pending_sagas()` recovers both kinds.

```python
dispatching = SagaState(
    saga_type="OrderSaga",
    correlation_id=uuid4(),
    status=SagaStatus.RUNNING,
    pending_commands=[
        {
            "command_type": "ReserveItems",
            "module_name": "orders.commands",
            "data": {"order_id": "order-1"},
            "dispatched": False,
        }
    ],
)
compensating = SagaState(
    saga_type="OrderSaga", correlation_id=uuid4(), status=SagaStatus.COMPENSATING
)
await repo.save(dispatching)
await repo.save(compensating)

stalled = await repo.find_stalled_sagas(limit=10)
assert {s.id for s in stalled} == {dispatching.id, compensating.id}
```

### 6. Find suspended and expired suspended sagas

```python
from datetime import UTC, datetime, timedelta

waiting = SagaState(
    saga_type="OrderSaga",
    correlation_id=uuid4(),
    status=SagaStatus.SUSPENDED,
    timeout_at=datetime.now(UTC) - timedelta(minutes=1),
)
await repo.save(waiting)

# All suspended sagas
suspended = await repo.find_suspended_sagas(limit=10)

# Only suspended sagas whose timeout has expired
expired = await repo.find_expired_suspended_sagas(limit=10)
assert [s.id for s in expired] == [waiting.id]
```

### 7. Collect domain events

`save` drains the state's pending events into the repository; `pull_events()` hands them over once and empties the buffer. A later save of the same state, with no new event, hands over nothing.

```python
from pydomain.ddd.domain_event import DomainEvent


class OrderNoted(DomainEvent):
    order_id: str


repo.pull_events()  # start from an empty buffer
noted = SagaState(saga_type="OrderSaga", correlation_id=uuid4())
noted._add_event(OrderNoted(order_id="order-1"))
await repo.save(noted)

assert [type(e).__name__ for e in repo.pull_events()] == ["OrderNoted"]
assert repo.pull_events() == []

reloaded = await repo.get_by_id(noted.id)
assert reloaded is not None
await repo.save(reloaded)
assert repo.pull_events() == []
```

`_add_event` is how saga code inside the library records events; tests use it only to put an event on a state.

## Complete Example

```python
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pydomain.cqrs.saga.state import SagaState, SagaStatus
from pydomain.testing import FakeSagaRepository


def new_state(**fields) -> SagaState:
    return SagaState(saga_type="OrderSaga", correlation_id=uuid4(), **fields)


@pytest.fixture
def repo() -> FakeSagaRepository:
    return FakeSagaRepository()


@pytest.mark.anyio
async def test_save_and_load_saga(repo: FakeSagaRepository) -> None:
    state = new_state(current_step="reserving")
    await repo.save(state)

    loaded = await repo.get_by_id(state.id)
    assert loaded is not None
    assert loaded.current_step == "reserving"


@pytest.mark.anyio
async def test_returns_none_for_unknown_correlation(repo: FakeSagaRepository) -> None:
    assert await repo.find_by_correlation_id(uuid4(), saga_type="OrderSaga") is None


@pytest.mark.anyio
async def test_compensating_saga_is_stalled(repo: FakeSagaRepository) -> None:
    state = new_state(status=SagaStatus.COMPENSATING)
    await repo.save(state)

    assert [s.id for s in await repo.find_stalled_sagas()] == [state.id]


@pytest.mark.anyio
async def test_find_expired_suspended_sagas(repo: FakeSagaRepository) -> None:
    state = new_state(
        status=SagaStatus.SUSPENDED,
        timeout_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    await repo.save(state)

    assert len(await repo.find_expired_suspended_sagas()) == 1


@pytest.mark.anyio
async def test_deep_copy_isolation(repo: FakeSagaRepository) -> None:
    state = new_state(current_step="reserving")
    await repo.save(state)

    loaded = await repo.get_by_id(state.id)
    assert loaded is not None
    loaded.current_step = "modified"

    stored = await repo.get_by_id(state.id)
    assert stored is not None
    assert stored.current_step == "reserving"
```

<!-- docs-test: check
from tests.docs.doc_examples import run_tests

await run_tests(
    test_save_and_load_saga,
    test_returns_none_for_unknown_correlation,
    test_compensating_saga_is_stalled,
    test_find_expired_suspended_sagas,
    test_deep_copy_isolation,
)
-->


## Expected Outcome

Your saga tests use `FakeSagaRepository` for saga state persistence. States can be saved, retrieved by ID or correlation ID, and queried as stalled (pending commands or `COMPENSATING`), suspended or expired; domain events are handed over once via `pull_events()`. Deep-copy semantics prevent test pollution through mutable aliasing.

## See Also

- [Saga Repository concept](../../concepts/sagas/saga-repository.md)
- [Run the contract suites](run-contract-suites.md)
- [Define a Saga](../sagas/define-saga.md)
- [Testing philosophy](../../concepts/testing/testing-philosophy.md)
