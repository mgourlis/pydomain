# 5. Building Block View

This section decomposes the `pydomain` library into its constituent building blocks — the modules, classes, and interfaces that form its public API. The view follows a top-down whitebox approach: starting from the package boundary, drilling into each module, and cataloguing every public type with its role, relationships, and dependencies.

---

## 5.1 Level 1 — Package Whitebox

The library is a single installable package (`pydomain`) composed of **five modules** with a strict layered dependency graph. No module is independently installable — all five ship together.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            pydomain (package)                               │
│                                                                             │
│   ┌────────────────┐                                                        │
│   │  pydomain.ddd  │   ← Level 1: Tactical DDD primitives                  │
│   │                │     No internal dependencies                           │
│   └───────┬────────┘                                                        │
│           │                                                                 │
│     ┌─────┴──────────────────────┐                                          │
│     │                            │                                          │
│   ┌─┴──────────────┐  ┌──────────┴──────────┐                               │
│   │  pydomain.cqrs │  │   pydomain.es       │   ← Level 2 & 4              │
│   │                │  │                     │     Each depends on ddd only  │
│   └───────┬────────┘  └──────────┬──────────┘                               │
│           │                      │                                          │
│   ┌───────┴──────────────────────┴───────────┐                              │
│   │        pydomain.infrastructure           │   ← Level 3+5               │
│   │  (cross-cutting: bootstrap, MessageBus,  │     Depends on cqrs + es    │
│   │   MessageBroker, EventRegistry,          │                              │
│   │   Subscription)                          │                              │
│   └──────────────────────────────────────────┘                              │
│                                                                             │
│   ┌──────────────────────────────────────────┐                              │
│   │         pydomain.testing                 │   ← Test doubles            │
│   │  (Fake*, InMemory* — uses all modules)   │     Not imported at runtime │
│   └──────────────────────────────────────────┘                              │
│                                                                             │
│   ┌──────────────────────────────────────────┐                              │
│   │       pydomain.testing.contracts         │   ← Protocol contract       │
│   │  (*Contract suites — pytest, anyio)      │     suites (ADR-067)        │
│   └──────────────────────────────────────────┘     `testing` extra only    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

Dependency rules (violations = architecture bugs):

  testing.contracts ──→ ddd, cqrs, es, infrastructure, pytest, anyio
                        (never the fakes; no module imports it)
  testing ──→ ddd, cqrs, es, infrastructure
  infrastructure ──→ cqrs, es
  cqrs ──→ ddd
  es ──→ ddd
  ddd ──→ (pydantic, uuid-utils, stdlib only)
```

### Module dependency table

| Module | Depends on | Depends on | Used by |
|--------|-----------|-----------|---------|
| `pydomain.ddd` | `pydantic`, `uuid-utils`, stdlib | — | `cqrs`, `es`, `testing` |
| `pydomain.cqrs` | `pydomain.ddd` | — | `infrastructure`, `testing` |
| `pydomain.es` | `pydomain.ddd` | — | `infrastructure`, `testing` |
| `pydomain.infrastructure` | `pydomain.cqrs`, `pydomain.es` | — | `testing`, user application |
| `pydomain.testing` | All four modules above | — | User test code only |
| `pydomain.testing.contracts` | All four modules above, `pytest`, `anyio` (the `testing` extra) | — | Test code only: adopters' adapter tests and pydomain's `tests/testing/` |

### Adoption levels

Each module corresponds to an adoption level. Users may stop at any level:

| Level | Module | What you get |
|-------|--------|-------------|
| **Level 1** | `ddd` | Entity, ValueObject, AggregateRoot, DomainEvent, Repository, Specification, Factory, DomainService |
| **Level 2** | `+ cqrs` | Command, Query, CommandBus, QueryBus, typed results |
| **Level 3** | `+ infrastructure` | MessageBus (facade), UnitOfWork, PipelineBehavior, EventHandler |
| **Level 4** | `+ es` | EventSourcedAggregateRoot, EventStore, EventSourcedProjection |
| **Level 5** | `+ es` (advanced) | SnapshotStore, Upcaster, Subscription, Saga |

---

## 5.2 Level 2 — `pydomain.ddd` Module

**Purpose:** Tactical Domain-Driven Design primitives. Every class is a Pydantic v2 `BaseModel` (or a `Protocol`), providing built-in validation, serialization, and immutability control.

**Internal dependency rule:** Imports only `pydantic`, `uuid`, `datetime`, and the standard library. No infrastructure, no CQRS, no event-sourcing imports.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         pydomain.ddd                                     │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────┐            │
│  │                   Base Classes                           │            │
│  │                                                          │            │
│  │   ValueObject ◄────── Entity[TId] ◄── AggregateRoot[TId]│            │
│  │   (frozen=True)     (frozen=False)   (+ event buffer)    │            │
│  │                                                          │            │
│  │   DomainEvent        DomainService    Specification      │            │
│  │   (frozen=True)      (marker class)   (frozen ABC)       │            │
│  └──────────────────────────────────────────────────────────┘            │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────┐            │
│  │                  Protocols & Generators                  │            │
│  │                                                          │            │
│  │   Repository[T, TId]   Factory[T]   IdGenerator         │            │
│  │   (Protocol)           (Protocol)    (Protocol)          │            │
│  │                                      Uuid7Generator     │            │
│  └──────────────────────────────────────────────────────────┘            │
│                                                                          │
│  ┌──────────────────────┐                                                │
│  │     Exceptions       │                                                │
│  │  DomainError         │                                                │
│  │  ConcurrencyError    │                                                │
│  │  SpecificationError  │                                                │
│  └──────────────────────┘                                                │
└──────────────────────────────────────────────────────────────────────────┘
```

