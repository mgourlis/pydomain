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
                        (never the fakes; no other pydomain module imports it)
  testing ──→ ddd, cqrs, es, infrastructure
  infrastructure ──→ cqrs, es
  cqrs ──→ ddd (and anyio, in cqrs/unit_of_work.py only)
  es ──→ ddd
  ddd ──→ (pydantic, uuid-utils, stdlib only)
```

### Module dependency table

| Module | Depends on | Depends on | Used by |
|--------|-----------|-----------|---------|
| `pydomain.ddd` | `pydantic`, `uuid-utils`, stdlib | — | `cqrs`, `es`, `testing` |
| `pydomain.cqrs` | `pydomain.ddd`; `anyio` in `cqrs/unit_of_work.py` only (ADR-079) | — | `infrastructure`, `testing` |
| `pydomain.es` | `pydomain.ddd` | — | `infrastructure`, `testing` |
| `pydomain.infrastructure` | `pydomain.cqrs`, `pydomain.es` | — | `testing`, user application |
| `pydomain.testing` | All four modules above | — | User test code only |
| `pydomain.testing.contracts` | All four modules above, `pytest` (the `testing` extra), `anyio` (a runtime dependency) | — | Test code only: adopters' adapter tests and pydomain's `tests/testing/` |

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
| **Auto-ID** | All `Entity[TId]` subclasses get auto-generated IDs via pluggable `IdGenerator[TId]`. A runtime type guard verifies the generated value matches the declared `TId` — raises `DomainError` on mismatch. `NewType`, PEP 695 aliases, `Annotated` and unions are resolved recursively to the types they name; `Any` accepts every value; an id type that cannot be checked (an unbound `TypeVar`) raises `DomainError` saying to parametrize the generic or pass an id. The guard adds `id` to a copy of the input and never changes the caller's dict ([ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)). Default generator: `Uuid7Generator` (produces `UUID`). |
| **Extension point** | `Entity.configure(id_generator=...)` — call once at startup. A subclass sets its own with `Sub.configure(...)` or an `_id_generator` class variable, for itself and its subclasses; the nearest class's own generator wins ([ADR-078](../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)). |

### 5.2.2 `ValueObject`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/value_object.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True` |
| **Mutability** | Immutable. Operations return new instances built through the class, so validators run (`model_copy(update=...)` skips validation) |
| **Equality** | Structural — two value objects with identical fields are equal (Pydantic default when frozen) |
| **No identity** | No `id` field. Defined entirely by attributes. |

### 5.2.3 `AggregateRoot[TId]`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/aggregate_root.py` |
| **Base** | `Entity[TId]` |
| **Event buffer** | `_pending_events: list[DomainEvent]` via `PrivateAttr(default_factory=list)` |
| **Key methods** | `_add_event(event)` — buffer a domain event; `pull_events()` — drain and return the buffer |
| **Copies** | Every copy (`model_copy()`, `copy.copy`, and the deep copies) owns a new buffer holding the events pending at copy time; later events on one side never reach the other ([ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)) |
| **Pattern** | Publish-after-commit: aggregate records events during mutation; UnitOfWork drains them after successful commit |
| **Invariants** | Must hold after every public mutation method |

### 5.2.4 `DomainEvent`

