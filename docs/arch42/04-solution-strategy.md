# 4. Solution Strategy

This section describes the foundational technical decisions that shape the entire library. These are not feature-level choices but **cross-cutting strategies** that every module, class, and interface must conform to. The section answers: *what are the core mechanisms, and why were they chosen?*

## 4.1 Strategy Overview

The library rests on **five interlocking strategies**, each addressing a specific architectural concern:

| # | Strategy | Concern | Mechanism |
|---|----------|---------|-----------|
| **S1** | Pydantic v2 `BaseModel` as universal base | Validation, serialization, immutability control | All domain concepts inherit `BaseModel`; frozen config per concept type |
| **S2** | `Protocol` for behavioural interfaces | Decoupling, testability, infrastructure independence | Structural subtyping — no inheritance required for adapters |
| **S3** | Generic type parameters | Type safety, developer ergonomics | Python 3.12 `class Foo[T]` (PEP 695) — variance auto-inferred via `__infer_variance__` |
| **S4** | Pipeline behavior middleware | Cross-cutting concerns without coupling | Onion-style `PipelineBehavior` chain wrapping handlers |
| **S5** | Fully async with first-class fakes | Testability without infrastructure | `async`/`await` on all I/O; `pydomain.testing` ships complete doubles |

The strategies compose: S1 provides the data backbone, S3 binds it to types, S2 decouples infrastructure, S4 wraps execution, and S5 makes everything testable.

---

## 4.2 S1 — Pydantic v2 `BaseModel` as the Universal Base

### Decision

Every domain concept — `Entity`, `ValueObject`, `DomainEvent`, `Command`, `Query`, `IntegrationEvent`, `CommandResult`, `QueryResult` — inherits from Pydantic v2's `BaseModel`. There is no parallel class hierarchy, no custom metaclass magic, and no abstraction layer between user models and Pydantic.

### Why

| Benefit | How it manifests |
|---------|-----------------|
| **Built-in validation** | Pydantic `@field_validator` and `@model_validator` enforce domain invariants at construction time. No separate validation layer. |
| **Round-trip serialization** | `model_dump()` → dict → `model_validate()` → instance. Events, commands, and snapshots serialize without custom codecs. |
| **JSON Schema generation** | `model_json_schema()` produces schemas for API contracts, message broker validation, and documentation — for free. |
| **IDE autocompletion** | Pydantic models expose typed fields that VS Code / Pyright / mypy understand natively. |
| **Immutability control** | `ConfigDict(frozen=True)` for value objects and events; `ConfigDict(frozen=False)` for entities. One mechanism, two behaviours. |

### How it shapes the codebase

**Frozen vs. mutable** is the primary axis that distinguishes concept types:

```
frozen=True   ← ValueObject, DomainEvent, Command[TResult], Query[TResult],
                  CommandResult, QueryResult, IntegrationEvent, EventStream

frozen=False  ← Entity[TId], AggregateRoot[TId], EventSourcedAggregateRoot[TId],
                  SagaState
```

**Private attributes** bridge the gap when a frozen model needs mutable internal state. `AggregateRoot` uses `PrivateAttr(default_factory=list)` for `_pending_events` — the list itself is mutable even though the model appears frozen to Pydantic. The `pull_events()` method drains and returns the list, enabling the publish-after-commit pattern.

**Stamping preserves immutability.** `DomainEvent.stamp(correlation_id, causation_id)` does not mutate the event — it returns a new frozen copy via `model_copy(update={...})`. The UnitOfWork replaces originals with stamped copies before publishing.

### What this rules out

- **No Pydantic v1 shims.** The library never uses `@validator`, `__fields__`, `schema()`, or `parse_obj()`. Users on Pydantic v1 must migrate first.
- **No custom serialization.** Everything goes through `model_dump()` / `model_validate()`. If a user needs custom serialization (e.g., protobuf), they implement it on their side — the library does not provide hooks.
- **No ORM base classes.** The library does not depend on SQLAlchemy, Django, or any ORM. Users' ORM models are separate from their domain models.

---

## 4.3 S2 — `Protocol` for Behavioural Interfaces

### Decision