### 5.2.1 `Entity[TId]`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/entity.py` |
| **Base** | Pydantic `BaseModel` with `frozen=False` |
| **Type param** | `TId` — identity type (`UUID`, `int`, `str`, etc.) |
| **Fields** | `id: TId` (auto-generated when omitted), `version: int = 0` |
| **Equality** | By identity: two entities equal iff `type(self) is type(other) and self.id == other.id` |
| **Auto-ID** | All `Entity[TId]` subclasses get auto-generated IDs via pluggable `IdGenerator[TId]`. A runtime type guard verifies the generated value matches the declared `TId` — raises `DomainError` on mismatch. Default generator: `Uuid7Generator` (produces `UUID`). |
| **Extension point** | `Entity.configure(id_generator=...)` — call once at startup. Subclasses can override `_id_generator` individually. |

### 5.2.2 `ValueObject`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/value_object.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True` |
| **Mutability** | Immutable. Operations return new instances via `model_copy(update=...)` |
| **Equality** | Structural — two value objects with identical fields are equal (Pydantic default when frozen) |
| **No identity** | No `id` field. Defined entirely by attributes. |

### 5.2.3 `AggregateRoot[TId]`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/aggregate_root.py` |
| **Base** | `Entity[TId]` |
| **Event buffer** | `_pending_events: list[DomainEvent]` via `PrivateAttr(default_factory=list)` |
| **Key methods** | `_add_event(event)` — buffer a domain event; `pull_events()` — drain and return the buffer |
| **Pattern** | Publish-after-commit: aggregate records events during mutation; UnitOfWork drains them after successful commit |
| **Invariants** | Must hold after every public mutation method |

### 5.2.4 `DomainEvent`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/domain_event.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True` |
| **Fields** | `event_id: UUID` (UUIDv7), `occurred_at: datetime`, `event_version: int = 1`, `correlation_id: UUID \| None`, `causation_id: UUID \| None` |
| **Immutability** | `stamp()` returns a new frozen copy via `model_copy(update=...)` — original unchanged |
| **Naming** | Past tense in Ubiquitous Language (`OrderPlaced`, not `PlaceOrder`) |

### 5.2.5 `Repository[T, TId]` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/repository.py` |
| **Kind** | `@runtime_checkable` Protocol |
| **Type params** | `T: AggregateRoot`, `TId` |
| **Methods** | `save(aggregate, command_id?)` — persist with optimistic concurrency; `get_by_id(id) → T | None` — load (returns `None` if not found); `delete(id)` — idempotent removal; `pull_events() → list[DomainEvent]` — drain collected events |
| **Constraint** | One repository per aggregate root type. Only aggregate roots get repositories. |

### 5.2.6 `Specification`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/specification.py` |
| **Base** | `BaseModel` + `ABC` with `frozen=True` |
| **Key method** | `is_satisfied_by(obj) → bool` (abstract) |
| **Composition** | `and_()`, `or_()`, `not_()` — return composite specifications (`AndSpecification`, `OrSpecification`, `NotSpecification`) |
| **Subsumption** | `is_specialization_of()` / `is_generalization_of()` for subset reasoning |

### 5.2.7 `Factory[T]` and `ReconstitutionFactory[T]` *(Protocols)*

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/factory.py` |
| **Kind** | `@runtime_checkable` Protocols |
| **`Factory[T]`** | Encapsulates complex creation. Method: `create(**kwargs) → T`. No inheritance required. |
| **`ReconstitutionFactory[T]`** | Rebuilds domain objects from persisted state. Preserves existing identity (no new ID). |

### 5.2.8 `DomainService`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/domain_service.py` |
| **Kind** | Marker base class (`__slots__ = ()`) |
| **Purpose** | Stateless domain operations that span multiple aggregates. Signals "domain layer, no infrastructure." |
| **Guidance** | Prefer standalone functions when no class state is needed. |

### 5.2.9 `IdGenerator[TId]` *(Protocol)* and `Uuid7Generator`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/id_generator.py` |
| **`IdGenerator[TId]`** | Generic Protocol parameterized by the ID type it produces. Method: `generate() → TId`. Supports any ID scheme (UUID, Snowflake `int`, custom `str`). |
| **`Uuid7Generator`** | Default implementation — generates UUIDv7 via `uuid_utils.uuid7()`. Structurally conforms to `IdGenerator[UUID]`. |
| **Scope** | Shared across `Entity`, `DomainEvent`, `Command`, `Query` via `ClassVar[IdGenerator[Any]]`. Subclasses can override with a type-specific generator. |

### 5.2.10 Exceptions

| Exception | Inherits from | Purpose |
|-----------|---------------|---------|
| `DomainError` | `Exception` | Base for all domain-layer errors |
| `ConcurrencyError` | `DomainError` | Optimistic concurrency conflict on aggregate version |
| `SpecificationError` | `DomainError` | Specification-based validation failure |

---

## 5.3 Level 2 — `pydomain.cqrs` Module

**Purpose:** Command-Query Responsibility Segregation abstractions — commands, queries, buses, handlers, unit of work, pipeline behaviors, projections, integration events, and the saga subsystem.