| Aspect | Detail |
|--------|--------|
| **File** | `ddd/domain_event.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True` |
| **Fields** | `event_id: UUID` (UUIDv7), `occurred_at: datetime`, `event_version: int = 1`, `correlation_id: UUID \| None`, `causation_id: UUID \| None` |
| **Immutability** | `stamp()` validates the two ids alone, each against its field's type and constraints (a UUID string is converted, anything else raises `pydantic.ValidationError`), then returns `model_copy(update={...}, deep=True)`. No model validator of the event runs. The original, including any list or dict in its payload, is unchanged ([ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)) |
| **Event ID** | When the model is validated, a missing `event_id` comes from `cls._id_generator`: the nearest class's own generator (an `_id_generator` class variable, or `configure()` called on that class), else the one set by `DomainEvent.configure()`. `model_construct()` uses the base class's generator. The event records in the private attribute `_event_id_generated` that its validation generated the id, so the `InboundEventGateway` can derive an inbound event's id and keep one the translator set: `_ensure_event_id` (a `mode="before"` validator, which runs after a subclass's own) reports the id it generated through a `ContextVar` (put under the key validation reads `event_id` from, the alias for a class validated by alias only, B-170), and a wrap validator sets the marker when the built event carries that id, never clearing it. An id given under the alias, or set by a before- or field validator, is explicit; a subclass after- or wrap-validator that replaces the id is not seen (B-171). `model_copy()` and `stamp()` keep it; `model_copy(update={"event_id": ...})` clears it. `__eq__` ignores it and Pydantic regenerates `__hash__`, so round trips stay equal; dumps and `model_fields_set` are unchanged ([ADR-080](../adr/ADR-080-identify-delivered-events-for-event-idempotency.md) point 1) |
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
| **Subsumption** | `subsumes(other)`: `False` by default, overridden with domain rules. `AndSpecification` subsumes when every child does, `OrSpecification` when any child does, `NotSpecification` never |
| **Evaluation** | Composites evaluate `is_satisfied_by()` and `subsumes()` with an explicit stack, short-circuiting left to right, so depth is not bounded by the recursion limit. Each composite method starts the walk from its own operator and children, so a composite subclass's override is called as written, also when it delegates to `super()`; an empty `And` or `Or` follows `all(())` or `any(())`, and a `Not` without its child raises `ValueError` ([ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)) |
| **Serialization** | Dump only. The children are `SerializeAsAny[Specification]`, so a composite's `model_dump()` and `model_dump_json()` contain each child's own fields, nested. A dump carries no type name and cannot be validated back into the leaf types; a type-tagged round trip is deferred to a Wave 2 Feature (backlog F-1) ([ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)) |

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
| **Scope** | Independent per class: `Entity` has a `ClassVar[IdGenerator[Any]]`, and `DomainEvent`, `Command` and `Query` each have a `ClassVar[IdGenerator[UUID]]`, set with that class's `configure()`. A subclass can set its own `_id_generator`, or call `configure()` on itself, and only it and its subclasses use that generator when the model is validated ([ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md), [ADR-078](../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)). |

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
│  │  AggregateLockingBehavior                    │  │                         │ │
│  └──────────────────────────────────────────────┘  └─────────────────────────┘ │
│                                                                               │
│  ┌──────────────────────────────────────────────┐  ┌─────────────────────────┐ │
│  │       Cross-Cutting Protocols                 │  │     Projection          │ │
│  │                                              │  │                         │ │
│  │  ProcessedMessageStore (Protocol)             │  │  Projection[StateT]     │ │
│  │  MessageReservationStore (Protocol)           │  │    (Protocol)           │ │
│  │  LockProvider (Protocol)                      │  │  ProjectionStore        │ │
│  │  LockKeyResolver (Protocol)                   │  │    (Protocol)           │ │
│  │  DictLockKeyResolver                         │  │                         │ │
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
│  │  UnitOfWorkAlreadyCommittedError              │                            │
│  │  LockReentryError                             │                            │
│  │  PipelineReentryError                         │                            │
│  │  CommandInProgressError                       │                            │
│  │  EventInProgressError                         │                            │
│  │  EventIdempotencyMisuseError                  │                            │
│  │  EventHandlersFailed (also an ExceptionGroup) │                            │
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
| **Command ID** | When the model is validated, a missing `command_id` comes from `cls._id_generator`: the nearest class's own generator (an `_id_generator` class variable, or `configure()` called on that class), else the one set by `Command.configure()`. `model_construct()` uses the base class's generator |
| **Naming** | Imperative mood (`PlaceOrder`, `Allocate`) |
| **Rule** | One command → one aggregate mutation → one handler |

#### `Query[TResult]`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/queries.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True`, `extra="forbid"` |
| **Type param** | `TResult: QueryResult` |
| **Fields** | `query_id: UUID` (UUIDv7) |
| **Query ID** | When the model is validated, a missing `query_id` comes from `cls._id_generator`: the nearest class's own generator (an `_id_generator` class variable, or `configure()` called on that class), else the one set by `Query.configure()`. `model_construct()` uses the base class's generator ([ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)) |
| **Rule** | Read-only. No side effects. No Unit of Work. |

