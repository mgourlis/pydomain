# API Reference

> **Adoption Level:** All levels

The API reference is auto-generated from the source code via Sphinx. This page serves as the entry point and explains how to navigate the generated reference documentation.

## Generate the Reference

```bash
# From the project root:
sphinx-build -b html docs/api-reference/ docs/api-reference/_build/
```

## Module Index

| Module | Package | Description |
|--------|---------|-------------|
| **DDD** | `pydomain.ddd` | Entities, Value Objects, Aggregates, Domain Events, Repositories, Specifications, Factories, Domain Services |
| **CQRS** | `pydomain.cqrs` | Commands, Queries, Command Bus, Query Bus, Handlers, Pipeline Behaviors, Unit of Work, Idempotency, Locking, Integration Events, Projections |
| **Event Sourcing** | `pydomain.es` | Event Store, Event Stream, Event-Sourced Aggregates, Repositories, Projections, Snapshots, Upcasters, Subscriptions, Checkpoints |
| **Sagas** | `pydomain.cqrs.saga` | Saga, SagaState, SagaManager, SagaRegistry, SagaRepository, Compensation, Hydration, Pruning |
| **Infrastructure** | `pydomain.infrastructure` | Bootstrap, Message Bus, Event Registry, Message Broker, Message Subscriber, Inbound Event Gateway |
| **Testing** | `pydomain.testing` | FakeRepository, FakeUnitOfWork, FakeEventStore, FakeSnapshotStore, FakeSagaRepository, FakeCheckpointStore, FakeLockProvider, FakeProcessedCommandStore, InMemoryMessageBroker, InMemoryMessageSubscriber, InMemoryProjectionStore |

## Key Classes

### DDD (`pydomain.ddd`)

- `AggregateRoot` — Base class for aggregate roots with event collection and optimistic concurrency
- `Entity` — Base class for entities with identity
- `ValueObject` — Immutable value object base (Pydantic model with `frozen=True`)
- `DomainEvent` — Base class for domain events with tracing fields
- `Repository` — Generic repository protocol (`Repository[T, TId]`)
- `Specification` — Specification pattern for query criteria
- `DomainError` — Base exception for domain rule violations

### CQRS (`pydomain.cqrs`)

- `Command[TResult]` — Command base class parameterized by result type
- `Query[TResult]` — Query base class parameterized by result type
- `CommandResult` / `QueryResult` — Typed result base classes
- `CommandBus` — Routes commands to single handler with behavior pipeline
- `QueryBus` — Routes queries to single handler
- `AbstractUnitOfWork` — Transaction boundary with event collection and commit/rollback
- `PipelineBehavior` — Protocol for cross-cutting behaviors (logging, idempotency, locking)

### Event Sourcing (`pydomain.es`)

- `EventSourcedAggregateRoot` — Aggregate rebuilt from event history
- `EventStore` — Persists and replays event streams
- `EventStream` — Ordered sequence of events with version
- `EventSourcedRepository` — Repository that saves aggregate events to event store
- `Snapshot` / `SnapshotStore` — Optimize aggregate rebuild with snapshots
- `EventSourcedProjection` — Projection that consumes events to build read models

### Sagas (`pydomain.cqrs.saga`)

- `Saga[SagaState]` — Base class with declarative `on()` and imperative handler patterns
- `SagaState` — Mutable aggregate root tracking full saga lifecycle
- `SagaManager` — Orchestrates load → handle → save → dispatch pipeline
- `SagaRegistry` — Maps event types to saga classes
- `SagaRepository` — Persistence protocol for saga state

### Infrastructure (`pydomain.infrastructure`)

- `Application` — Configured entry point wrapping MessageBus
- `bootstrap()` — Dependency injection composition root
- `MessageBus` — Unified bus dispatching commands and queries to respective buses
- `EventRegistry` — Registers event types for serialization/deserialization
- `MessageBroker` — Protocol for publishing integration events to external systems
- `MessageSubscriber` — Protocol for consuming messages from external brokers
- `InboundEventGateway` — Manages external consumer lines with lifecycle control

### Testing (`pydomain.testing`)

- `FakeRepository` — In-memory `Repository` with optimistic concurrency
- `FakeUnitOfWork` — In-memory `AbstractUnitOfWork`
- `FakeEventStore` — In-memory `EventStore` with deduplication
- `FakeSnapshotStore` — In-memory `SnapshotStore`
- `FakeSagaRepository` — In-memory `SagaRepository` with deep copy isolation
- `FakeCheckpointStore` — In-memory `CheckpointStore`
- `FakeLockProvider` — In-memory `LockProvider`
- `FakeProcessedCommandStore` — In-memory `ProcessedCommandStore`
- `InMemoryMessageBroker` — Captures published integration events
- `InMemoryMessageSubscriber` — Records subscriptions, simulates incoming messages
- `InMemoryProjectionStore` — In-memory projection state persistence

## Navigation

- [Introduction](../introduction.md) — project overview and design philosophy
- [Getting Started](../getting-started/) — installation and quickstart
- [Concepts](../concepts/) — understanding-oriented documentation
- [How-To Guides](../how-to/) — task-oriented documentation
- [Recipes](../how-to/recipes/) — end-to-end integration patterns