**Internal dependency rule:** Depends on `pydomain.ddd` only. Does not import `pydomain.es` or `pydomain.infrastructure`.

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                            pydomain.cqrs                                      │
│                                                                               │
│  ┌─────────────────────────────────────────────┐  ┌─────────────────────────┐ │
│  │              Messages                        │  │     Results             │ │
│  │                                              │  │                         │ │
│  │  Command[TResult]    Query[TResult]          │  │  CommandResult          │ │
│  │  (frozen, intent)    (frozen, read-only)     │  │  EmptyCommandResult     │ │
│  │  IntegrationEvent                            │  │  QueryResult            │ │
│  │  (frozen, primitives only)                   │  │  (all frozen)           │ │
│  └──────────────────┬──────────────────────────┘  └─────────────────────────┘ │
│                     │                                                         │
│  ┌──────────────────┴──────────────────────────┐  ┌─────────────────────────┐ │
│  │              Handlers (Protocols)             │  │     Buses               │ │
│  │                                              │  │                         │ │
│  │  CommandHandler[TCommand, TResult]           │  │  CommandBus             │ │
│  │  QueryHandler[TQuery, TResult]               │  │  QueryBus               │ │
│  │  EventHandler[TEvent]                        │  │                         │ │
│  └──────────────────────────────────────────────┘  └─────────────────────────┘ │
│                                                                               │
│  ┌──────────────────────────────────────────────┐  ┌─────────────────────────┐ │
│  │           Pipeline & Middleware               │  │   Unit of Work          │ │
│  │                                              │  │                         │ │
│  │  MessageContext    MessagePipeline            │  │  UnitOfWork (Protocol)  │ │
│  │  PipelineBehavior (Protocol)                  │  │  AbstractUnitOfWork     │ │
│  │  MessageKind                                  │  │    (ABC)                │ │
│  │  IdempotencyBehavior                         │  │                         │ │
│  │  LockingBehavior                             │  │                         │ │
│  └──────────────────────────────────────────────┘  └─────────────────────────┘ │
│                                                                               │
│  ┌──────────────────────────────────────────────┐  ┌─────────────────────────┐ │
│  │       Cross-Cutting Protocols                 │  │     Projection          │ │
│  │                                              │  │                         │ │
│  │  ProcessedCommandStore (Protocol)             │  │  Projection[StateT]     │ │
│  │  LockProvider (Protocol)                      │  │    (Protocol)           │ │
│  │  LockKeyResolver (Protocol)                   │  │  ProjectionStore        │ │
│  │  DictLockKeyResolver                         │  │    (Protocol)           │ │
│  └──────────────────────────────────────────────┘  └─────────────────────────┘ │
│                                                                               │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │                          Saga Subsystem                                  │ │
│  │                                                                          │ │
│  │  Saga[S]                SagaState (AggregateRoot[UUID])                 │ │
│  │  SagaManager            SagaRegistry                                    │ │
│  │  SagaRepository (Protocol)     hydrate_command()                        │ │
│  │  SagaStatus             StepRecord          CompensationRecord          │ │
│  │  SagaError hierarchy                                                    │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                               │
│  ┌──────────────────────────────────────────────┐                            │
│  │              Exceptions                       │                            │
│  │  CQRSError (base)                             │                            │
│  │  HandlerAlreadyRegisteredError                │                            │
│  │  NoHandlerRegisteredError                     │                            │
│  │  CommandExecutionError                        │                            │
│  │  IdempotentCommandIgnored                     │                            │
│  └──────────────────────────────────────────────┘                            │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 5.3.1 Messages

#### `Command[TResult]`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/commands.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True`, `extra="forbid"` |
| **Type param** | `TResult: CommandResult` — what `dispatch()` returns |
| **Fields** | `command_id: UUID` (UUIDv7), `correlation_id: UUID \| None`, `causation_id: UUID \| None` |
| **Naming** | Imperative mood (`PlaceOrder`, `Allocate`) |
| **Rule** | One command → one aggregate mutation → one handler |

#### `Query[TResult]`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/queries.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True`, `extra="forbid"` |
| **Type param** | `TResult: QueryResult` |
| **Fields** | `query_id: UUID` (UUIDv7) |
| **Rule** | Read-only. No side effects. No Unit of Work. |

#### `IntegrationEvent`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/integration_events.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True` |
| **Constraint** | Fields must be primitives only (`str`, `int`, `float`, `bool`, `dict`, `list`, `None`) — enforced by `@model_validator` |
| **Fields** | `event_id: str`, `occurred_at: str` — auto-generated as strings for broker serialization |

### 5.3.2 Results

| Class | File | Purpose |
|-------|------|---------|
| `CommandResult` | `cqrs/commands.py` | Abstract base for command results. Frozen. |
| `EmptyCommandResult` | `cqrs/commands.py` | Void-style result. No data. |
| `QueryResult` | `cqrs/queries.py` | Abstract base for query results. Frozen. |

### 5.3.3 Handlers *(Protocols)*

| Protocol | File | Signature | Notes |
|----------|------|-----------|-------|
| `CommandHandler[TCommand, TResult]` | `cqrs/handlers.py` | `async def __call__(command, uow) → TResult` | Receives `UnitOfWork`. Must not call `commit()`. |
| `QueryHandler[TQuery, TResult]` | `cqrs/handlers.py` | `async def __call__(query) → TResult` | No UoW. Read-only. |
| `EventHandler[TEvent]` | `cqrs/handlers.py` | `async def __call__(event) → None` | Fire-and-forget. Multiple handlers per event type. Fail independently. |

### 5.3.4 Buses

