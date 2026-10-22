# API Reference

> **Adoption Level:** All levels

This page is a hand-kept index of pydomain's public modules and classes. A generated reference built from the source docstrings is planned; until then the docstrings in `src/pydomain/` are the detailed reference, and the lists below name what each package exports (its `__all__`). The lists are a selection, not every name.

## Module Index

| Module | Package | Description |
|--------|---------|-------------|
| **DDD** | `pydomain.ddd` | Entities, Value Objects, Aggregates, Domain Events, Repositories, Specifications, Factories, Domain Services |
| **CQRS** | `pydomain.cqrs` | Commands, Queries, Command Bus, Query Bus, Handlers, Pipeline Behaviors, Unit of Work, Idempotency, Locking, Outbox, Integration Events, Projections |
| **Event Sourcing** | `pydomain.es` | Event Store, Event Stream, Event-Sourced Aggregates, Repositories, Projections, Snapshots, Upcasters, Subscriptions, Checkpoints |
| **Sagas** | `pydomain.cqrs.saga` | Saga, SagaState, SagaManager, SagaRegistry, SagaRepository, Compensation, Hydration, Pruning |
| **Infrastructure** | `pydomain.infrastructure` | Bootstrap, Message Bus, Event Registry, Message Broker, Message Subscriber, Inbound Event Gateway, Outbound Event Gateway |
| **Testing** | `pydomain.testing` | FakeRepository, FakeUnitOfWork, FakeEventStore, FakeSnapshotStore, FakeSagaRepository, FakeCheckpointStore, FakeLockProvider, FakeProcessedMessageStore, FakeOutboxStore, InMemoryMessageBroker, InMemoryMessageSubscriber, InMemoryProjectionStore |

## Key Classes

### DDD (`pydomain.ddd`)

- `AggregateRoot` — Base class for aggregate roots with event collection and optimistic concurrency
- `Entity` — Base class for entities with identity
- `DomainService`, `Factory`, `ReconstitutionFactory` — Domain services and factories
- `IdGenerator`, `Uuid7Generator` — Identity generation (`configure()` is per class)
- `ValueObject` — Immutable value object base (Pydantic model with `frozen=True`)
- `DomainEvent` — Base class for domain events with tracing fields
- `Repository` — Generic repository protocol (`Repository[T, TId]`)
- `Specification`, `AndSpecification`, `OrSpecification`, `NotSpecification` — Specification pattern for query criteria
- `DomainError`, `ConcurrencyError`, `SpecificationError` — Domain exceptions

### CQRS (`pydomain.cqrs`)

- `Command[TResult]` — Command base class parameterized by result type
- `Query[TResult]` — Query base class parameterized by result type
- `CommandResult` / `QueryResult` — Typed result base classes
- `CommandBus` — Routes commands to single handler with behavior pipeline
- `QueryBus` — Routes queries to single handler
- `UnitOfWork` — Protocol for the transaction boundary; the base class `AbstractUnitOfWork` (event collection, commit and rollback hooks) is in `pydomain.cqrs.unit_of_work` and exported from `pydomain`
- `EventBus` — Dispatches domain events to their handlers, isolating handler failures
- `PipelineBehavior` — Protocol for cross-cutting behaviors; built in: `LoggingBehavior`, `ValidationBehavior`, `IdempotencyBehavior`, `EventIdempotencyBehavior`, `AggregateLockingBehavior`
- `ProcessedMessageStore`, `LockProvider`, `LockKeyResolver` — Protocols behind idempotency and locking
- `IntegrationEvent` — Strict-JSON event for other systems
- `CommandExecutionError`, `LockReentryError`, `PipelineReentryError`, `UnitOfWorkAlreadyCommittedError` — CQRS exceptions

### Outbox (`pydomain.cqrs.outbox`)

- `OutboxEntry` — Frozen record of an integration event awaiting publication
- `OutboxStore` — Protocol for outbox persistence (`append`, `fetch_unpublished`, `mark_published`, `mark_failed`)
- `OutboundEventRegistry` — Maps domain event types to an integration class, topic, and pure translator
- `OutboxWriter` — Translates collected domain events and appends them to the outbox

The relay that drains the outbox, `OutboundEventGateway`, is in `pydomain.infrastructure`.

### Event Sourcing (`pydomain.es`)

- `EventSourcedAggregateRoot` — Aggregate rebuilt from event history
- `EventStore` — Persists and replays event streams
- `EventStream` — Ordered sequence of events with version
- `EventSourcedRepository` — Repository that saves aggregate events to event store
- `Snapshot` / `SnapshotStore` / `SnapshotPolicy` — Optimize aggregate rebuild with snapshots; `SnapshotRestoreError` when one no longer validates
- `EventUpcaster` / `UpcasterRegistry` — Upgrade stored events to the current schema version
- `CheckpointStore` — Persists how far a subscription has read
- `EventSourcedProjection` — Projection that consumes events to build read models

### Sagas (`pydomain.cqrs.saga`)

- `Saga[SagaState]` — Base class with declarative `on()` and imperative handler patterns
- `SagaState` — Mutable aggregate root tracking full saga lifecycle
- `SagaManager` — Orchestrates load → handle → save → dispatch pipeline
- `SagaRegistry` — Maps event types to saga classes
- `SagaRepository` — Persistence protocol for saga state
- `CommandDispatcher` — Protocol for what `SagaManager` sends commands through; pass the `MessageBus`

### Infrastructure (`pydomain.infrastructure`)

- `Application` — Configured entry point wrapping MessageBus
- `bootstrap()` — Dependency injection composition root
- `MessageBus` — Facade dispatching commands, queries and events to their buses
- `EventRegistry` — Registers event types for serialization/deserialization
- `MessageBroker` — Protocol for publishing integration events to external systems
- `MessageSubscriber` — Protocol for consuming messages from external brokers
- `InboundEventGateway` — Manages external consumer lines with lifecycle control
- `OutboundEventGateway` — Relay that drains the outbox and publishes through a `MessageBroker`
- `SubscriptionRunner` / `Subscription` — Catch-up subscriptions from an event store to projections

### Testing (`pydomain.testing`)

- `FakeRepository` — In-memory `Repository` with optimistic concurrency
- `FakeUnitOfWork` — In-memory `AbstractUnitOfWork`
- `FakeEventStore` — In-memory `EventStore` with deduplication
- `FakeSnapshotStore` — In-memory `SnapshotStore`
- `FakeSagaRepository` — In-memory `SagaRepository` with deep copy isolation
- `FakeCheckpointStore` — In-memory `CheckpointStore`
- `FakeLockProvider` — In-memory `LockProvider`
- `FakeProcessedMessageStore` — In-memory `ProcessedMessageStore`
- `FakeOutboxStore` — In-memory `OutboxStore` with claims, retries and dead letters
- `InMemoryMessageBroker` — Captures published integration events
- `InMemoryMessageSubscriber` — Records subscriptions, simulates incoming messages
- `InMemoryProjectionStore` — In-memory projection state persistence
- `pydomain.testing.contracts` — Contract suites (`RepositoryContract`, `EventStoreContract`, `OutboxStoreContract`, and the rest) to run against your own adapters; needs the `testing` extra

## Navigation

- [Introduction](../introduction.md) — project overview and design philosophy
- [Getting Started](../getting-started/) — installation and quickstart
- [Concepts](../concepts/) — understanding-oriented documentation
- [How-To Guides](../how-to/) — task-oriented documentation
- [Recipes](../how-to/recipes/) — end-to-end integration patterns
