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
| [Use a Fake Processed Command Store](use-fake-processed-command-store.md) | `FakeProcessedCommandStore` | `ProcessedCommandStore` |
| [Use an In-Memory Message Broker](use-in-memory-message-broker.md) | `InMemoryMessageBroker` | (duck-typed) |
| [Use an In-Memory Message Subscriber](use-in-memory-message-subscriber.md) | `InMemoryMessageSubscriber` | `MessageSubscriber` |
| [Use an In-Memory Projection Store](use-in-memory-projection-store.md) | `InMemoryProjectionStore` | `ProjectionStore` |

## Philosophy

All fakes are in-memory — no database, no broker, no external process. Tests run fast and are fully deterministic. See [Testing Philosophy](../../concepts/testing/testing-philosophy.md) for the rationale.

## See Also

- [Concepts / Testing](../../concepts/testing/) — understanding the testing approach
- [Concepts / Infrastructure](../../concepts/infrastructure/) — the infrastructure components these fakes replace
- [Recipe: Test Your Application](../recipes/test-your-application.md) — end-to-end testing strategy