#### `CommandBus`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/command_bus.py` |
| **Registration** | `register(command_type, handler, uow_factory, behaviors?)` — one handler per command type; raises on duplicate |
| **Dispatch** | `dispatch(command) → TResult` — creates UoW, runs pipeline, collects events |
| **Pipeline** | Wraps handler in `MessagePipeline` with registered `PipelineBehavior`s (onion pattern) |
| **Error** | `NoHandlerRegisteredError` if no handler; handler exceptions propagate |

#### `QueryBus`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/query_bus.py` |
| **Registration** | `register(query_type, handler, behaviors?)` — one handler per query type |
| **Dispatch** | `dispatch(query) → TResult` — no UoW, no events |
| **Difference from CommandBus** | No `UnitOfWork`. No event collection. No side effects. |

### 5.3.5 Pipeline Behavior Middleware

#### `PipelineBehavior` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/behaviors.py` |
| **Method** | `async def handle(ctx: MessageContext, next: NextHandler) → Any` |
| **Pattern** | Onion (decorator): runs before `next()` and after it returns |

#### `MessageContext`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/behaviors.py` |
| **Kind** | Mutable `dataclass` |
| **Fields** | `message`, `handler`, `kind` (`COMMAND`/`QUERY`/`EVENT`), `uow`, `correlation_id`, `causation_id`, `metadata`, `new_events` |

#### Built-in behaviors

| Behavior | File | Concern | Mechanism |
|----------|------|---------|-----------|
| `IdempotencyBehavior` | `cqrs/behaviors.py` | Duplicate command rejection | Checks `ProcessedCommandStore`; returns cached result if found |
| `LockingBehavior` | `cqrs/behaviors.py` | Concurrency control | Resolves lock key via `LockKeyResolver`; acquires via `LockProvider` |

### 5.3.6 Cross-Cutting Protocols

| Protocol | File | Methods | Purpose |
|----------|------|---------|---------|
| `ProcessedCommandStore` | `cqrs/idempotency.py` | `get(command_id)`, `set(command_id, result)`, `contains(command_id)` | Tracks processed command IDs for idempotency |
| `LockProvider` | `cqrs/locking.py` | `acquire(key)`, `release(key)` | Named lock acquisition for concurrency control |
| `LockKeyResolver` | `cqrs/locking.py` | `resolve(message) → list[str]` | Derives lock keys from messages; empty list = no lock |
| `DictLockKeyResolver` | `cqrs/locking.py` | Registry-based `register(message_type, key_fn)` | Maps message types to key-extraction functions |

### 5.3.7 Unit of Work

#### `UnitOfWork` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/unit_of_work.py` |
| **Kind** | `@runtime_checkable` Protocol |
| **Methods** | `__aenter__`, `__aexit__`, `commit()`, `rollback()`, `collect_events()` |
| **Semantics** | Publish-after-commit: events stamped with `correlation_id`/`causation_id` after successful commit |

#### `AbstractUnitOfWork` *(ABC)*

| Aspect | Detail |
|--------|--------|
| **Base** | `ABC` + `UnitOfWork` |
| **Provides** | Full commit/rollback lifecycle, event stamping, extension hooks |
| **Subclass must** | Implement `_commit()` and `_rollback()` |
| **Extension hooks** | `_on_post_commit()` for outbox writes |

### 5.3.8 Projection

#### `Projection[StateT]` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/projection.py` |
| **Kind** | `@runtime_checkable` Protocol |
| **Type param** | `StateT` — read model state type |
| **Methods** | `apply(event)` — apply a single event; `rebuild(events)` — rebuild from scratch |
| **Pattern** | Left-fold: `current_state + event → new_state` |

#### `ProjectionStore` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/projection.py` |
| **Purpose** | Persistence contract for projection read models |
| **Methods** | `load(projection_name, key)`, `save(projection_name, key, state)` |

### 5.3.9 Saga Subsystem

The saga subsystem is a self-contained package within `cqrs/saga/` providing explicit state machine orchestration for long-running business processes.

```
cqrs/saga/
├── saga.py           Saga[S] base class with on() DSL
├── state.py          SagaState, SagaStatus, StepRecord, CompensationRecord
├── manager.py        SagaManager — load → handle → save → dispatch
├── registry.py       SagaRegistry — event type → saga class mapping
├── repository.py     SagaRepository (Protocol) — saga state persistence
├── hydration.py      hydrate_command() — reconstruct commands from serialized data
└── exceptions.py     SagaError hierarchy
```

#### `Saga[S]`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/saga/saga.py` |
| **Type param** | `S: SagaState` |
| **Class vars** | `state_class: type[SagaState]`, `listens_to: list[type[DomainEvent]]` |
| **DSL** | `self.on(EventType, send=lambda e: Command(...), step="...", compensate=lambda e: Command(...))` |
| **Entry point** | `handle(event)` — idempotent, skips already-processed events |

#### `SagaState`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/saga/state.py` |
| **Base** | `AggregateRoot[UUID]` — carries `version` for optimistic concurrency and `_pending_events` |
| **Status enum** | `PENDING → RUNNING → SUSPENDED → COMPLETED / FAILED → COMPENSATING → COMPENSATED` |
| **Fields** | `saga_type`, `current_step`, `step_history`, `processed_event_ids`, `pending_commands`, `compensation_stack` |
| **Memory bounds** | `max_processed_events`, `max_step_history` caps; `prune_history()` for explicit cleanup |

#### `SagaManager`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/saga/manager.py` |
| **Dependencies** | `SagaRepository`, `SagaRegistry`, `CommandBus` |
| **Lifecycle** | Load/create state → instantiate saga → `handle(event)` → save state → dispatch pending commands |
| **Integration** | `bind_to(event_dispatcher)` — registers itself as an event handler |

