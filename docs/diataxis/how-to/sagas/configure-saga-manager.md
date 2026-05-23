# How to Configure a Saga Manager

> **Adoption Level:** 5 · Prerequisites: [Saga Manager concept](../../concepts/sagas/saga-manager.md), [Saga Registry concept](../../concepts/sagas/saga-registry.md), [Command Bus concept](../../concepts/cqrs/command-bus.md)

This guide shows how to wire the `SagaManager` with its dependencies — repository, registry, command bus — and integrate it with the application's event bus.

## 1. Create the dependencies

```python
from pydomain.cqrs.saga import SagaManager, SagaRegistry

# The repository persists SagaState (use FakeSagaRepository for testing)
from pydomain.testing.saga import FakeSagaRepository
repo = FakeSagaRepository()

# The registry maps event types to saga classes
registry = SagaRegistry()

# The command bus dispatches commands produced by sagas
from pydomain.testing.command_bus import FakeCommandBus
cmd_bus = FakeCommandBus()
```

## 2. Register your saga classes

```python
from myapp.sagas import OrderFulfillmentSaga, InventoryTrackingSaga

registry.register_saga(OrderFulfillmentSaga)
registry.register_saga(InventoryTrackingSaga)
```

Each saga's `listens_to` declaration determines which events it receives. See [Define a Saga](define-saga.md) for creating saga classes.

## 3. Create the manager

```python
manager = SagaManager(
    repository=repo,
    registry=registry,
    command_bus=cmd_bus,
)
```

## 4. Bind to the event bus

```python
from pydomain.infrastructure.message_bus import MessageBus

message_bus = MessageBus(...)
manager.bind_to(message_bus)
```

`bind_to()` reads all event types from the registry and calls `message_bus.register_event(event_type, manager.handle)` for each. This means every incoming event of a registered type is automatically routed to the manager.

## 5. Schedule background recovery tasks

For production, schedule `recover_pending_sagas()` and `process_timeouts()` on a timer:

```python
import asyncio


async def recovery_loop(manager: SagaManager, interval: int = 60):
    while True:
        await asyncio.sleep(interval)
        try:
            await manager.recover_pending_sagas(limit=50)
            await manager.process_timeouts(limit=50)
        except Exception as exc:
            logger.error("Recovery loop error: %s", exc)
```

## 6. Full bootstrap example

```python
async def bootstrap_sagas(
    repo: SagaRepository,
    registry: SagaRegistry,
    cmd_bus: CommandBus,
    message_bus: MessageBus,
) -> SagaManager:
    manager = SagaManager(
        repository=repo,
        registry=registry,
        command_bus=cmd_bus,
    )
    manager.bind_to(message_bus)

    # Start the recovery loop as a background task
    asyncio.create_task(recovery_loop(manager))

    return manager
```

## 7. Test the wiring

```python
import pytest
from uuid import uuid4


@pytest.mark.anyio
async def test_saga_manager_wiring():
    repo = FakeSagaRepository()
    registry = SagaRegistry()
    registry.register_saga(OrderFulfillmentSaga)
    cmd_bus = FakeCommandBus()

    manager = SagaManager(repo, registry, cmd_bus)

    event = OrderCreated(
        event_id=uuid4(),
        order_id=uuid4(),
        customer_id=uuid4(),
        correlation_id=uuid4(),
    )
    await manager.handle(event)

    # Verify state was persisted
    state = await repo.find_by_correlation_id(event.correlation_id, "OrderFulfillmentSaga")
    assert state is not None
    assert state.status == SagaStatus.RUNNING
```

## Expected outcome

A fully wired `SagaManager` that routes events to the correct sagas, persists state, dispatches commands, and recovers from crashes.

## Next steps

- [Handle Saga Errors](saga-error-handling.md) — configure retry and recovery
- [Prune Saga History](saga-pruning.md) — cap unbounded growth
- [Suspend, Resume & Timeout](saga-suspend-resume-timeout.md) — human-in-the-loop
