# 3. Context View

This section describes the library's external boundaries — what it depends on, what depends on it, how users integrate with it, and how the system behaves at its edges.

## 3.1 System Context Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│                     Library User's Application                      │
│                                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐ │
│  │ Domain Model│  │Infrastructure│  │   Web Framework / CLI      │ │
│  │  (user code)│  │  Adapters    │  │   (FastAPI, Starlette…)    │ │
│  │             │  │ (user code)  │  │                            │ │
│  │ • Entities  │  │ • SQLAlchemy │  │ • HTTP routes call         │ │
│  │ • VOs       │  │ • RabbitMQ   │  │   app.dispatch(cmd)       │ │
│  │ • Aggregates│  │ • MongoDB    │  │ • Map requests → commands │ │
│  │ • Events    │  │ • PostgreSQL │  │ • Map results → responses │ │
│  └──────┬──────┘  └──────┬───────┘  └────────────┬───────────────┘ │
│         │                │                        │                 │
└─────────┼────────────────┼────────────────────────┼─────────────────┘
          │                │                        │
          │  inherits      │  implements            │  calls dispatch()
          ▼                ▼                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          pydomain Library                           │
│                                                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐          │
│  │   ddd    │ │   cqrs   │ │    es    │ │infrastructure│          │
│  │          │ │          │ │          │ │              │          │
│  │ Entity   │ │ Command  │ │ Event    │ │ bootstrap()  │          │
│  │ ValueObj │ │ Query    │ │ Sourced  │ │ Application  │          │
│  │ Aggregate│ │ Bus      │ │ Aggregate│ │ MessageBus   │          │
│  │ DomainEv │ │ UoW      │ │ Event    │ │ EventRegistry│          │
│  │ Spec     │ │ Pipeline │ │ Store    │ │ Subscription │          │
│  │ Factory  │ │ Saga     │ │ Snapshot │ │ MessageBroker│          │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘          │
│                                                                     │
│  ┌──────────────┐                                                   │
│  │   testing    │  (consumed only by test code)                     │
│  │              │                                                   │
│  │ Fake*        │                                                   │
│  │ InMemory*    │                                                   │
│  └──────────────┘                                                   │
│                                                                     │
└─────────────────────────────┬───────────────────────────────────────┘
                              │
                  depends on  │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       External Dependencies                         │
│                                                                     │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │   Pydantic v2    │  │   uuid-utils     │  │  Python stdlib   │  │
│  │                  │  │                  │  │                  │  │
│  │ • BaseModel      │  │ • uuid7()        │  │ • uuid.UUID      │  │
│  │ • ConfigDict     │  │                  │  │ • datetime       │  │
│  │ • PrivateAttr    │  │                  │  │ • typing         │  │
│  │ • model_dump()   │  │                  │  │ • abc            │  │
│  │ • model_validate │  │                  │  │ • dataclasses    │  │
│  │ • Field          │  │                  │  │ • logging        │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## 3.2 External Interfaces

The library exposes its public API through five Python packages. Users interact with the library via three mechanisms: **inheritance** (base classes), **structural subtyping** (Protocol interfaces), and **composition** (bootstrap wiring).

### 3.2.1 Base Classes — Inheritance Interface

Users extend these library classes to define their domain model:

| Base Class | Module | User Extends With | Purpose |
|-----------|--------|-------------------|---------|
| `Entity[TId]` | `pydomain.ddd` | `class Order(Entity[UUID])` | Identity-based domain object with auto-generated ID |
| `ValueObject` | `pydomain.ddd` | `class Money(ValueObject)` | Immutable, attribute-defined value |
| `AggregateRoot[TId]` | `pydomain.ddd` | `class Order(AggregateRoot[UUID])` | Consistency boundary with domain event collection |
| `DomainEvent` | `pydomain.ddd` | `class OrderPlaced(DomainEvent)` | Immutable record of a domain fact |
| `Command[TResult]` | `pydomain.cqrs` | `class PlaceOrder(Command[PlaceOrderResult])` | Typed intent message (imperative mood) |
| `Query[TResult]` | `pydomain.cqrs` | `class GetOrder(Query[GetOrderResult])` | Typed read request (no side effects) |
| `CommandResult` | `pydomain.cqrs` | `class PlaceOrderResult(CommandResult)` | Typed command return value |
| `QueryResult` | `pydomain.cqrs` | `class GetOrderResult(QueryResult)` | Typed query return value |
| `IntegrationEvent` | `pydomain.cqrs` | `class OrderCreated(IntegrationEvent)` | Cross-boundary event (primitives only) |
| `EventSourcedAggregateRoot[TId]` | `pydomain.es` | `class Order(EventSourcedAggregateRoot[UUID])` | Event-sourced aggregate with `_when()` dispatch |
| `EventSourcedProjection` | `pydomain.es` | `class OrderProjection(EventSourcedProjection)` | ABC for `_when_*` event handlers with checkpoint tracking |
| `Saga[S]` | `pydomain.cqrs` | `class OrderSaga(Saga[OrderState])` | Orchestrator with `on()` DSL and compensation |