#### `SagaRepository` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/saga/repository.py` |
| **Methods** | `save(state)`, `get_by_id(id)`, `find_by_correlation_id(correlation_id, saga_type)`, `find_stalled_sagas(limit)`, `find_suspended_sagas(limit)`, `find_expired_suspended_sagas(limit)`, `pull_events()` |

### 5.3.10 Exceptions

| Exception | Inherits from | Purpose |
|-----------|---------------|---------|
| `CQRSError` | `DomainError` | Base for all CQRS-layer errors |
| `HandlerAlreadyRegisteredError` | `CQRSError` | Duplicate handler registration |
| `NoHandlerRegisteredError` | `CQRSError` | Dispatch with no registered handler |
| `CommandExecutionError` | `CQRSError` | Handler exception wrapper (carries failed command) |
| `IdempotentCommandIgnored` | `CQRSError` | Duplicate command detected and ignored |

---

## 5.4 Level 2 — `pydomain.es` Module

**Purpose:** Event Sourcing building blocks — event-sourced aggregates, event store, snapshots, upcasting, checkpoints, and event-sourced projections.

**Internal dependency rule:** Depends on `pydomain.ddd` only. Does not import `pydomain.cqrs` or `pydomain.infrastructure`.

```
┌───────────────────────────────────────────────────────────────────────────┐
│                           pydomain.es                                     │
│                                                                           │
│  ┌───────────────────────────────────────────────────────────┐            │
│  │                Event-Sourced Aggregate                     │            │
│  │                                                           │            │
│  │  EventSourcedAggregateRoot[TId]                           │            │
│  │    (extends AggregateRoot[TId])                           │            │
│  │    _apply(event)  _replay(event)  _when(event)            │            │
│  │    _take_snapshot()                                       │            │
│  └───────────────────────────────────────────────────────────┘            │
│                                                                           │
│  ┌──────────────────────────┐  ┌──────────────────────────────────┐      │
│  │    Event Persistence      │  │         Snapshots                │      │
│  │                           │  │                                  │      │
│  │  EventStore (Protocol)   │  │  Snapshot (frozen VO)            │      │
│  │  EventStream (frozen VO) │  │  SnapshotStore (Protocol)       │      │
│  │  EventSourcedRepository  │  │  SnapshotPolicy (Protocol)      │      │
│  │    [T, TId]              │  │  SnapshotThresholdPolicy        │      │
│  └──────────────────────────┘  └──────────────────────────────────┘      │
│                                                                           │
│  ┌──────────────────────────┐  ┌──────────────────────────────────┐      │
│  │     Event Versioning      │  │        Projections               │      │
│  │                           │  │                                  │      │
│  │  EventUpcaster            │  │  EventSourcedProjection (ABC)   │      │
│  │  UpcasterRegistry         │  │    _when_* dispatch             │      │
│  │                           │  │    checkpoint tracking           │      │
│  └──────────────────────────┘  └──────────────────────────────────┘      │
│                                                                           │
│  ┌──────────────────────────┐  ┌──────────────────────────────────┐      │
│  │     Checkpoint Store      │  │        Exceptions                │      │
│  │                           │  │                                  │      │
│  │  CheckpointStore          │  │  StreamNotFoundError             │      │
│  │    (Protocol)             │  │  UpcastError                     │      │
│  │                           │  │  DuplicateCommandError           │      │
│  └──────────────────────────┘  └──────────────────────────────────┘      │
└───────────────────────────────────────────────────────────────────────────┘
```

### 5.4.1 `EventSourcedAggregateRoot[TId]`

| Aspect | Detail |
|--------|--------|
| **File** | `es/aggregate.py` |
| **Base** | `AggregateRoot[TId]` (from `ddd`) |
| **Key difference from AggregateRoot** | Mutates state via events only: call `_apply(event)` instead of direct field mutation |
| **Abstract method** | `_when(event)` — subclasses dispatch by `isinstance` to update fields |
| **`_apply(event)`** | Calls `_when(event)` → `_add_event(event)` → increments `self.version` |
| **`_replay(event)`**** | Calls `_when(event)` → increments `self.version` — does NOT buffer events |
| **`_take_snapshot()`** | Serializes state via `model_dump(mode='python')` → returns `Snapshot` |

### 5.4.2 `EventStore` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `es/event_store.py` |
| **Methods** | `append_to_stream(aggregate_id, events, expected_version, command_id?)` — append with optimistic concurrency; `read_stream(aggregate_id, from_version?) → EventStream` — read by stream; `read_all(from_version?) → EventStream` — read global log |
| **Concurrency** | `append_to_stream` raises `ConcurrencyError` if `expected_version` doesn't match |
| **Idempotency** | When `command_id` is provided, should raise `DuplicateCommandError` on re-submission |

### 5.4.3 `EventStream`

| Aspect | Detail |
|--------|--------|
| **File** | `es/event_stream.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True` |
| **Fields** | `events: Sequence[DomainEvent]`, `version: int` |
| **Usage** | Returned by `EventStore.read_stream()` and `read_all()`. Immutable slice of the event log. |

### 5.4.4 `EventSourcedRepository[T, TId]`

| Aspect | Detail |
|--------|--------|
| **File** | `es/event_sourced_repository.py` |
| **Kind** | Concrete base class (not Protocol) — implements `Repository` contract via `EventStore` |
| **Constructor** | `EventSourcedRepository(event_store, aggregate_cls, snapshot_store?, snapshot_policy?)` |
| **`save(aggregate)`** | Pulls pending events → appends to stream with optimistic concurrency → optionally takes snapshot |
| **`get_by_id(id)`** | Reads event stream → optionally loads snapshot for faster hydration → replays events → returns aggregate |
| **`pull_events()`** | Drains internal event buffer for Unit of Work |

