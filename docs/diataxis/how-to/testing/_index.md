# Testing How-to Guides

> **Adoption Level:** All levels

Task-oriented guides for testing with `pydomain.testing` fakes and in-memory doubles. Each guide covers a single class — in-memory, no infrastructure, tests run in milliseconds.

## Fakes & In-Memory Doubles

| Guide | Class | Protocol Implemented |
|-------|-------|---------------------|
| [Use a Fake Repository](use-fake-repository.md) | `FakeRepository` | `Repository[T, TId]` |
| [Use a Fake Unit of Work](use-fake-uow.md) | `FakeUnitOfWork` | `AbstractUnitOfWork` |
| [Use a Fake Event Store](use-fake-event-store.md) | `FakeEventStore` | `EventStore` |
| [Use a Fake Snapshot Store](use-fake-snapshot-store.md) | `FakeSnapshotStore` | `SnapshotStore` |
| [Use a Fake Saga Repository](use-fake-saga-repository.md) | `FakeSagaRepository` | `SagaRepository` |
| [Use a Fake Checkpoint Store](use-fake-checkpoint-store.md) | `FakeCheckpointStore` | `CheckpointStore` |
| [Use a Fake Lock Provider](use-fake-lock-provider.md) | `FakeLockProvider` | `LockProvider` |
| [Use a Fake Processed Message Store](use-fake-processed-message-store.md) | `FakeProcessedMessageStore` | `ProcessedMessageStore` |
| [Use an In-Memory Message Broker](use-in-memory-message-broker.md) | `InMemoryMessageBroker` | (duck-typed) |
| [Use an In-Memory Message Subscriber](use-in-memory-message-subscriber.md) | `InMemoryMessageSubscriber` | `MessageSubscriber` |
| [Use an In-Memory Projection Store](use-in-memory-projection-store.md) | `InMemoryProjectionStore` | `ProjectionStore` |

## Your Own Adapters

| Guide | Module | Covers |
|-------|--------|--------|
| [Run a Contract Suite Against Your Adapter](run-contract-suites.md) | `pydomain.testing.contracts` | Every Protocol the fakes implement |

## Contributing to pydomain

| Guide | Covers |
|-------|--------|
| [Add a Runnable Docs Example](add-runnable-docs-examples.md) | Marking a Diataxis page so its Python examples run as tests |

## Philosophy

All fakes are in-memory — no database, no broker, no external process. Tests run fast and are fully deterministic. See [Testing Philosophy](../../concepts/testing/testing-philosophy.md) for the rationale.

## See Also

- [Concepts / Testing](../../concepts/testing/) — understanding the testing approach
- [Concepts / Infrastructure](../../concepts/infrastructure/) — the infrastructure components these fakes replace
- [Recipe: Test Your Application](../recipes/test-your-application.md) — end-to-end testing strategy