#### `IntegrationEvent`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/integration_events.py` |
| **Base** | Pydantic `BaseModel` with `frozen=True` |
| **Constraint** | Fields must be strict JSON at any depth (`str`, `int`, finite `float`, `bool`, `None`, and `dict` with `str` keys or `list` holding them; at most 100 levels, no cycles) — enforced by `@model_validator` ([ADR-074](../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md)) |
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
| **Dispatch** | `dispatch(command) → TResult` — creates UoW, enters it, runs the pipeline; returns the result and the terminal step's copy of the committed events (empty when the handler was not reached) |
| **Pipeline** | Wraps handler in a `MessagePipeline` with registered `PipelineBehavior`s (onion pattern), whose terminal step awaits the handler, then `uow.commit()`, then sets `ctx.new_events` (ADR-079); the bus does not commit after the pipeline |
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
| **Command pipelines** | `next()` includes the commit (ADR-079). Before `next()`: inside the unit-of-work scope, nothing committed. After `next()` returns: committed and `ctx.new_events` set, or nothing committed when an inner behavior short-circuited (the scope rolls back at the exit). After `next()` raises: in general the handler or the commit failed and the rollback runs at the exit (every case: §8.5, [What a behavior sees around `next()`](08-concepts.md#what-a-behavior-sees-around-next)). Work a behavior does through `ctx.uow` after `next()` is not committed |
| **At most one `next()`** | A command pipeline's terminal step runs once per dispatch: a second `next()` raises `PipelineReentryError` before the handler runs (B-111), whatever the first run did. The bus wraps it in `CommandExecutionError`; a first run that already committed keeps its commit. To retry, dispatch the command again. Query and event pipelines allow any number of calls |

#### `MessageContext`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/behaviors.py` |
| **Kind** | Mutable `dataclass` |
| **Fields** | `message`, `handler`, `kind` (`COMMAND`/`QUERY`/`EVENT`), `uow`, `correlation_id`, `causation_id`, `metadata`, `new_events` |

#### Built-in behaviors

| Behavior | File | Concern | Mechanism |
|----------|------|---------|-----------|
| `LoggingBehavior` | `cqrs/behaviors.py` | Observability | Logs each dispatch, its duration and any error; logs no content. The entry line names the handler by `__qualname__` (a `partial` unwrapped, a callable instance by its type's), never by `repr()` (B-201). A `payload_formatter` that raises logs a type-only WARNING `Payload formatter failed for <KIND> <Type>: <ExceptionType>`, and the message goes on with no payload extra (B-188). A cancellation logs `Cancelled <KIND> <Type> after <ms>ms` at INFO and re-raises; the class is read through `cqrs.unit_of_work._cancelled_exc_class` (anyio's, or `asyncio.CancelledError` with no async library running), after `except Exception`, so it never replaces the handler's own error (B-189, B-197). Any other `BaseException` passes unlogged (ADR-081 point 8) |
| `ValidationBehavior` | `cqrs/behaviors.py` | Input validation | Runs the validators registered for the message type before the handler; sync or async |
| `IdempotencyBehavior` | `cqrs/behaviors.py` | Duplicate command rejection | Checks `ProcessedMessageStore`; returns cached result if found; otherwise records the result after the commit, so a failed commit is not cached (at-least-once); a record that fails is a type-only WARNING and the result is still returned (ADR-079). When the store also implements `MessageReservationStore`, reserves the command before the handler: a concurrent duplicate gets `CommandInProgressError`; a command that raises before its commit releases its token, and a committed one completes the reservation with `set` (ADR-079 point 4). `reserve`, `release` and the post-commit `set` run under the unit of work's shield; a failed release is a type-only note |
| `AggregateLockingBehavior` | `cqrs/behaviors.py` | Concurrency control | Resolves lock keys via `LockKeyResolver` and acquires them in sorted order via `LockProvider` (ADR-019). Holds them through the commit (ADR-079). Releases every acquired key in reverse order however the command ends, cancellation included, in the acquiring task; a cancellation during the releases (also one the provider converts or swallows) hands the rest to a background task, which retries the interrupted key; asyncio only, inline under trio; one failing release does not skip the others, and a failed release after a successful handler is logged by type, not raised (ADR-076). Not reentrant: a same-key nested dispatch is unsupported |
| `EventIdempotencyBehavior` | `cqrs/behaviors.py` | Duplicate event rejection | Keys an event on `uuid5(uuid5(EVENT_KEY_NAMESPACE, consumer), str(event_id))`, so sibling events, several handlers sharing a store and a store shared with `IdempotencyBehavior` never share a key; `consumer=` names the handler, and without it the handler's `module:qualname` does, with a `DeprecationWarning` (ADR-080 point 3). Marks the event after its handler succeeds: with a `MessageReservationStore`, `get`, `reserve` (a concurrent duplicate gets `EventInProgressError`), the handler, then `set(key, True)`, releasing the token when the handler raises; `reserve`, `release` and `set` run under the unit of work's shield. Without reservations: `contains`, the handler, then `check_and_set`, also shielded (B-174), so either store must work from any task. A failed mark is a type-only WARNING; a failed release a type-only note (ADR-080 points 4 and 6). One instance serves one handler: a second raises `EventIdempotencyMisuseError` (B-159). In-process and inbound events alike (ADR-080 point 7). On a direct dispatch, the handler's own error, `EventInProgressError` and `EventIdempotencyMisuseError` reach the caller inside `EventHandlersFailed`, after the release, so the inbound gateway NACKs them (ADR-081) |

### 5.3.6 Cross-Cutting Protocols

| Protocol | File | Methods | Purpose |
|----------|------|---------|---------|
| `ProcessedMessageStore` | `cqrs/idempotency.py` | `get(message_id)`, `set(message_id, result)`, `contains(message_id)`, `check_and_set(message_id)` | Tracks processed message IDs (commands and events) for idempotency. Results (`set`, `get`) and the marks of `check_and_set` are separate: a mark creates no result (`get` stays `MISSING`), `check_and_set` reads marks only, and `contains` covers both (ADR-080, B-151) |
| `MessageReservationStore` | `cqrs/idempotency.py` | `reserve(message_id) → token \| None`, `release(message_id, token)` | Optional, beside `ProcessedMessageStore`: reserves a command, or an event key, before its handler. `reserve` is atomic; `release` frees only its token's reservation (B-120); `set` completes it, and a completed reservation refuses `reserve` at least until the TTL (B-142); a reservation is not a result (`get` returns `MISSING`); reservations have a key space of their own, separate from results and from the marks of `check_and_set`: reserving an id never marks it processed, and `check_and_set` never reserves it; a reservation left by a crash or a failed release waits for the adapter's TTL (B-117). Works from any task (B-123). Contract: `MessageReservationStoreContract` (ADR-067) |
| `LockProvider` | `cqrs/locking.py` | `acquire(key)`, `release(key)` | Named lock acquisition for concurrency control. A cancelled `acquire` never holds the key; a same-task re-acquire is never granted (it raises `LockReentryError` or blocks); `release` runs in the acquiring task, except after a cancellation during the releases, when it comes from a background task, possibly a second time for one key; it must be ownership-safe: a late release never frees another holder's lock (ADR-076) |
| `LockKeyResolver` | `cqrs/locking.py` | `resolve(message) → list[str]` | Derives lock keys from messages; empty list = no lock |
| `DictLockKeyResolver` | `cqrs/locking.py` | Registry-based `register(message_type, key_fn)` | Maps message types to key-extraction functions |

### 5.3.7 Unit of Work

#### `UnitOfWork` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/unit_of_work.py` |
| **Kind** | `@runtime_checkable` Protocol |
| **Methods** | `__aenter__`, `__aexit__`, `commit()`, `rollback()`, `collect_events()` |
| **Semantics** | Publish-after-commit: events stamped with `correlation_id`/`causation_id` after successful commit. Exit without `commit()`, with or without an exception, rolls back. A rollback discards every event the scope staged, also in repositories that outlive the unit of work. A scope commits at most once (ADR-068). A Protocol-only implementation is not covered by the shield below: it shields its own storage commit and rollback (ADR-079) |

#### `AbstractUnitOfWork` *(ABC)*

| Aspect | Detail |
|--------|--------|
| **Base** | `ABC` + `UnitOfWork` |
| **Provides** | Full commit/rollback lifecycle, event stamping, extension hooks |
| **Subclass must** | Fill `_repos` with its repositories; override `_flush()`, `_commit()` and `_rollback()` for its storage |
| **Extension hooks** | `_flush()`, `_write_outbox()` (built in with an `OutboxWriter`), `_commit()`, `_rollback()` (all async no-ops by default) |
| **Rollback** | Calls `_rollback()`, then, in `finally`, drains `pull_events()` of every repository in `_repos` (all of them, even if one raises), clears the collected events and starts a new scope; re-raises `_rollback()`'s error, noting a drain error's type, else the first drain error ([ADR-078](../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)) |
| **Exit** | Not committed: `rollback()`. Committed: no rollback, collected events kept, repositories drained so a change staged after the commit never reaches a later scope |
| **Cancellation** | `_commit()` and `_rollback()` run through the private helper `_shielded()`, to completion when the calling task is cancelled, and the cancellation propagates once the hook ended (ADR-079 point 6). The helper runs the hook inside `anyio.CancelScope(shield=True)` and, under asyncio, in a task of its own (`asyncio.Task(coro, loop=loop, context=<caller's context>)`, never eager), waited on through a private future set by the task's done callback. Under an anyio cancel scope it does not re-raise: anyio delivers the cancellation again at the next checkpoint. With no async library running, or no current task, it runs the hook inline (B-131). A cancellation pending before `_commit()` starts still lets it complete (B-132). A completed `_commit()` marks the scope committed before the cancellation is re-raised, so the exit drains. A hook that fails during the cancellation is a type-only note on it (`clean-up also failed: <Type>`); a hook's own `CancelledError`, with nothing cancelling the caller, becomes the private `_ShieldedCallCancelledError(CQRSError)` raised from it (B-130). `_flush()` and `_write_outbox()` are not shielded (B-110). `IdempotencyBehavior` uses the same helper for its record after a commit (B-123) |
| **`shield_hooks`** | `ClassVar[bool] = True`. `False` runs `_commit()` and `_rollback()` inline in the calling task, cancellable, for storage keyed by the current task (`async_scoped_session(scopefunc=current_task)`), which in a hook task sees another session and silently loses the writes (B-115, B-119) |
| **Commit once** | A second `commit()` in one scope raises `UnitOfWorkAlreadyCommittedError` before any hook runs. After a successful first commit its changes stay persisted and nothing is rolled back; after a failed one, `rollback()` starts a new scope for the retry |
| **`collect_events()`** | Returns a copy of the last commit's stamped events |
| **Exceptions** | `UnitOfWorkAlreadyCommittedError(CQRSError)` in `cqrs/exceptions.py` |

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
| **Methods** | `load(projection_id)`, `save(projection_id, state)` |

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
| **Fields** | `saga_type`, `current_step`, `step_history`, `processed_event_ids`, `pending_commands`, `compensation_stack`, `failed_compensations`, `retry_count`, `max_retries` |
| **Retries** | `max_retries: int \| None = Field(default=3, ge=0)` is the number of retries after the first attempt; the saga fails once `retry_count > max_retries`, on the event path and in recovery alike; `None` allows any number (ADR-077) |
| **Memory bounds** | `max_processed_events`, `max_step_history` caps; `prune_history()` for explicit cleanup |

#### `SagaManager`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/saga/manager.py` |
| **Dependencies** | `SagaRepository`, `SagaRegistry`, a `CommandDispatcher` (Protocol: `dispatch(command)` runs the command and publishes its events; the `MessageBus`). A `CommandBus` or a subclass raises `TypeError` (ADR-077) |
| **Lifecycle** | Load/create state → instantiate saga → `handle(event)` → save state → dispatch pending commands |
| **Failure** | A failing step's forward commands (`fail=True`, or `dispatch()` then `fail()`) are sent before the compensations; a failed one is recorded in `failed_compensations` and the saga ends `FAILED` (ADR-077) |
| **Recovery** | `recover_pending_sagas(limit)` re-dispatches undispatched commands and finishes interrupted compensations, saga by saga; a failing saga is logged by type and skipped, recovery stops at an unhydratable command, which stays pending with the commands after it and suspends a running saga (an earlier suspension is kept) until it can be sent; returns the ids not recovered (ADR-035, ADR-077) |
| **Integration** | `bind_to(event_dispatcher)` — registers itself as an event handler with an `EventHandlerRegistrar` (`register_event`), such as the `MessageBus` |

#### `SagaRepository` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/saga/repository.py` |
| **Methods** | `save(state)`, `get_by_id(id)`, `find_by_correlation_id(correlation_id, saga_type)`, `find_stalled_sagas(limit)`, `find_suspended_sagas(limit)`, `find_expired_suspended_sagas(limit)`, `pull_events()` |
| **Stalled sagas** | Non-terminal sagas with pending commands or in `COMPENSATING`; `recover_pending_sagas` relies on both (ADR-077) |
| **Events** | `save` drains the state's events; `pull_events()` hands each over once |
| **Contract** | `SagaRepositoryContract` in `pydomain.testing.contracts` (ADR-067) |

### 5.3.10 Exceptions

| Exception | Inherits from | Purpose |
|-----------|---------------|---------|
| `CQRSError` | `DomainError` | Base for all CQRS-layer errors |
| `HandlerAlreadyRegisteredError` | `CQRSError` | Duplicate handler registration |
| `NoHandlerRegisteredError` | `CQRSError` | Dispatch with no registered handler |
| `CommandExecutionError` | `CQRSError` | Handler exception wrapper (carries failed command) |
| `IdempotentCommandIgnored` | `CQRSError` | Exported, but nothing raises it: `IdempotencyBehavior` returns the cached result for a duplicate command |
| `UnitOfWorkAlreadyCommittedError` | `CQRSError` | Second `commit()` in one unit-of-work scope; raised before any hook runs (ADR-068) |
| `LockReentryError` | `CQRSError` | A task acquires a lock key it already holds; raised by `FakeLockProvider` instead of deadlocking, may be raised by any `LockProvider` (ADR-076) |
| `PipelineReentryError` | `CQRSError` | A behavior calls `next()` a second time in a command pipeline; raised before the handler runs, wrapped in `CommandExecutionError` by the bus (ADR-079, B-111). Its message carries no content |
| `CommandInProgressError` | `CQRSError` | `IdempotencyBehavior` with a reserving store refuses a duplicate of a command still running; raised before the handler runs, wrapped in `CommandExecutionError` by the bus (ADR-079 point 5, B-107). Its message names the command type only |
| `EventInProgressError` | `CQRSError` | `EventIdempotencyBehavior` with a reserving store refuses a duplicate of an event its consumer is still handling; raised before the handler runs. `EventBus` logs it at ERROR like any handler failure; on a direct dispatch it is a member of `EventHandlersFailed`, so the inbound gateway NACKs it (ADR-080 points 4 and 6, B-150; ADR-081). Its message names the event type only |
| `EventIdempotencyMisuseError` | `CQRSError` | One `EventIdempotencyBehavior` instance reaches a second, different handler; raised at that handler's first dispatch, before any store call, so that handler does not run (ADR-080 point 3, B-159, B-167). On a direct dispatch it is a member of `EventHandlersFailed`, so the inbound gateway NACKs it (B-182). Its message names no content |
| `EventHandlersFailed` | `ExceptionGroup`, `CQRSError` | A direct dispatch (`EventBus.dispatch`, `MessageBus.dispatch(event)`, `Application.dispatch(event)`) raises it once every handler has run, if any raised an `Exception`. `exceptions` holds each handler's own exception, unchanged and in registration order; `event_type` names the event class. Its message, `<n> handler(s) failed for <EventType>`, and its `repr()` carry no value (B-195); `split()` and `except*` give an `EventHandlersFailed` that recounts; it pickles and copies intact (ADR-081 point 2, B-181) |

Every exported exception pickles and copies with its type, message and attributes. Those whose `__init__` takes other arguments than a message (`CommandExecutionError`, `IdempotentCommandIgnored`, `EventHandlersFailed` and the ES exceptions of §5.4) define `__reduce__`; all but `EventHandlersFailed` rebuild through the private `pydomain.ddd.exceptions._restore_exception`, which never calls `__init__` (B-208, B-210; `tests/test_exception_pickling.py` sweeps them).

---

### 5.3.11 Event Bus and Outbox

#### `EventBus`

| Aspect | Detail |
|--------|--------|
| **File** | `cqrs/event_bus.py` |
| **Registration** | `register(event_type, handler, behaviors?)` — several handlers per event type |
| **Dispatch** | `dispatch(event)` runs one event's handlers; `dispatch_many(events)` runs events one after another. Each handler runs through its own pipeline with `uow=None`; a handler's exception is logged, and the others still run (ADR-046, ADR-062). Then `dispatch`, the direct path, raises `EventHandlersFailed` if any handler failed; `dispatch_many`, the post-command path, swallows the failures (ADR-081 point 1). A cancellation propagates at once |
| **Used by** | `MessageBus`, which hands it the committed events of a command and any `DomainEvent` passed to `MessageBus.dispatch()` |

#### Transactional outbox

| Type | File | Role |
|------|------|------|
| `OutboxEntry` | `cqrs/outbox.py` | Frozen record of one integration event awaiting publication: `message_id`, `topic`, `payload`, `event_version`, `attempts`, `occurred_at`, tracing IDs |
| `OutboxStore` *(Protocol)* | `cqrs/outbox.py` | `append`, `fetch_unpublished` (claims what it returns), `mark_published`, `mark_failed`, `mark_dead_lettered` |
| `OutboundEventRegistry` | `cqrs/outbox.py` | Maps a domain event type to an integration class, a topic and a pure translator (`register_translation`) |
| `OutboxWriter` | `cqrs/outbox.py` | Translates the collected events and appends the entries; `AbstractUnitOfWork` calls it inside the transaction |
| `IntegrationEvent` | `cqrs/integration_events.py` | Strict-JSON event for other systems; `stamp()` adds tracing IDs |

The relay that delivers the entries is `OutboundEventGateway` (§5.5.7). See §6.6 and ADR-063, ADR-074.

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
│  │                           │  │  StaleSnapshotError              │      │
│  │                           │  │  SnapshotRestoreError            │      │
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
| **`_replay(event)`** | Calls `_when(event)` → increments `self.version` — does NOT buffer events |
| **`_take_snapshot()`** | Serializes state via `model_dump(mode='python')` → returns `Snapshot` |

### 5.4.2 `EventStore` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `es/event_store.py` |
| **Methods** | `append_to_stream(aggregate_id, events, expected_version, command_id?)` — append with optimistic concurrency; `read_stream(aggregate_id, from_version?) → EventStream` — read by stream; `read_all(from_version?) → EventStream` — read global log |
| **Concurrency** | `append_to_stream` raises `ConcurrencyError` if `expected_version` doesn't match |
| **Idempotency** | When `command_id` is provided, should raise `DuplicateCommandError` on re-submission; the id counts as processed only once its append stores events, so a retry after `ConcurrencyError` is accepted (ADR-041, ADR-072) |
| **Semantics** | An empty append runs both checks and stores nothing (no stream, no `command_id`); a negative `from_version` raises `ValueError` in `read_stream` (before the stream lookup) and `read_all` (ADR-072) |

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
| **Constructor** | `EventSourcedRepository(event_store, aggregate_cls, snapshot_store?, snapshot_policy?, snapshot_schema_policy?)` |
| **`save(aggregate)`** | Pulls pending events → appends to stream with optimistic concurrency → optionally takes snapshot |
| **`get_by_id(id)`** | Reads event stream → optionally loads snapshot for faster hydration → replays events → returns aggregate |
| **`pull_events()`** | Drains internal event buffer for Unit of Work |

### 5.4.5 Snapshots

#### `Snapshot`

| Aspect | Detail |
|--------|--------|
| **File** | `es/snapshot.py` |
| **Base** | Pydantic `BaseModel` |
| **Fields** | `aggregate_id: str`, `version: int`, `state: dict` (the aggregate's fields without `version` and computed fields), `schema_version: int = 1`, `created_at: datetime` |
| **Restore** | `EventSourcedRepository` validates `state` into the aggregate class with `model_validate` (computed keys dropped, the requested id set); invalid state raises `SnapshotRestoreError` (ADR-075) |

#### `SnapshotStore` *(Protocol)*

| Aspect | Detail |
|--------|--------|
| **File** | `es/snapshot.py` |
| **Methods** | `get(aggregate_type, aggregate_id) → Snapshot \| None`; `save(aggregate_type, snapshot) → None` |
| **Promise** | Changing a snapshot after `save()`, or one `get()` returned, never changes what the store holds; `SnapshotStoreContract` pins it (ADR-075) |

#### `SnapshotPolicy` *(Protocol)* and `SnapshotThresholdPolicy`

| Aspect | Detail |
|--------|--------|
| **`SnapshotPolicy`** | `should_snapshot(aggregate_type, aggregate_id, current_version, pending_event_count) → bool`; `pending_event_count` is the size of the batch just appended |
| **`SnapshotThresholdPolicy`** | Default implementation — snapshots every N events (configurable `threshold`, default 10). `threshold=0` → snapshot on every flush. |

### 5.4.6 Event Versioning

#### `EventUpcaster`

| Aspect | Detail |
|--------|--------|
| **File** | `es/upcasting.py` |
| **Class vars** | `source_type: str`, `source_version: int`, `target_version: int` |
| **Method** | `upcast(event: dict) → dict` — transforms payload; wraps errors in `UpcastError`; does not need to set `event_version` |

#### `UpcasterRegistry`

| Aspect | Detail |
|--------|--------|
| **File** | `es/upcasting.py` |
| **Purpose** | Discovers and chains upcasters to migrate events across schema versions |
| **Methods** | `register(upcaster)` — add an upcaster; raises `TypeError` without `source_type`, `source_version` or `target_version`, `ValueError` for a step that does not go up or is already registered. `resolve(source_type, source_version) → list[type[EventUpcaster]]` — the chain of hops; `UpcastError` on a cycle |
| **Contract** | One upcaster per `(source_type, source_version)`; every step goes up a version (ADR-042, amended by ADR-069) |

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
| `StaleSnapshotError` | `DomainError` | A snapshot's schema version does not match the aggregate's; never raised by the repository, available to policies |
| `SnapshotRestoreError` | `DomainError` | A usable snapshot's state no longer validates into the aggregate class; value-free message, no cause or context, attributes `aggregate_id` and `snapshot_version` (ADR-075) |

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
│  │            message_broker?, outbound_gateway?, event_registry?,    │    │
│  │            inbound_gateways?) → Application                        │    │
│  │                                                                    │    │
│  │  Application                                                       │    │
│  │    message_bus, event_registry (read-only)                         │    │
│  │    dispatch(command | query | event) → result                      │    │
│  │    shutdown()                                                      │    │
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
| **Constructor** | `Application(message_bus, event_registry?, snapshot_store?, message_broker?, inbound_gateways?, outbound_gateway?)`; a missing `event_registry` becomes a new `EventRegistry()` |
| **Key methods** | `dispatch(message: Command \| Query \| DomainEvent) → Any` — unified dispatch; `shutdown()` — stops inbound gateways, outbound gateway, broker, in that order; every stop runs, the first error is re-raised with type-only notes for later ones, each failure noted once ([ADR-073](../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md), [ADR-078](../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md)) |
| **Properties** | Read-only: `message_bus`, `event_registry` (never `None`), `snapshot_store`, `message_broker`, `inbound_gateways` (a copy, a new list each call), `outbound_gateway` |

### 5.5.2 `bootstrap()`

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/bootstrap.py` |
| **Signature** | `async bootstrap(event_store?, snapshot_store?, message_bus?, message_broker?, outbound_gateway?, event_registry?, inbound_gateways?) → Application` |
| **Purpose** | Dependency injection composition root. Wires together all components. Tests call with fakes; production calls with real adapters. |
| **Wiring check** | Raises `ValueError` before starting anything if an inbound gateway's `message_bus` is not the application's bus, including when `message_bus` is omitted, or if one gateway is listed twice. It works on a copy of the list, so a gateway added to the caller's list during a start is ignored ([ADR-073](../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)) |
| **Start order** | Inbound gateways, then broker, then outbound gateway. If one `start()` raises or is cancelled, the parts already started are stopped in shutdown order (inside `asyncio.shield`, so a cancellation does not interrupt it) and the error or cancellation is re-raised. `KeyboardInterrupt` and `SystemExit` propagate at once, without clean-up |

### 5.5.3 `MessageBus`

| Aspect | Detail |
|--------|--------|
| **File** | `infrastructure/message_bus.py` |
| **Kind** | Level 3 facade wrapping `CommandBus` + `QueryBus` + event dispatcher |
| **Registration** | `register_command(type, handler, uow_factory, behaviors?)`; `register_query(type, handler, behaviors?)`; `register_event(type, handler)` |
| **Dispatch** | `dispatch(message) → Any` — inspects type, routes to CommandBus or QueryBus |
| **Event dispatch** | After command dispatch, collected domain events are dispatched to registered `EventHandler`s |
| **Error handling** | Event handlers fail independently — caught and logged per handler, queue continues. After a command, the failures are swallowed and the command's result is returned; `dispatch(event)` raises `EventHandlersFailed` once every handler has run (ADR-081 point 1) |

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
| **Upcasting** | Optionally wired to `UpcasterRegistry` for schema evolution on read. `deserialize` applies the chain, raises `UpcastError` unless it ends at the class's version, and writes that version to `event_version`; a stored version newer than the class is validated unchanged (ADR-069) |

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

### 5.5.7 Gateways and `MessageSubscriber`

| Type | File | Role |
|------|------|------|
| `OutboundEventGateway` | `infrastructure/message_broker.py` | Relay: `run()` polls the outbox, publishes each entry through the `MessageBroker`, marks it, retries with backoff and dead-letters after `max_attempts`; `start()` and `stop()` run it as a background task (ADR-063, ADR-074) |
| `MessageSubscriber` *(Protocol)* | `infrastructure/message_subscriber.py` | `subscribe(topic, handler)`, `start()`, `stop()` for consuming from an external broker |
| `InboundEventGateway` | `infrastructure/message_subscriber.py` | `InboundEventGateway(subscriber, message_bus)`; `register_translation(topic, integration_class, translator)` turns an incoming integration event into a domain event and dispatches it on the `MessageBus` (ADR-060). Poison messages (an unknown topic, a payload that fails validation, a translator that raises or returns something other than a `DomainEvent`) are logged and ACKed; a handler failure reaches it as `EventHandlersFailed` and is re-raised, so the subscriber NACKs it (ADR-081 point 3, §6.7) |

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
| `FakeUnitOfWork` | `testing/fake_unit_of_work.py` | `UnitOfWork` | Subclasses `AbstractUnitOfWork`; takes one `repository` or a dict of `repositories`; records a rollback; `fail_at` (`"flush"`, `"write_outbox"`, `"commit"`) makes that commit hook raise `failure` (a new `RuntimeError` by default) once, then resets itself (B-127) |
| `FakeEventStore` | `testing/fake_event_store.py` | `EventStore` | Dict-of-lists plus a global log; `append_to_stream`, `read_stream`, `read_all`; concurrency check; duplicate `command_id` detection, recorded only when events are stored; empty append stores nothing; negative `from_version` raises `ValueError` (ADR-072) |
| `FakeSnapshotStore` | `testing/fake_snapshot_store.py` | `SnapshotStore` | Dict-backed; `save`/`get` by aggregate type + ID; stores and returns deep copies (ADR-075) |
| `FakeCheckpointStore` | `testing/fake_checkpoint_store.py` | `CheckpointStore` | Dict-backed; `load`/`save` by subscription ID |
| `FakeOutboxStore` | `testing/fake_outbox_store.py` | `OutboxStore` (structurally) | Records appends, publish marks, failures and dead letters; `append` raises `CQRSError` for a `message_id` that is stored or repeats in the batch and stores nothing of it (ADR-080); injectable clock; simulated fetch and append failures |
| `FakeSagaRepository` | `testing/fake_saga_repository.py` | `SagaRepository` | Dict-backed; deep copies; `find_by_correlation_id`, `find_stalled_sagas`, `find_suspended_sagas`, `find_expired_suspended_sagas` |
| `FakeProcessedMessageStore` | `testing/fake_processed_message_store.py` | `ProcessedMessageStore`, `MessageReservationStore` | Dict-backed; `get`/`set`/`contains`/`check_and_set`, with the marks of `check_and_set` in a set of their own, not in the results; `reserve`/`release` with `UUID` tokens in their own key space, an optional `reservation_ttl`; `set` completes a reservation |
| `FakeLockProvider` | `testing/fake_lock_provider.py` | `LockProvider` | One `asyncio.Lock` per key; not reentrant |
| `InMemoryMessageBroker` | `testing/in_memory_message_broker.py` | `MessageBroker` (structurally) | Captures published events in a list |
| `InMemoryMessageSubscriber` | `testing/in_memory_message_subscriber.py` | `MessageSubscriber` (structurally) | Records subscriptions; `simulate_message()` calls the handler, or raises `KeyError` for a topic with no handler (ADR-072); `outcomes` records `"ack"` or `"nack <ExceptionType>"` for each delivery, and the exception is still re-raised (ADR-081 point 9) |
| `InMemoryProjectionStore` | `testing/in_memory_projection_store.py` | `ProjectionStore` | Dict-backed; `load`/`save` by projection ID |

**Design principle:** Fakes over mocks. Every fake behaves like the real thing without requiring infrastructure setup. Tests run in milliseconds.

### 5.6.1 `pydomain.testing.contracts`

| Aspect | Detail |
|--------|--------|
| **Purpose** | One reusable pytest suite per Protocol, so the shipped fakes and adopters' adapters face the same cases (ADR-067) |
| **Suites** | `RepositoryContract`, `UnitOfWorkContract`, `EventStoreContract`, `SnapshotStoreContract`, `CheckpointStoreContract`, `OutboxStoreContract`, `ProcessedMessageStoreContract`, `MessageReservationStoreContract`, `LockProviderContract`, `SagaRepositoryContract`, `MessageBrokerContract`, `MessageSubscriberContract`, `ProjectionStoreContract` |
| **Use** | Subclass a suite in a `Test*` class and override its adapter fixture (`repository`, `event_store`, `broker`, …); some suites also ask for hook methods such as `new_aggregate()` and `change()` |
| **Cases** | Each case asserts one promise from the Protocol's docstring or its ADR; a promise a suite does not cover yet is named in its docstring |
| **Async backend** | `_backend.py`: a session-scoped `anyio_backend` fixture returns `"asyncio"`; a subclass overrides it |
| **Dependencies** | `pytest`, from the `testing` extra, and `anyio`, a runtime dependency. `tests/test_architecture.py` forbids any other pydomain module to import pytest, any module but `cqrs/unit_of_work.py` to import anyio directly (B-128), any other pydomain module to import the suites, and the suites to import the fakes |
| **Run in pydomain** | `tests/testing/test_contracts_*.py`, one per shipped adapter, plus `EventSourcedRepository` over `FakeEventStore` (plus three that test the suites themselves) |
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
├── Snapshot
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
│   ├── ProcessedMessageStore
│   ├── MessageReservationStore
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
| `AbstractUnitOfWork` | `cqrs` | Commit/rollback lifecycle, event stamping, `_flush()`, `_write_outbox()`, `_commit()` and `_rollback()` hooks |
| `SubscriptionRunner` | `infrastructure` | Polling loop, checkpoint management; subclass implements `process_batch()` |