### 5.4.5 Snapshots

#### `Snapshot`

| Aspect | Detail |
|--------|--------|
| **File** | `es/snapshot.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True` |
| **Fields** | `aggregate_id: str`, `version: int`, `state: dict`, `created_at: datetime` |

#### `SnapshotStore` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `es/snapshot.py` |
| **Methods** | `load(aggregate_type, aggregate_id) → Snapshot \| None`; `save(snapshot) → None` |

#### `SnapshotPolicy` *(Protocol)* and `SnapshotThresholdPolicy`

| Aspect | Detail |
|--------|--------|
| **`SnapshotPolicy`** | `should_snapshot(aggregate_type, aggregate_id, current_version, pending_event_count) → bool` |
| **`SnapshotThresholdPolicy`** | Default implementation — snapshots every N events (configurable `threshold`, default 10). `threshold=0` → snapshot on every flush. |

### 5.4.6 Event Versioning

#### `EventUpcaster`

| Aspect | Detail |
|--------|--------|
| **File** | `es/upcasting.py` |
| **Class vars** | `source_type: str`, `source_version: int`, `target_version: int` |
| **Method** | `upcast(event: dict) → dict` — transforms payload; wraps errors in `UpcastError` |

#### `UpcasterRegistry`

| Aspect | Detail |
|--------|--------|
| **File** | `es/upcasting.py` |
| **Purpose** | Discovers and chains upcasters to migrate events across schema versions |
| **Method** | `register(upcaster)` — add an upcaster; `upcast(event_type, event_version, event_data) → dict` — apply chain |

### 5.4.7 `EventSourcedProjection` *(ABC)*

| Aspect | Detail |
|--------|--------|
| **File** | `es/projection.py` |
| **Base** | `ABC` |
| **Class vars** | `name: ClassVar[str]`, `version: ClassVar[int]` |
| **Convention** | Subclasses implement `_when_{EventTypeName}` methods; `handle(event)` dispatches by name |
| **Checkpoint** | Tracks integer position in event stream via `_checkpoint` property |
| **Difference from `Projection` (cqrs)** | `Projection` is a Protocol for read-model contracts; `EventSourcedProjection` is an ABC with checkpoint tracking and `_when_*` dispatch |

### 5.4.8 `CheckpointStore` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `es/checkpoint_store.py` |
| **Methods** | `load(subscription_id) → int` (returns 0 if none); `save(subscription_id, checkpoint)` |

### 5.4.9 Exceptions

| Exception | Inherits from | Purpose |
|-----------|---------------|---------|
| `StreamNotFoundError` | `DomainError` | Event stream does not exist for aggregate |
| `UpcastError` | `DomainError` | Upcaster transformation failed |
| `DuplicateCommandError` | `DomainError` | Command already processed for aggregate |

---

## 5.5 Level 2 — `pydomain.infrastructure` Module

**Purpose:** Cross-cutting composition and wiring — the MessageBus facade, message broker abstraction, event registry (serialization), subscription runner, and the bootstrap composition root.

**Internal dependency rule:** The only module that imports both `cqrs` and `es`. This is where the pieces meet.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                       pydomain.infrastructure                              │
│                                                                            │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │                       Composition Root                             │    │
│  │                                                                    │    │
│  │  bootstrap(event_store?, snapshot_store?, message_bus?,            │    │
│  │            message_broker?, event_registry?) → Application        │    │
│  │                                                                    │    │
│  │  Application                                                       │    │
│  │    dispatch(command | query) → result                             │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│  ┌──────────────────────────────┐  ┌───────────────────────────────────┐  │
│  │        Message Bus            │  │       Message Broker              │  │
│  │   (Level 3 facade)           │  │                                   │  │
│  │                              │  │  MessageBroker (Protocol)         │  │
│  │  MessageBus                  │  │  InMemoryMessageBroker            │  │
│  │    register_command()        │  │    publish(topic, event)          │  │
│  │    register_query()          │  │    start() / stop()               │  │
│  │    register_event()          │  │                                   │  │
│  │    dispatch(message)         │  └───────────────────────────────────┘  │
│  └──────────────────────────────┘                                         │
│                                                                            │
│  ┌──────────────────────────────┐  ┌───────────────────────────────────┐  │
│  │      Event Registry           │  │       Subscriptions               │  │
│  │                              │  │                                   │  │
│  │  EventRegistry               │  │  Subscription (dataclass)         │  │
│  │    register(event_class)     │  │  SubscriptionRunner (ABC)        │  │
│  │    resolve(type_name) → cls  │  │    add_subscription()             │  │
│  │    serialize(event) → dict   │  │    process_batch() (abstract)     │  │
│  │    deserialize(data) → event │  │    run() / stop()                 │  │
│  │  GenericDomainEvent          │  │                                   │  │
│  └──────────────────────────────┘  └───────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

### 5.5.1 `Application`

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/bootstrap.py` |
| **Purpose** | Configured application entry point wrapping a `MessageBus` |
| **Constructor** | `Application(message_bus, event_registry?, snapshot_store?)` |
| **Key method** | `dispatch(message: Command \| Query) → Any` — unified command/query dispatch |
| **Properties** | `snapshot_store` — exposes configured snapshot store to consumers |

### 5.5.2 `bootstrap()`

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/bootstrap.py` |
| **Signature** | `async bootstrap(event_store?, snapshot_store?, message_bus?, message_broker?, event_registry?) → Application` |
| **Purpose** | Dependency injection composition root. Wires together all components. Tests call with fakes; production calls with real adapters. |