All infrastructure contracts — `Repository`, `EventStore`, `SnapshotStore`, `CheckpointStore`, `MessageBroker`, `UnitOfWork`, `ProcessedCommandStore`, `LockProvider`, `LockKeyResolver`, `SagaRepository`, `ProjectionStore`, `CommandHandler`, `QueryHandler`, `EventHandler`, `PipelineBehavior`, `SnapshotPolicy`, `IdGenerator[TId]` — are expressed as Python `typing.Protocol` classes, not abstract base classes (ABCs).

### Why

| Concern | `Protocol` solution | ABC problem avoided |
|---------|-------------------|-------------------|
| **No base-class coupling** | Any class satisfying the method signatures is compatible — zero inheritance required. | ABC forces a shared base class, creating coupling between library and user infrastructure code. |
| **Multiple interfaces** | A single class can satisfy `Repository[Order, UUID]` and `EventStore` simultaneously — Python's structural subtyping allows this naturally. | ABC's `__mro__` makes multiple-inheritance hierarchies fragile. |
| **Third-party integration** | A SQLAlchemy `Session` wrapper satisfies `UnitOfWork` without the library ever importing SQLAlchemy. | ABC requires importing the ABC base class from the library, dragging in library types. |
| **Test doubles** | `FakeRepository` in `pydomain.testing` is a plain class — no stub methods, no `NotImplementedError`. | ABC-based fakes must inherit the ABC, getting `NotImplementedError` stubs they then override. |

### How it shapes the codebase

Every `Protocol` is decorated with `@runtime_checkable`, enabling `isinstance()` checks for defensive validation where needed. Methods declare `...` (Ellipsis) bodies, making the interface explicit without dictating implementation:

```python
@runtime_checkable
class Repository[T: AggregateRoot, TId](Protocol):
    async def save(self, aggregate: T, command_id: UUID | None = None) -> None: ...
    async def get_by_id(self, id: TId) -> T: ...
    def pull_events(self) -> list[DomainEvent]: ...
```

The library **never** holds concrete implementations of these protocols (except fakes in `pydomain.testing`). Production implementations live entirely in user code. This is how the library achieves its "opinionated about patterns, unopinionated about infrastructure" guarantee.

### Exceptions to the rule

Three base classes use **ABC** instead of `Protocol` — because they carry shared behaviour, not just a signature contract:

| Class | Module | Why ABC |
|-------|--------|---------|
| `EventSourcedAggregateRoot[TId]` | `es` | Inherits `AggregateRoot[TId]` and adds `_apply()`, `_replay()`, `_when()` orchestration logic. Subclasses must implement `_when()`. |
| `EventSourcedProjection` | `es` | Provides `_when_*` handler dispatch, checkpoint tracking, and `handle()` orchestration. Subclasses implement `_when_*` methods. |
| `AbstractUnitOfWork` | `cqrs` | Implements commit/rollback lifecycle, event stamping, and extension hooks. Subclasses implement `_commit()` and `_rollback()`. |

The principle: **use `Protocol` when you need only a signature; use ABC when you provide reusable orchestration logic.**

---

## 4.4 S3 — Generic Type Parameters

### Decision

The library uses Python 3.12's new-style generic syntax (`class Generic[T]` from PEP 695) throughout, binding return types and aggregate types at the point of declaration rather than the point of use.

### Key type parameters

| Type Parameter | Bound | Declared On | Purpose |
|---------------|-------|-------------|---------|
| `TId` | unconstrained | `Entity[TId]`, `AggregateRoot[TId]`, `EventSourcedAggregateRoot[TId]` | Identity type — `UUID`, `int`, `str`, or any hashable/serializable type |
| `TResult` | `CommandResult` | `Command[TResult]` | What `dispatch(command)` returns |
| `TResult` | `QueryResult` | `Query[TResult]` | What `dispatch(query)` returns |
| `T` | `AggregateRoot` | `Repository[T, TId]` | Aggregate type the repository manages |
| `T` | `Command` | `CommandHandler[T, R]` | Command type the handler accepts |
| `T` | `Query` | `QueryHandler[T, R]` | Query type the handler accepts |
| `T` | `DomainEvent` | `EventHandler[T]` | Event type the handler reacts to |
| `S` | `SagaState` | `Saga[S]` | State type carried by the saga |
| `StateT` | unconstrained | `Projection[StateT]` | Read model state type |

### Why this matters for developers

Without generics, `dispatch()` returns `Any`. With them:

```python
class PlaceOrder(Command[PlaceOrderResult]): ...
class PlaceOrderResult(CommandResult): ...

result: PlaceOrderResult = await bus.dispatch(PlaceOrder(...))
#              ↑ mypy / pyright infers PlaceOrderResult — no cast
```

The `CommandBus` resolves the handler, executes it, and returns the declared `TResult`. The same pattern applies to `Query[TResult]` and `Saga[S]`. Type checkers enforce the binding — if a handler returns the wrong type, it's a static error.

### How it shapes the codebase

Generics flow through the entire stack: `Command[TResult]` → `CommandHandler[TCommand, TResult]` → `CommandBus.dispatch()` → typed return. The `Repository[T, TId]` generic ensures the `get_by_id()` method returns the concrete aggregate type, not `AggregateRoot` or `Any`. This eliminates downcasting in handler code.

---

## 4.5 S4 — Pipeline Behavior Middleware

### Decision

Cross-cutting concerns — idempotency, locking, logging, validation — are expressed as `PipelineBehavior` implementations that wrap command/query handlers in an **onion (decorator) pattern**. The `CommandBus` and `QueryBus` compose registered behaviors into a chain before invoking the terminal handler.

### Architecture

```
CommandBus.dispatch(command)
    │
    ▼
┌──────────────────────────┐
│  IdempotencyBehavior     │  ← checks ProcessedCommandStore
│  ┌────────────────────┐  │
│  │  LockingBehavior   │  │  ← acquires LockProvider
│  │  ┌──────────────┐  │  │
│  │  │  (handler)   │  │  │  ← actual command handler
│  │  └──────────────┘  │  │
│  └────────────────────┘  │
└──────────────────────────┘
```

Each behavior receives a `MessageContext` (mutable carrier with the message, handler, correlation IDs, and metadata) and a `next` callable. It executes logic **before** calling `next()` and **after** it returns.

### Built-in behaviors

| Behavior | Concern | Mechanism |
|----------|---------|-----------|
| `IdempotencyBehavior` | Duplicate command rejection | Checks `ProcessedCommandStore` for `command_id`; returns cached result if found; stores result after success |
| `LockingBehavior` | Concurrency control | Resolves lock key via `LockKeyResolver`; acquires distributed lock via `LockProvider`; releases on completion |

### Why pipeline behaviors

- **Open/closed principle.** Adding a new cross-cutting concern (rate limiting, authorization, metric collection) requires implementing `PipelineBehavior` and registering it — no modification to the bus or handlers.
- **Handler purity.** Command handlers focus on domain logic: load aggregate → mutate → return result. They never import idempotency stores or lock providers.
- **Composability.** Behaviors are ordered by registration. The same handler can be wrapped differently per environment (e.g., no locking in tests, Redis locking in production).

### `MessageContext` — the shared carrier

The `MessageContext` dataclass flows through every behavior and the terminal handler:

| Field | Purpose |
|-------|---------|
| `message` | The `Command` or `Query` being dispatched |
| `handler` | The resolved handler callable |
| `kind` | `MessageKind.COMMAND` / `QUERY` / `EVENT` |
| `uow` | Active `UnitOfWork` (commands only) |
| `correlation_id` / `causation_id` | Distributed tracing propagation |
| `metadata` | Extensible dict for behaviors to pass data downstream |
| `new_events` | Domain events collected during execution |

---

## 4.6 S5 — Fully Async with First-Class Fakes

### Decision

All public I/O-facing APIs are `async def`. No synchronous dual API exists. The `pydomain.testing` module ships complete, in-memory fake implementations of every `Protocol` interface — not stubs, not mocks, but **behaviourally correct doubles** that support the full lifecycle.

### Async everywhere

```
Repository.get_by_id()       → async def
Repository.save()            → async def
EventStore.append_to_stream() → async def
EventStore.read_stream()     → async def
UnitOfWork.commit()          → async def
MessageBus.dispatch()        → async def
SagaManager.handle()         → async def
SubscriptionRunner.start()   → async def
```

This forces a clean separation: I/O boundaries are always explicit. Synchronous domain logic (aggregate mutations, value object operations) remains `def` — only the edges are `async`.

### Fakes, not mocks

The `pydomain.testing` module provides:

| Fake | Replaces | Key Behaviour |
|------|----------|---------------|
| `FakeRepository[T, TId]` | `Repository[T, TId]` | In-memory dict storage; optimistic concurrency checking; event collection |
| `FakeUnitOfWork` | `UnitOfWork` | Commit/rollback lifecycle; correlation/causation stamping; event collection from repositories |
| `FakeEventStore` | `EventStore` | In-memory event stream storage; append-only; version checking; global log for subscriptions |
| `FakeSnapshotStore` | `SnapshotStore` | In-memory snapshot storage; load/save by aggregate ID |
| `FakeCheckpointStore` | `CheckpointStore` | In-memory position tracking for subscriptions |
| `FakeSagaRepository` | `SagaRepository` | In-memory saga state persistence |
| `FakeProcessedCommandStore` | `ProcessedCommandStore` | In-memory idempotency tracking |
| `FakeLockProvider` | `LockProvider` | In-memory lock acquisition (no-op for single-threaded tests) |
| `InMemoryMessageBroker` | `MessageBroker` | Captures published integration events for assertions |
| `InMemoryProjectionStore` | `ProjectionStore` | In-memory read model storage |

### Why fakes over mocks

1. **Behavioural correctness.** `FakeUnitOfWork` commits events exactly like the real one — correlation ID stamping, publish-after-commit semantics, rollback on exception. Tests verify real behaviour, not mock setup.
2. **No library coupling.** Tests import `pydomain.testing`, not `unittest.mock`. The test code doesn't depend on mock library internals.
3. **Refactoring safety.** Renaming a method on `Repository` breaks the `FakeRepository` (same module recompiled), not an isolated mock string somewhere in a test file.
4. **Speed.** All fakes are in-memory, single-threaded. Test suites run in milliseconds.

### Testing convention

Tests use `bootstrap()` with fakes — the same composition root as production:

```python
# Production
app = await bootstrap(event_store=PostgresEventStore(...), ...)

# Tests
app = await bootstrap(event_store=FakeEventStore(), ...)
```

Handler code is identical in both contexts. Only the wiring changes.

---

## 4.7 Strategy Interactions

The five strategies are not independent — they reinforce each other:

```
                    ┌─────────────────┐
                    │  S1: BaseModel   │
                    │  (data backbone) │
                    └────────┬────────┘
                             │ inherits
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌────────────┐  ┌────────────┐
     │ S2: Protocol│  │ S3: Generic│  │ S5: Async  │
     │ (interfaces)│  │ (type bind)│  │ (lifecycle)│
     └──────┬─────┘  └──────┬─────┘  └──────┬─────┘
            │               │               │
            └───────────────┼───────────────┘
                            ▼
                    ┌───────────────┐
                    │ S4: Pipeline  │
                    │ (cross-cutting)│
                    └───────────────┘
```

- **S1 + S3**: `Command[TResult]` is a `BaseModel` whose `TResult` binds the handler return type. Pydantic validates the command; generics type the result.
- **S2 + S5**: `Protocol` interfaces are all async. Fakes implement the same async signatures. Structural subtyping means the fake doesn't inherit from the protocol — it just satisfies it.
- **S3 + S4**: `MessageContext` carries the typed message. `PipelineBehavior` wraps the handler without knowing the concrete type. The bus resolves the generic binding.
- **S2 + S4**: Pipeline behaviors are `Protocol` too — users add custom behaviors without inheriting from a library base class.
- **S1 + S5**: Fakes use the same Pydantic serialization (`model_dump()` / `model_validate()`) as production code. Event round-trips are tested end-to-end.

---

## 4.8 Summary — What the Strategies Enable

| Strategy | Enables | Prevents |
|----------|---------|----------|
| **S1: BaseModel** | Validation, serialization, JSON Schema, IDE support out of the box | Custom serialization layers, manual validation, schema drift |
| **S2: Protocol** | Any infrastructure, no coupling, easy test doubles | Vendor lock-in, base-class inheritance chains, import cycles |
| **S3: Generics** | Typed `dispatch()` results, typed repositories, compile-time safety | `Any` returns, runtime type errors, downcasting |
| **S4: Pipeline** | Composable cross-cutting concerns, clean handlers | Scattered idempotency/locking logic, handler pollution |
| **S5: Async + Fakes** | Millisecond tests, real behaviour verification, `bootstrap()` parity | Slow integration tests, mock brittleness, environment-dependent tests |