### 3.2.2 Protocol Interfaces — Structural Subtyping

Users implement these `Protocol` interfaces to connect their infrastructure. No library base class inheritance required — any class satisfying the method signatures is compatible.

| Protocol | Module | User Implements Against | Purpose |
|----------|--------|------------------------|---------|
| `Repository[T, TId]` | `pydomain.ddd` | SQLAlchemy, MongoDB, in-memory | Aggregate persistence (add, get_by_id, save) |
| `EventStore` | `pydomain.es` | PostgreSQL, DynamoDB, in-memory | Append-only event stream storage |
| `SnapshotStore` | `pydomain.es` | S3, PostgreSQL, in-memory | Aggregate state snapshots for performance |
| `CheckpointStore` | `pydomain.es` | Redis, PostgreSQL, in-memory | Subscription position tracking |
| `MessageBroker` | `pydomain.infrastructure` | RabbitMQ, Kafka, in-memory | Integration event publishing |
| `ProcessedCommandStore` | `pydomain.cqrs` | Redis, PostgreSQL, in-memory | Idempotent command deduplication |
| `LockProvider` | `pydomain.cqrs` | Redis, ZooKeeper, in-memory | Distributed aggregate locking |
| `LockKeyResolver` | `pydomain.cqrs` | Custom key generation | Lock key derivation from commands |
| `SagaRepository` | `pydomain.cqrs` | SQLAlchemy, in-memory | Saga state persistence |
| `UnitOfWork` | `pydomain.cqrs` | SQLAlchemy session wrapper | Transaction boundary + event collection |
| `CommandHandler[T, R]` | `pydomain.cqrs` | Handler functions/classes | Command → result execution |
| `QueryHandler[T, R]` | `pydomain.cqrs` | Handler functions/classes | Query → result execution |
| `EventHandler[T]` | `pydomain.cqrs` | Handler functions/classes | Domain event side effects |
| `PipelineBehavior` | `pydomain.cqrs` | Middleware classes | Cross-cutting command/query concerns |
| `ProjectionStore` | `pydomain.cqrs` | PostgreSQL, MongoDB, in-memory | Read model storage |
| `SnapshotPolicy` | `pydomain.es` | Custom snapshot triggers | When to snapshot aggregates |
| `IdGenerator[TId]` | `pydomain.ddd` | Custom ID generation | Replace default UUIDv7 generator with any scheme (Snowflake, ULID, etc.) |

### 3.2.3 Composition Interface — bootstrap()

The `bootstrap()` function in `pydomain.infrastructure` is the **composition root**. Production code calls it with real adapters; test code calls it with fakes from `pydomain.testing`:

```python
# Production wiring
app = await bootstrap(
    event_store=PostgresEventStore(dsn=...),
    snapshot_store=S3SnapshotStore(bucket=...),
    message_broker=RabbitMQBroker(url=...),
    event_registry=registry,
)
await app.dispatch(PlaceOrder(order_id=..., items=[...]))

# Test wiring
app = await bootstrap(
    event_store=FakeEventStore(),
    snapshot_store=FakeSnapshotStore(),
)
result = await app.dispatch(PlaceOrder(order_id=..., items=[...]))
assert isinstance(result, PlaceOrderResult)
```

`bootstrap()` returns an `Application` object with:
- `dispatch(message)` — unified command/query dispatch through the configured `MessageBus`
- `snapshot_store` — access to the configured snapshot store (for repository wiring)

## 3.3 External Dependencies

### 3.3.1 Runtime Dependencies

The library has exactly **two** runtime dependencies. No database drivers, no message broker clients, no DI containers.

| Dependency | Version | Role in the Library |
|-----------|---------|---------------------|
| `pydantic` | ≥ 2.7 | Universal base for all domain concepts. Provides `BaseModel`, `ConfigDict`, `PrivateAttr`, `Field`, `model_dump()`, `model_validate()`, `@field_validator`, `@model_validator`, JSON Schema generation, Rust-core validation performance. |
| `uuid-utils` | ≥ 0.9 | UUIDv7 generation (`uuid7()`) in the default `Uuid7Generator`. Produces time-ordered, sortable identifiers used for `event_id`, `command_id`, `query_id`, and entity `id` fields. |