### 5.5.3 `MessageBus`

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/message_bus.py` |
| **Kind** | Level 3 facade wrapping `CommandBus` + `QueryBus` + event dispatcher |
| **Registration** | `register_command(type, handler, uow_factory, behaviors?)`; `register_query(type, handler, behaviors?)`; `register_event(type, handler)` |
| **Dispatch** | `dispatch(message) → Any` — inspects type, routes to CommandBus or QueryBus |
| **Event dispatch** | After command dispatch, collected domain events are dispatched to registered `EventHandler`s |
| **Error handling** | Event handlers fail independently — caught and logged per handler, queue continues |

### 5.5.4 `MessageBroker` *(Protocol)* and `InMemoryMessageBroker`

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/message_broker.py` |
| **`MessageBroker`** | Protocol: `publish(topic, event)`, `start()`, `stop()` — for integration events |
| **`InMemoryMessageBroker`** | Test double — captures published events in a list for assertions |

### 5.5.5 `EventRegistry`

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/event_registry.py` |
| **Purpose** | Maps event type names ↔ Pydantic model classes for dynamic serialization/deserialization |
| **Methods** | `register(event_class)`, `resolve(type_name) → type`, `serialize(event) → dict`, `deserialize(data) → DomainEvent` |
| **Fallback** | Unregistered types deserialize as `GenericDomainEvent` (weak-schema mode) |
| **Upcasting** | Optionally wired to `UpcasterRegistry` for schema evolution on read |

### 5.5.6 Subscriptions

#### `Subscription`

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/subscription.py` |
| **Kind** | `dataclass` |
| **Fields** | `subscription_id: str`, `projection: EventSourcedProjection`, `event_types: tuple[type[DomainEvent], ...]` |

#### `SubscriptionRunner` *(ABC)*

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/subscription.py` |
| **Purpose** | Coordinates catch-up subscriptions from `EventStore` to projections |
| **Constructor** | `SubscriptionRunner(event_store, checkpoint_store, poll_interval_seconds?, failure_backoff_seconds?)` |
| **Abstract method** | `process_batch(events, subscription)` — defines how matching events are handled |
| **Lifecycle** | `add_subscription(subscription)`, `run()` (polling loop), `stop()` |
| **Pattern** | Reads global log from checkpoint → filters by event type → delegates to `process_batch` → saves checkpoint |

---

## 5.6 Level 2 — `pydomain.testing` Module

**Purpose:** Complete test doubles for every storage and messaging `Protocol`, and the contract suites that check them. Imported only by test code — never by the library itself or user application code.

```
┌────────────────────────────────────────────────────────────────────────┐
│                         pydomain.testing                               │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  DDD Fakes                                                       │  │
│  │                                                                  │  │
│  │  FakeRepository[T, TId]     in-memory dict-backed repository     │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  CQRS Fakes                                                      │  │
│  │                                                                  │  │
│  │  FakeUnitOfWork              in-memory AbstractUnitOfWork        │  │
│  │  FakeOutboxStore             records appends, marks, failures    │  │
│  │  FakeProcessedMessageStore   in-memory idempotency store         │  │
│  │  FakeLockProvider            one asyncio.Lock per key            │  │
│  │  InMemoryProjectionStore     dict-backed projection state        │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  ES Fakes                                                        │  │
│  │                                                                  │  │
│  │  FakeEventStore              dict-of-lists event store           │  │
│  │  FakeSnapshotStore           in-memory snapshot store            │  │
│  │  FakeCheckpointStore         in-memory checkpoint store          │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Saga Fakes                                                      │  │
│  │                                                                  │  │
│  │  FakeSagaRepository          in-memory saga state store          │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Infrastructure Fakes                                            │  │
│  │                                                                  │  │
│  │  InMemoryMessageBroker       captures published events           │  │
│  │  InMemoryMessageSubscriber   simulate_message() delivers         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  pydomain.testing.contracts                                      │  │
│  │                                                                  │  │
│  │  *Contract                   one pytest suite per Protocol       │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

### Test double catalogue

| Fake | File | Satisfies | Key features |
|------|------|-----------|-------------|
| `FakeRepository[T, TId]` | `testing/fake_repository.py` | `Repository[T, TId]` | Dict-backed; optimistic concurrency check on update; `save()` drains pending events for `pull_events()` |
| `FakeUnitOfWork` | `testing/fake_unit_of_work.py` | `UnitOfWork` | Subclasses `AbstractUnitOfWork`; takes one `repository` or a dict of `repositories`; records a rollback |
| `FakeEventStore` | `testing/fake_event_store.py` | `EventStore` | Dict-of-lists plus a global log; `append_to_stream`, `read_stream`, `read_all`; concurrency check; duplicate `command_id` detection |
| `FakeSnapshotStore` | `testing/fake_snapshot_store.py` | `SnapshotStore` | Dict-backed; `save`/`get` by aggregate type + ID |
| `FakeCheckpointStore` | `testing/fake_checkpoint_store.py` | `CheckpointStore` | Dict-backed; `load`/`save` by subscription ID |
| `FakeOutboxStore` | `testing/fake_outbox_store.py` | `OutboxStore` | Records appends, publish marks, failures and dead letters; injectable clock; simulated fetch and append failures |
| `FakeSagaRepository` | `testing/fake_saga_repository.py` | `SagaRepository` | Dict-backed; deep copies; `find_by_correlation_id`, `find_stalled_sagas`, `find_suspended_sagas`, `find_expired_suspended_sagas` |
| `FakeProcessedMessageStore` | `testing/fake_processed_message_store.py` | `ProcessedMessageStore` | Dict-backed; `get`/`set`/`contains`/`check_and_set` |
| `FakeLockProvider` | `testing/fake_lock_provider.py` | `LockProvider` | One `asyncio.Lock` per key; not reentrant |
| `InMemoryMessageBroker` | `testing/in_memory_message_broker.py` | `MessageBroker` | Captures published events in a list |
| `InMemoryMessageSubscriber` | `testing/in_memory_message_subscriber.py` | `MessageSubscriber` | Records subscriptions; `simulate_message()` calls the handler |
| `InMemoryProjectionStore` | `testing/in_memory_projection_store.py` | `ProjectionStore` | Dict-backed; `load`/`save` by projection ID |

**Design principle:** Fakes over mocks. Every fake behaves like the real thing without requiring infrastructure setup. Tests run in milliseconds.

### 5.6.1 `pydomain.testing.contracts`

| Aspect | Detail |
|--------|--------|
| **Purpose** | One reusable pytest suite per Protocol, so the shipped fakes and adopters' adapters face the same cases (ADR-067) |
| **Suites** | `RepositoryContract`, `UnitOfWorkContract`, `EventStoreContract`, `SnapshotStoreContract`, `CheckpointStoreContract`, `OutboxStoreContract`, `ProcessedMessageStoreContract`, `LockProviderContract`, `SagaRepositoryContract`, `MessageBrokerContract`, `MessageSubscriberContract`, `ProjectionStoreContract` |
| **Use** | Subclass a suite in a `Test*` class and override its adapter fixture (`repository`, `event_store`, `broker`, …); some suites also ask for hook methods such as `new_aggregate()` and `change()` |
| **Cases** | Each case asserts one promise from the Protocol's docstring or its ADR; gaps are listed in each suite's docstring |
| **Async backend** | `_backend.py`: a session-scoped `anyio_backend` fixture returns `"asyncio"`; a subclass overrides it |
| **Dependencies** | `pytest` and `anyio`, from the `testing` extra. `tests/test_architecture.py` forbids any other pydomain module to import them, any module to import the suites, and the suites to import the fakes |
| **Run in pydomain** | `tests/testing/test_contracts_*.py`, one per shipped adapter, plus `EventSourcedRepository` over `FakeEventStore` |
| **How-to** | `docs/diataxis/how-to/testing/run-contract-suites.md` |

---

## 5.7 Cross-Cutting Relationships

### Inheritance hierarchy

```
BaseModel (Pydantic v2)
├── ValueObject (frozen=True)
├── Entity[TId] (frozen=False)
│   └── AggregateRoot[TId] (+ _pending_events)
│       └── EventSourcedAggregateRoot[TId] (+ _apply, _replay, _when)
│           └── [user aggregates]
│       └── SagaState (+ lifecycle, steps, compensation)
├── DomainEvent (frozen=True)
│   └── [user events]
├── Command[TResult] (frozen=True, extra="forbid")
│   └── [user commands]
├── Query[TResult] (frozen=True, extra="forbid")
│   └── [user queries]
├── IntegrationEvent (frozen=True, primitives only)
├── CommandResult (frozen=True)
│   └── EmptyCommandResult
├── QueryResult (frozen=True)
├── Snapshot (frozen=True)
├── EventStream (frozen=True)
├── StepRecord (frozen=True)
├── CompensationRecord (frozen=True)
└── Specification + ABC (frozen=True)
    └── AndSpecification / OrSpecification / NotSpecification

DomainService (marker, __slots__ = ())
EventUpcaster (non-Pydantic base class)
```

### Protocol interface hierarchy

```
Protocols (structural subtyping, @runtime_checkable)
├── ddd
│   ├── Repository[T: AggregateRoot, TId]
│   ├── Factory[T]
│   └── IdGenerator
├── cqrs
│   ├── CommandHandler[TCommand, TResult]
│   ├── QueryHandler[TQuery, TResult]
│   ├── EventHandler[TEvent]
│   ├── PipelineBehavior
│   ├── UnitOfWork
│   ├── ProcessedCommandStore
│   ├── LockProvider
│   ├── LockKeyResolver
│   ├── Projection[StateT]
│   ├── ProjectionStore
│   └── cqrs.saga
│       └── SagaRepository
├── es
│   ├── EventStore
│   ├── SnapshotStore
│   ├── SnapshotPolicy
│   └── CheckpointStore
└── infrastructure
    ├── MessageBroker
    └── (InMemoryMessageBroker — concrete implementation)
```

### ABCs (shared behaviour, not just signatures)

| ABC | Module | What it provides |
|-----|--------|-----------------|
| `Specification` | `ddd` | `is_satisfied_by()` (abstract), `and_()`, `or_()`, `not_()`, subsumption |
| `EventSourcedAggregateRoot[TId]` | `es` | `_apply()`, `_replay()`, `_take_snapshot()`; subclass implements `_when()` |
| `EventSourcedProjection` | `es` | `handle()` with `_when_*` dispatch, checkpoint tracking, `rebuild()` |
| `AbstractUnitOfWork` | `cqrs` | Commit/rollback lifecycle, event stamping, `_on_post_commit()` hook |
| `SubscriptionRunner` | `infrastructure` | Polling loop, checkpoint management; subclass implements `process_batch()` |