### 3.3.2 Standard Library Usage

The library additionally uses these standard library modules — no installation required:

| Module | Used For |
|--------|----------|
| `uuid.UUID` | Identity type for entities, events, commands, queries |
| `datetime` | `occurred_at` timestamps on events and integration events |
| `typing` | `Protocol`, `ClassVar`, `Annotated`, generics (PEP 695) |
| `abc.ABC` / `abc.abstractmethod` | `AbstractUnitOfWork`, `EventSourcedProjection`, `SubscriptionRunner` — where shared behaviour is provided |
| `dataclasses` | `Subscription` dataclass, internal data structures |
| `logging` | Structured logging throughout all modules |
| `collections.abc` | `Sequence`, `Callable` type hints |
| `asyncio` | Event loop scheduling in `SubscriptionRunner` |
| `secrets` | Random token generation for test utilities |

## 3.4 What the Library Does NOT Provide

The library explicitly avoids providing concrete infrastructure implementations. These are the user's responsibility:

| Concern | What User Provides | Why |
|---------|-------------------|-----|
| Database persistence | `Repository` impl backed by SQLAlchemy, MongoDB, etc. | The library is infrastructure-agnostic |
| Event storage | `EventStore` impl backed by PostgreSQL, DynamoDB, etc. | Storage technology is a deployment choice |
| Message broker | `MessageBroker` impl backed by RabbitMQ, Kafka, etc. | Broker choice depends on operational context |
| Web framework integration | HTTP routes that call `app.dispatch()` | FastAPI, Starlette, Flask — user's choice |
| DI container | Manual wiring or `bootstrap()` | No hidden service locator or magic resolution |
| HTTP/API serialization | Pydantic models serialize to JSON natively | No custom REST layer needed |

## 3.5 Integration Patterns

### Pattern 1: Domain Model Only (Level 1–2)

The simplest integration. User imports base classes, defines domain model, implements a `Repository` against their database:

```
User Application
    │
    ├── from pydomain.ddd import Entity, ValueObject, AggregateRoot, DomainEvent
    ├── class Order(AggregateRoot[UUID]): ...
    ├── class SqlAlchemyOrderRepository: ...  # implements Repository protocol
    └── Uses repository directly in service layer
```

No message bus, no unit of work, no event sourcing. Just tactical DDD primitives.

### Pattern 2: CQRS with Message Bus (Level 3)

User adds the CQRS pipeline — commands, queries, buses, unit of work, and event handlers:

```
User Application
    │
    ├── from pydomain.cqrs import Command, Query, CommandBus, QueryBus
    ├── from pydomain.cqrs import UnitOfWork, PipelineBehavior
    ├── from pydomain.infrastructure import MessageBus, bootstrap
    │
    ├── class PlaceOrder(Command[PlaceOrderResult]): ...
    ├── class GetOrder(Query[OrderReadModel]): ...
    ├── async def handle_place_order(cmd, uow): ...
    │
    ├── app = await bootstrap(handlers=[...])
    └── result = await app.dispatch(PlaceOrder(...))
```

Commands mutate state through the Unit of Work; queries return read models. Domain events are published after commit via the `MessageBus`. Pipeline behaviors (logging, validation, idempotency) wrap handlers in an onion pattern.

### Pattern 3: Event Sourcing (Level 4)

User replaces state-based repositories with event-sourced ones. Aggregates change their base class to `EventSourcedAggregateRoot`:

```
User Application
    │
    ├── from pydomain.es import EventSourcedAggregateRoot, EventStore
    ├── from pydomain.es import EventSourcedRepository, EventSourcedProjection
    │
    ├── class Order(EventSourcedAggregateRoot[UUID]):   # was AggregateRoot
    │       def _when(self, event): ...                  # apply events
    │
    ├── class SqlEventStore(EventStore): ...             # user's DB adapter
    ├── class OrderProjection(EventSourcedProjection): ...
    └── app = await bootstrap(event_store=SqlEventStore(...))
```

The event log becomes the source of truth. Current state is derived by replaying events. Projections build read models from the event stream.

### Pattern 4: Advanced Event Sourcing (Level 5)

User adds snapshots, upcasters, subscriptions, and saga orchestration:

```
User Application
    │
    ├── from pydomain.es import SnapshotStore, Upcaster, SubscriptionRunner
    ├── from pydomain.cqrs.saga import Saga, SagaManager
    ├── from pydomain.infrastructure import MessageBroker
    │
    ├── class OrderSnapshotStore(SnapshotStore): ...
    ├── class OrderCreatedV1ToV2(Upcaster): ...
    ├── class OrderSaga(Saga[OrderSagaState]): ...
    │
    ├── app = await bootstrap(
    │       event_store=...,
    │       snapshot_store=OrderSnapshotStore(...),
    │       message_broker=RabbitMQBroker(...),
    │   )
    └── SubscriptionRunner(app).start()   # catch-up projections
```

Snapshots avoid replaying the full event stream. Upcasters handle event schema evolution. Sagas orchestrate long-running processes across aggregates. Subscriptions deliver events to projections or external systems via a `MessageBroker`.

### Pattern 5: Testing with Fakes

At every level, the `pydomain.testing` module provides in-memory fakes for fast, deterministic unit tests:

```
User Tests
    │
    ├── from pydomain.testing import (
    │       FakeRepository, FakeUnitOfWork, FakeEventStore,
    │       FakeSnapshotStore, InMemoryMessageBroker,
    │   )
    │
    ├── app = await bootstrap(
    │       event_store=FakeEventStore(),
    │       snapshot_store=FakeSnapshotStore(),
    │   )
    │
    └── result = await app.dispatch(PlaceOrder(...))
    assert isinstance(result, PlaceOrderResult)
```

Fakes live in `pydomain.testing` — never mock what you don't own.

User wires through `bootstrap()`, registers handlers on the `MessageBus`:

```
User Application
    │
    ├── Domain model (inherits pydomain base classes)
    ├── Infrastructure adapters (implement pydomain Protocols)
    ├── bootstrap(event_store=..., message_broker=...) → Application
    ├── bus.register_command(PlaceOrder, handle_place_order, uow_factory)
    ├── bus.register_event_handler(OrderPlaced, send_confirmation_email)
    └── FastAPI route: result = await app.dispatch(PlaceOrder(...))
```

The `MessageBus` manages UoW lifecycle, event dispatch, and pipeline behaviors.

### Pattern 3: Test Code (All Levels)

Tests use fakes from `pydomain.testing` — no infrastructure required:

```
User Test Code
    │
    ├── from pydomain.testing import FakeRepository, FakeUnitOfWork, FakeEventStore
    ├── app = await bootstrap(event_store=FakeEventStore(), ...)
    ├── result = await app.dispatch(PlaceOrder(...))
    └── assert result.status == "placed"
```

All fakes are in-memory, deterministic, and fast (microsecond-scale).

## 3.6 Communication Channels

### 3.6.1 Internal: Sync Function Calls

All interaction between modules is **direct Python function/method calls**. There are no network calls, no IPC, no message queues internal to the library. The `MessageBus` dispatches events synchronously within the same process — it is not a networked service bus.

### 3.6.2 External: Async Protocol Boundaries

Every I/O boundary is expressed as an `async` method on a `Protocol`:

| Boundary | Protocol | Async Methods |
|----------|----------|---------------|
| Aggregate persistence | `Repository` | `async save()`, `async get_by_id()` |
| Event stream storage | `EventStore` | `async append_to_stream()`, `async read_stream()`, `async read_all()` |
| Snapshot storage | `SnapshotStore` | `async save()`, `async load()` |
| Integration event publishing | `MessageBroker` | `async publish()`, `async start()`, `async stop()` |
| Subscription checkpointing | `CheckpointStore` | `async get()`, `async save()` |
| Idempotency tracking | `ProcessedCommandStore` | `async check_and_mark()` |
| Distributed locking | `LockProvider` | `async acquire()`, `async release()` |
| Saga state persistence | `SagaRepository` | `async load()`, `async save()` |

All protocol methods are `async` — the library never blocks synchronously on I/O.

## 3.7 Context Summary

| Aspect | Detail |
|--------|--------|
| **What depends on the library** | User applications (Python projects that `pip install pydomain`) |
| **What the library depends on** | Pydantic v2, uuid-utils, Python ≥ 3.12 stdlib |
| **Integration mechanism** | Inheritance (base classes) + structural subtyping (`Protocol`) + composition (`bootstrap()`) |
| **I/O boundaries** | All expressed as `async` methods on `Protocol` interfaces |
| **Internal communication** | Direct function calls within a single process |
| **What the library does NOT provide** | Database drivers, message broker clients, web framework adapters, DI containers |
