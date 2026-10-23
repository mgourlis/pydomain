# 9. Design Decisions

This section records the key architectural decisions that shaped `pydomain`, explaining **why** each choice was made and what alternatives were considered. Each decision follows the format: context → decision → rationale → consequences.

---

## 9.1 Protocol over ABC for Interfaces

### Context

The library defines behavioural contracts: `Repository`, `EventStore`, `SnapshotStore`, `MessageBroker`, `UnitOfWork`, `PipelineBehavior`, `Factory`, `Projection`, and many others. These are consumed by infrastructure adapters written by library users.

### Decision

Use `typing.Protocol` with `@runtime_checkable` for all behavioural interfaces. Reserve `ABC` + `abstractmethod` only for base classes that provide default behaviour (`EventSourcedAggregateRoot`, `EventSourcedProjection`, `Specification`).

### Rationale

| Consideration | `Protocol` (chosen) | `ABC` (rejected) |
|---------------|---------------------|-------------------|
| Coupling | None — structural subtyping | Requires explicit inheritance |
| Multiple interfaces | Naturally — any class with matching methods conforms | MRO complications, diamond problem |
| User code impact | Zero — existing classes automatically conform | Must subclass the ABC |
| `isinstance()` support | Via `@runtime_checkable` | Native |
| Default behaviour | Cannot provide | Can provide via concrete methods |

The decisive factor: `pydomain` users own their infrastructure adapters. A `SqlAlchemyOrderRepository` should not be forced to inherit from a library ABC — it may already inherit from a SQLAlchemy mixin or a project-specific base. `Protocol` respects this by requiring only method signatures, not inheritance.

### Where ABC is used instead

`ABC` is used only where the base class provides **substantial default behaviour** that subclasses build on:

- `EventSourcedAggregateRoot` — provides `_apply()`, `_replay()`, `_take_snapshot()`, and `pull_events()`. The only abstract method is `_when()`.
- `EventSourcedProjection` — provides `handle()` (convention dispatch), `apply()`, `rebuild()`, and checkpoint tracking. Subclasses only add `_when_*` methods.
- `Specification` — provides `and_()`, `or_()`, `not_()` composition operators. Subclasses implement `is_satisfied_by()`.

---

## 9.2 Pydantic v2 Only — No v1 Compatibility Shims

### Context

Pydantic v1 and v2 have incompatible APIs. Many existing Python projects still use v1. The library could support both via compatibility shims.

### Decision

Target `pydantic >= 2.12.5` exclusively (2.7 until ADR-075 needed `exclude_computed_fields`; 2.12.5 is the latest 2.12 patch, verified by the floor CI job on Python 3.12 and 3.14). No v1 compatibility layer.

### Rationale

- **API surface is fundamentally different**: v2 uses `model_config`, `model_dump()`, `model_validate()`, `@field_validator`, `@model_validator`. v1 uses `Config`, `dict()`, `parse_obj()`, `@validator`, `@root_validator`. A shim layer would be large, fragile, and would prevent using v2-only features (generics with `__init__`, `computed_field`, `PrivateAttr` semantics).
- **PrivateAttr behaviour differs**: `AggregateRoot._pending_events` relies on Pydantic v2's `PrivateAttr` semantics (excluded from `model_dump()`, excluded from equality). The v1 equivalent (`Field(exclude=True)`) behaves differently.
- **Performance**: Pydantic v2 is significantly faster due to its Rust core. A compatibility layer would force v2 users onto v1 code paths or require maintaining two implementations.
- **Migration is one-way**: The Python ecosystem is moving to v2. Supporting v1 would commit to a shrinking user base and increasing maintenance burden.

### Consequences

- Users on Pydantic v1 must migrate to v2 to use `pydomain`.
- The library can freely use v2-only features without conditional logic.

---

## 9.3 Two Separate Projection Types by Naming Convention

### Context

The library has two projection abstractions: `Projection[StateT]` in `pydomain.cqrs` and `EventSourcedProjection` in `pydomain.es`. Both transform events into read models. The question was whether to use a single type hierarchy or two independent types.

### Decision

Two independent types with distinct names and module locations, sharing no inheritance.

### Rationale

The two abstractions serve fundamentally different purposes:

| Aspect | `Projection[StateT]` | `EventSourcedProjection` |
|--------|-----------------------|--------------------------|
| Module | `pydomain.cqrs` | `pydomain.es` |
| Type | `Protocol` | `ABC` |
| Concern | "What is a projection?" (contract) | "How do I build one from an event stream?" (mechanism) |
| Methods | `apply()`, `rebuild()` | `handle()`, `apply()`, `rebuild()` + `_when_*` dispatch |
| Checkpoint | None | Built-in counter |
| Dependencies | `DomainEvent` only | `DomainEvent` + versioning assumptions |
| Users implement | The entire `apply()` logic | Only `_when_{EventType}` methods |

A single hierarchy would create a **layer violation**: the CQRS layer (which has no event-sourcing knowledge) would either depend on the ES layer, or the ES layer would provide a CQRS-layer base class. Neither is acceptable — `cqrs` must not import from `es`, and `es` must not define CQRS contracts.

By separating them:
- `cqrs` remains independent of `es` (preserving modularity).
- A class can satisfy both simultaneously (structural subtyping for `Projection`, inheritance for `EventSourcedProjection`).
- Each module's projection type carries only the concerns relevant to its layer.

---

## 9.4 `isinstance` Dispatch in `_when()` — No Event Registry

### Context

`EventSourcedAggregateRoot._when(event)` must route each event to the correct mutation logic. The alternatives are: (a) `isinstance` checks in user code, (b) a registry mapping event types to handlers, (c) convention-based method dispatch like `_when_{EventType}`.

### Decision

Use `isinstance` checks in user-written `_when()` methods. Do not provide a registry or convention dispatch for aggregates.

### Rationale

**Aggregates are typically small.** A well-bounded aggregate handles 3–8 event types. An `isinstance` chain for 5 events is trivially readable:

```python
def _when(self, event: DomainEvent) -> None:
    if isinstance(event, OrderPlaced):
        self.status = OrderStatus.PLACED
    elif isinstance(event, LineItemAdded):
        self.line_items.append(event.line_item)
    elif isinstance(event, OrderCancelled):
        self.status = OrderStatus.CANCELLED
    else:
        raise ValueError(f"Unknown event: {type(event).__name__}")
```

**Why not a registry:**
- Adds infrastructure into the aggregate (violating the principle that aggregates are pure domain objects).
- Registration must happen at class definition or `__init__` time — coupling the aggregate to a registration mechanism.
- For 5 event types, a registry is more ceremony than value.

**Why not convention dispatch (`_when_OrderPlaced`):**
- This pattern *is* used for `EventSourcedProjection`, where a projection may handle 20+ event types from multiple aggregates. Aggregates are narrower in scope.
- Convention dispatch requires `getattr()` lookup on every event — unnecessary indirection for small handler counts.
- Explicit `isinstance` makes the aggregate's event contract immediately visible. There are no "hidden" handlers.

### Contrast with `EventSourcedProjection`

Projections *do* use convention dispatch (`_when_{EventType}`) because projections handle many more event types across multiple aggregate boundaries. The convention reduces boilerplate from `handle()` → `isinstance` chains that would span dozens of events.

---

## 9.5 Integration Events — Primitive-Only Payloads

### Context

`IntegrationEvent` crosses service boundaries via message brokers (RabbitMQ, Kafka, etc.). It must be serializable to any broker format without custom logic.

### Decision

Restrict `IntegrationEvent` fields to primitive types: `str`, `int`, `float`, `bool`, `dict`, `list`, `None`. Enforce this via `@model_validator` that inspects every field value.

*Amended by [ADR-074](../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md) (§9.25):* the check is strict JSON at any depth — dict values and list items too, `str` keys only, finite floats, no tuples or sets, at most 100 levels and no cycles — and reports the field, type and path, never the value.

Additionally, use `str` for `event_id` and `occurred_at` (instead of `UUID` and `datetime`).

### Rationale

**Broker serialization is unpredictable.** A message broker may use JSON, Avro, Protobuf, or a custom format. Complex Python types (`UUID`, `datetime`, `Decimal`) require custom serializers for each format. By restricting to JSON-native primitives, `model_dump()` always produces a broker-safe dict — no custom serialization needed.

**UUID → str**: Not all brokers or consumer languages have a native UUID type. A string representation is universally compatible.

**datetime → str (ISO 8601)**: Same rationale — ISO 8601 strings are the universal interchange format for timestamps. Consumer code parses to its native type.

**Model validator enforcement**: The restriction is enforced at construction time, not at serialization time. This provides early feedback — a developer who accidentally adds a `UUID` field gets an immediate error, not a serialization failure in production.

### Consequences

- Domain events and integration events have different type signatures for the same conceptual fields (e.g., `order_id: UUID` vs `order_id: str`).
- An explicit translation step is required: domain event → integration event. This is intentional — it forces the application layer to define the public contract separately from the internal domain model.

---

## 9.6 Saga State as AggregateRoot

### Context

`SagaState` tracks a long-running process: step history, processed events, compensation stack, lifecycle transitions. It needs persistence, optimistic concurrency, and event tracking.

### Decision

`SagaState` inherits from `AggregateRoot[UUID]`, gaining all aggregate capabilities.

### Rationale

The requirements for saga state storage are identical to aggregate storage:

| Requirement | Provided by `AggregateRoot` |
|-------------|---------------------------|
| Identity | `id: UUID` (auto-generated) |
| Optimistic concurrency | `version: int` (checked on save) |
| Event collection | `pull_events()` (lifecycle events) |
| Repository pattern | Works with any `Repository` implementation |
| Tracing | `correlation_id`, `causation_id` fields |

Building a separate persistence mechanism for saga state would duplicate the aggregate repository's logic (concurrency checking, event collection, transaction management). Inheriting from `AggregateRoot` provides all of this with zero additional code.

### Consequences

- Saga state repositories are standard `Repository[SagaState, UUID]` implementations — no special saga-specific persistence layer.
- The `SagaManager` uses a regular `SagaRepository` (a `Repository[SagaState, UUID]` with a `get_by_saga_type` extension) for state persistence.
- Saga state benefits from the same publish-after-commit semantics as domain aggregates.

### Alternative considered

A dedicated `SagaStore` protocol with load/save semantics. Rejected because it would duplicate optimistic concurrency, event collection, and unit-of-work integration that `AggregateRoot` already provides.

---

## 9.7 Saga `on()` DSL — Unified Command and Compensation Declaration

### Context

For each event a saga handles, the user typically needs to: (1) map the event to a command, (2) register a compensating action, (3) update the step name. These three concerns are tightly coupled — they always refer to the same event type.

### Decision

The `on()` DSL captures all three concerns in a single declaration:

```python
self.on(
    OrderCreated,
    send=lambda e: ReserveItems(order_id=e.order_id),
    step="reserving",
    compensate=lambda e: CancelReservation(order_id=e.order_id),
)
```

### Rationale

**Cohesion**: The forward command and its compensation are always paired. Separating them across different registration calls (e.g., `on_event()`, `on_compensation()`) creates temporal coupling — the developer must remember to register both and keep them in sync. A single `on()` call makes the pairing explicit and impossible to forget.

**Readability**: The entire behaviour for an event is visible in one place. Reading a saga's constructor shows the complete process flow as a sequence of `on()` calls:

```python
self.on(OrderCreated, send=..., step="reserving", compensate=...)
self.on(ItemsReserved, send=..., step="charging", compensate=...)
self.on(PaymentCompleted, send=..., step="shipping", complete=True)
```

This reads as a **declarative process definition**, which is the primary value of the saga pattern.

**Compensation stack ordering**: Because compensation records are pushed in the same call that produces the forward command, the LIFO compensation order naturally mirrors the forward step order. The last step to execute is the first to compensate — which is the correct semantic.

### Alternative considered

Separate `compensate()` registration:
```python
self.on(OrderCreated, send=lambda e: ReserveItems(...))
self.compensate(OrderCreated, lambda e: CancelReservation(...))
```

Rejected because: (1) temporal coupling between `on()` and `compensate()`, (2) the compensation factory references the same event type and fields — repeating them is noise, (3) the step-level grouping is lost.

---

## 9.8 Publish-After-Commit — Never Dispatch Uncommitted Events

### Context

Domain events are collected during command handling and need to be dispatched to event handlers, saga managers, and integration event publishers. The question is when to dispatch: immediately on `_add_event()`, after handler completion but before commit, or after commit.

### Decision

Events are only dispatched after `UnitOfWork.commit()` succeeds. The `CommandBus` returns collected events alongside the command result, and the `MessageBus` dispatches them afterward.

### Rationale

**Consistency guarantee**: If an event handler triggers side effects (sending emails, publishing to a message broker, dispatching integration events), those side effects must reflect committed state. Dispatching before commit means a handler could publish an `OrderPlaced` integration event, then the transaction rolls back — the external world now believes the order exists when it doesn't.

**Idempotency simplification**: With publish-after-commit, event handlers never see rolled-back state. This eliminates an entire class of duplicate/inconsistent events that would otherwise require additional deduplication infrastructure.

**Separation of concerns**: The aggregate's job is to enforce invariants and record events. The UoW's job is to persist and publish. The handler's job is to orchestrate. Mixing publishing into aggregate mutation would blur these boundaries.

### Failure isolation

Once events are dispatched, individual handler failures do **not** affect other handlers. The `EventBus` (`EventBus._execute()`, since ADR-062) catches and logs per-handler exceptions:

```
Event dispatched to 3 handlers
  Handler A → succeeds
  Handler B → raises exception (logged, caught)
  Handler C → succeeds (unaffected by Handler B)
```

This design recognizes that after commit, the event **happened**. One handler's failure should not prevent others from processing the event.

What follows the handlers depends on the path (ADR-081 point 1, §9.32). After a command, `dispatch_many` logs and swallows the failures: the command has committed, so its result is returned. A direct dispatch, `EventBus.dispatch(event)`, `MessageBus.dispatch(DomainEvent)` or `Application.dispatch(event)`, raises `EventHandlersFailed` once every handler has run, holding each handler's own exception, so the inbound gateway can NACK the message (§6.7).

---

## 9.9 Entity Auto-ID vs Explicit ID

### Context

Every `Entity[TId]` has an `id` field. The question is whether to auto-generate the ID or require explicit provision.

### Decision

When `id` is omitted, auto-generate it via the configured `IdGenerator[TId]` for **all** `TId` types. A runtime type guard raises `DomainError` if the generator produces a value that does not match the declared `TId` annotation.

### Rationale

**Universal auto-generation is simpler**: Previously, only `Entity[UUID]` subclasses got auto-generated IDs. This forced developers writing `Entity[int]` or `Entity[str]` to always provide `id` explicitly, even when a suitable generator existed. By making auto-generation universal with a runtime type guard, the library supports any ID scheme without special-casing.

**Runtime type guard as safety net**: The `IdGenerator[TId]` protocol is generic, but Python's generic type parameters are erased at runtime. The `model_validator(mode="before")` in `_ensure_id()` inspects the actual `TId` annotation and verifies the generated value matches. If `Uuid7Generator` (which produces `UUID`) is configured but an `Entity[int]` subclass is constructed, the mismatch is caught immediately with a clear `DomainError` message.

**Configurable generator**: The `IdGenerator` is configurable at startup via `Entity.configure(id_generator=...)`. This supports testing (deterministic IDs) and custom schemes (e.g., Snowflake `int`, prefixed ULID `str`). Subclasses can override `_id_generator` individually.

**The `model_validator(mode="before")` approach**: Auto-generation runs before Pydantic validation, so the `id` field is always populated by the time field validators run. This avoids the need for `Optional[UUID]` types or `Field(default_factory=...)` on every subclass.

### Consequences

- An `Entity[int]` subclass works with auto-generation if a `SnowflakeIdGenerator` is configured globally or per-subclass.
- If the default `Uuid7Generator` is used with a non-`UUID` entity, the runtime guard raises `DomainError` — not a silent type mismatch.
- Amended by [ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md) (§9.21): `NewType`, PEP 695 alias, `Annotated` and union ids are resolved recursively, an id type the guard cannot check raises `DomainError`, and the guard never writes into the caller's dict.

---

## 9.10 `Frozen=True` on Commands and Queries with `extra="forbid"`

### Context

Commands and queries are user input DTOs. They carry all data the handler needs.

### Decision

Both `Command[TResult]` and `Query[TResult]` use `frozen=True` and `extra="forbid"`.

### Rationale

**`frozen=True`**: A command represents an immutable intent. Once created, it must not be modified — the handler should see the exact input it was given. This prevents accidental mutation in handlers and enables safe caching of command instances.

**`extra="forbid"`**: Commands and queries are explicit contracts. If a developer passes an extra field (typo, wrong DTO), it should fail immediately rather than being silently ignored. This catches bugs at the boundary between the application layer and the handler:

```python
# typo in field name → immediate validation error
PlaceOrder(order_id=..., custoemr_id=...)  # raises ValidationError
```

**Consequence for deserialization**: When reconstructing commands from messages (e.g., in a saga), all fields must be present. The `hydrate_command()` helper in the saga module handles this by using the stored `data` dict and the resolved command class.

**Unknown-key stripping**: `hydrate_command()` filters out keys in `data` that are not declared on the target command class before calling `model_validate()`. This makes hydration resilient to schema evolution — the write side already validated when the command was first created, so extra fields from a newer schema are safely ignored by an older consumer. The `importlib`-based resolution returns `None` (no exception) if the module or type cannot be found, which handles cross-service commands gracefully.

---

## 9.11 `DomainService` as a Marker Class, Not a Base Class

### Context

Domain services hold business logic that doesn't belong to any single entity or value object. Python naturally favours standalone functions over classes for stateless operations.

### Decision

`DomainService` is a lightweight architectural marker with `__slots__ = ()` and no methods. It signals that a class belongs to the domain layer.

### Rationale

**Python favours functions.** A stateless domain operation is often clearer as a standalone function:

```python
def calculate_total_price(items: list[LineItem], tax_rate: TaxRate) -> Money: ...
```

Forcing this into a class hierarchy adds ceremony without value. However, some operations genuinely benefit from being methods on a service class — particularly when the service accepts injected dependencies:

```python
class PricingService(DomainService):
    def __init__(self, rate_provider: RateProvider) -> None:
        self._rate_provider = rate_provider

    def calculate(self, order: Order) -> Money:
        rate = self._rate_provider.get_rate(order.customer.tier)
        ...
```

The marker approach supports both styles without imposing either:
- Standalone functions are first-class citizens — no marker needed.
- Classes that *do* exist get a clear architectural signal via `DomainService`.

**`__slots__ = ()`** prevents accidental instance state, reinforcing the "stateless" contract.

---

## 9.12 No Generic ReadStore Protocol — User-Defined Read Contracts

### Context

The library provides `Repository[T, TId]` as a universal Protocol for write-side persistence (`save`, `get_by_id`). On the read side, query handlers need similar decoupling from infrastructure. The question is whether the library should provide a generic `ReadStore` Protocol analogous to `Repository`.

### Decision

The library does **not** provide a generic `ReadStore` Protocol. Users define their own domain-specific read store Protocols in the application layer, one per read model.

### Rationale

**`Repository[T, TId]` works because it has a universal interface.** Every aggregate needs the same operations: persist changes and load by identity. The method signatures are identical regardless of domain — `save(aggregate)` and `get_by_id(id) -> T`.

**Read models have no universal interface.** Each read model has fundamentally different query methods, shaped by the specific access patterns it serves:

```python
# Order list — indexed by customer, date
OrderListStore.find_by_customer(customer_id, limit)
OrderListStore.find_by_date_range(start, end)

# Inventory dashboard — indexed by stock level
InventoryDashboardStore.get_low_stock(threshold)
InventoryDashboardStore.get_reorder_summary()

# Customer search — full-text, paginated
CustomerSearchStore.search(query, page, page_size)
```

A generic `ReadStore` would either be:

1. **Too abstract** — just a `Callable` or `load(id) -> T`, which adds no value beyond what `ProjectionStore` already provides.
2. **Too opinionated** — dictating query semantics (filtering, pagination, sorting) that vary wildly between read models and storage backends.

Neither option is useful. Instead, the library follows the same principle as domain-specific repository extensions: users define their own Protocols tailored to their read model's access patterns.

### What the library provides

| Concern | Library provides | User provides |
|---------|-----------------|---------------|
| Simple state persistence | `ProjectionStore` Protocol + `InMemoryProjectionStore` fake | — |
| Complex denormalized read models | Pattern and layer discipline | Domain-specific `ReadStore` Protocols per read model |
| Handler composition | `QueryHandler[T, R]` Protocol + `QueryBus` | Concrete handler implementations |
| Test doubles | `InMemoryProjectionStore` | Per-read-model fakes (or reuse `InMemoryProjectionStore` for simple cases) |

### Consequences

- Query handlers remain infrastructure-free — they depend on user-defined Protocols, not SQL or database drivers.
- Each read model gets precisely the query methods it needs, with no unused generic abstractions.
- The `bootstrap()` composition root wires concrete read store implementations into handlers, just as it wires repositories into command handlers.

---

## 9.13 Declarative Saga Failure via `fail=True`

### Context

The `on()` DSL ([ADR-028](../adr/ADR-028-saga-on-dsl.md)) already supported declarative
lifecycle transitions for `suspend=True` and `complete=True`. Failure, however, required an
imperative handler that manually called `self.dispatch()` and `await self.fail()`. This was
the only lifecycle transition without a declarative equivalent. Additionally, reason and
description parameters (`compensate_description`, `suspend_reason`) only accepted static
strings, forcing developers to write imperative handlers whenever event data was needed in
the message.

### Decision

Add `fail: bool = False` and `fail_reason: str | Callable[[DomainEvent], str] | None = None`
to `Saga.on()`. When `fail=True`, the mapped handler dispatches the forward command then
calls `await self.fail(reason=f_reason, compensate=True)`. Also extend
`compensate_description`, `suspend_reason`, and `fail_reason` to accept
`Callable[[DomainEvent], str]` for dynamic message construction from event data.

### Rationale

**Symmetry**: All three lifecycle transitions (`suspend`, `complete`, `fail`) are now
declarative. The most common failure pattern — dispatch a notification, fail with context,
trigger compensations — is a single `on()` call with no handler boilerplate.

**Callable control**: When `fail_reason` is a callable, its return value is used as-is
(empty string is valid). The `or "Saga failed"` fallback only applies to static strings.
This gives callables full control while providing sensible defaults for static usage.

**Mutual exclusion**: `fail=True` is mutually exclusive with both `complete=True` and
`suspend=True`, enforced at registration time via `SagaConfigurationError`.

### Consequences

- The `handler=` parameter path ignores `fail=True` entirely — handler-style users call
  `self.fail()` directly. This is consistent with how `send`/`compensate`/`suspend` are
  already ignored in handler mode.
- Failure always triggers compensation (`compensate=True`). Sagas needing to fail without
  compensation still require an imperative handler.
- The `on()` method signature is denser with two additional parameters.

> **Amended by [ADR-077](../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md) (§9.28).** A `fail=True` step now keeps its forward command: `execute_compensations()` sets queued forward commands aside, and the `SagaManager` sends them before the compensations.

---

## 9.14 Declarative Resume Authorization — `resumes_from` and `should_resume`

### Context

The `should_resume()` method was the only mechanism for filtering which events could wake a
suspended saga. For sagas with multiple suspension points, this forced an unwieldy
`if/elif` chain checking `self.state.current_step`. The intent of "this event resumes from
this step" was lost in imperative logic, and adding a new suspension point required
modifying a central method — coupling unrelated saga steps.

### Decision

Add `resumes_from: str | list[str] | None` and `should_resume: Callable[[Any], bool] | None`
to `Saga.on()`. Store them in `_resume_map` (event type → set of authorized step names) and
`_resume_predicates` (event type → inline predicate). Upgrade the base `should_resume()`
with three-tier evaluation: step authorization → inline predicate → fallback `True`.

### Rationale

**Locality**: The resume authorization sits next to the event handler in the `on()` call.
Reading one declaration tells you everything about that event's role — what command it
dispatches, which step it resumes from, and what predicate gates it.

**Step isolation**: Each suspension point is independent. Adding a new suspension point
with new events does not require touching existing `on()` calls or a central method.

**Backward compatibility**: Subclass overrides of `should_resume()` completely bypass the
base logic. Existing sagas are unaffected. When no `resumes_from` is registered on any
`on()` call, `_resume_map` is empty and the base method skips directly to the fallback —
preserving the pre-ADR-056 behavior.

**Empty map gate**: The `if self._resume_map:` check ensures that sagas without any
`resumes_from` registrations behave identically to the pre-ADR-056 default — all events
are allowed to resume from any step.

### Consequences

- An empty `resumes_from=[]` creates a permanently-blocked event (empty set can never match
  any step), which is intentional and test-covered.
- The three-tier evaluation (map → predicate → fallback) must be understood by developers
  debugging resume behavior.
- Inline predicates are scoped to a single event type — no `isinstance` check needed
  inside the predicate, unlike the global `should_resume()` override.

---

## 9.15 Class-Level Default Timeout with Sentinel and Step Overrides

### Context

ADR-034 introduced per-step `suspend_timeout`. But sagas with many suspension steps had no
global default — developers repeated the same timeout on every `on()` call. Additionally,
the parameter default of `None` was ambiguous: `suspend_timeout=None` could mean "use the
default" or "explicitly infinite." When a class-level default exists, there was no way to
express "override the default to be infinite" at a specific step.

### Decision

Add `default_timeout: ClassVar[timedelta | None] = None` to `Saga`. Define a module-level
sentinel `USE_DEFAULT_TIMEOUT = object()`. The `suspend_timeout` parameter defaults to the
sentinel (not `None`). Resolution uses identity check (`is`): if the sentinel, fall back
to `self.default_timeout`; otherwise pass through (including explicit `None` for infinite).

### Rationale

**Sentinel vs `None` as default**: `None` is both a valid timeout value ("infinite") and
Python's natural "not provided" default. A unique sentinel object compared by identity
disambiguates these two cases without affecting truthiness-based logic.

**Class-level default**: A global SLA set once at the class level, overridden only where
needed, follows the DRY principle and makes the saga's timeout policy visible in one place.

**Three-tier flexibility**:
| Usage | Meaning |
|-------|---------|
| Omit `suspend_timeout` | Use `default_timeout` (global SLA) |
| `suspend_timeout=timedelta(hours=1)` | Local override |
| `suspend_timeout=None` | Infinite (override even a non-None default) |

### Consequences

- `default_timeout` defaults to `None` — matching pre-ADR-057 behavior where omitted
  timeout means infinite. Existing sagas are unaffected.
- Each saga class has its own `default_timeout` — no cross-class leakage.
- The `suspend_timeout` type annotation widens to `timedelta | None | object`, weakening
  static type checking at the call site.
- `default_timeout=timedelta(0)` creates immediate expiry — valid for sagas that should
  never wait.

---



## 9.16 EventBus — Application Layer Peer

### Context

The library had three message types (Commands, Queries, Domain Events) but only two buses in the application layer. Event dispatch was inlined in `MessageBus` (infrastructure facade, per ADR-045), creating architectural asymmetry.

### Decision

Extract `EventBus` into `src/pydomain/cqrs/event_bus.py` as a concrete class and peer of `CommandBus`/`QueryBus`. `MessageBus` gains an optional `event_bus` parameter and delegates all event operations to it.

Idempotency for events is handled by `EventIdempotencyBehavior`, a pipeline behavior consistent with the existing `IdempotencyBehavior` for commands. The `ProcessedCommandStore` protocol is renamed to `ProcessedMessageStore` and gains an atomic `check_and_set()` method to serve both.

### Rationale

- **Consistency** — All three message bus types now live in the application layer.
- **Opt-in idempotency** — Event handlers choose whether to be idempotent via a pipeline behavior, matching the command-side pattern.
- **Unified store** — `ProcessedMessageStore` covers both commands (result caching) and events (mark-only), reducing protocol surface area.

### Consequences

- `EventBus` can be used standalone without `MessageBus`.
- Event idempotency is per-handler, not blanket all-or-nothing.
- Logger namespace for event errors shifts from `pydomain.message_bus` to `pydomain.event_bus`.

---

## 9.17 Outbound Event Gateway and Transactional Outbox

### Context

The inbound messaging path is complete: `MessageSubscriber` → `InboundEventGateway` → `MessageBus`, with hydration, Anti-Corruption Layer translation, tracing propagation, and a documented ACK/NACK convention (ADR-059, ADR-060, ADR-061).

The outbound path has no implementation. `MessageBroker` is a Protocol (ADR-051) and its lifecycle is managed by `bootstrap()` (ADR-047), but `publish()` has **no call site anywhere in the library**. `AbstractUnitOfWork._write_outbox()` is documented as an extension point and is a no-op, overridden nowhere. ADR-051 records the consequence as a known negative: *"The broker does not participate in the Unit of Work — publishing is fire-and-forget."*

Three forces shape the solution: atomicity between committed state and published events; the type boundary between `DomainEvent` (rich) and `IntegrationEvent` (primitives only, ADR-022); and consistency with ADR-060, which explicitly rejects `EventRegistry` for gateway type resolution.

### Decision

Translation from `DomainEvent` to `IntegrationEvent` happens at **write** time, inside `_write_outbox()`, within the same transaction as the aggregate state change. The outbox persists serialized `IntegrationEvent`s. `OutboundEventGateway` is a polling relay that fetches unpublished rows, rehydrates them by `topic` via `model_validate`, publishes through `MessageBroker`, and marks them published.

- `OutboxStore` is a five-method `@runtime_checkable` Protocol: `append`, `fetch_unpublished`, `mark_published`, `mark_failed`, `mark_dead_lettered`. Atomicity and concurrency safety are the **implementation's** documented responsibility, mirroring `ProcessedMessageStore`. Claim mechanics such as `SELECT ... FOR UPDATE SKIP LOCKED` live in the adapter, not the contract.
- `OutboundEventRegistry` offers synchronous, I/O-free registration keyed by **domain type**, with the topic as an output. Re-registration overwrites, enabling integration-event version hot-swap — the property ADR-060 established for the inbound registry. A translator returning `None` keeps the domain event internal.
- The gateway resolves the integration class from the outbox row's topic — the flat payload pattern of ADR-022 and ADR-060. It never imports a domain type.
- The gateway is **not** an `EventBus` handler. After a command, `EventBus` logs and swallows a handler's exceptions (ADR-046; a direct dispatch raises `EventHandlersFailed`, ADR-081), which is correct for in-process reactions and wrong for a non-replayable side effect.
- `bootstrap()` starts the gateway after `broker.start()`; `Application.shutdown()` drains the gateway before `broker.stop()`.
- *Amended by [ADR-074](../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md) (§9.25):* entries are marked one by one, a fetch claims its rows, a topic has one integration class, and the relay applies the entry's tracing at delivery.

| Failure mode | Behaviour | Recovery |
|---|---|---|
| Broker unavailable / confirm timeout | `mark_failed` with backoff | Retry until the budget is spent |
| Broker nack | `mark_failed` with backoff | Retry until the budget is spent |
| Unknown topic (no registration) | Log error; **do not** mark published | Retry until the budget is spent |
| Payload fails `model_validate` | Log error; **do not** mark published | Retry until the budget is spent |
| Retry budget exhausted | `mark_dead_lettered` at `ERROR` level | Dead-lettered — replayable after a fix |

### Rationale

`IntegrationEvent` is primitives-only by its own validator, so `model_dump()` is JSON-safe with no conversion layer, and the outbox row *is* the wire format — replay-stable across deploys and processable by a relay that never imports a domain type.

Translation at write time also keeps the two rejected alternatives rejected: read-time translation would re-interpret already-written rows with later code (replay instability in an at-least-once queue) and would require `EventRegistry`-based type resolution, which ADR-060 rules out on principle.

### Consequences

- Committed state and its outbound events are persisted atomically — no dual-write window.
- Broker outages become backpressure rather than data loss; rows drain when the broker returns.
- The outbox doubles as a durable audit record of everything published.
- Delivery is at-least-once, so consumers must deduplicate on `message_id`.
- Translators **must be pure** — they run inside the database transaction.
- An `OutboxUnitOfWork` ABC was deliberately **not** provided: ADR-001 rejects forced extra inheritance, so the library ships the registry and the port, and the user's `_write_outbox()` is two lines of composition.

---

## 9.18 Outbox Retry Policy and Dead-Letter Queue

### Context

ADR-063 fixed the outbound retry policy as unbounded: every failure is requeued with capped exponential backoff and **never** dead-lettered, so that a later deploy registering a missing translation could still deliver previously undeliverable rows. The same ADR recorded the cost under its own Negative section: there is no dead-letter state and no max-attempts policy, and a row that can never be delivered stays in the fetchable set forever.

Three forces made that trade-off no longer acceptable. Unbounded retry has **no terminal state**, so "will succeed next pass" and "will never succeed" are indistinguishable and there is nothing to alert on. A permanently-failing row is a **permanent tax on every pass**, consuming one of the `batch_size` slots in every fetch and starving newer, deliverable rows. And the recoverable-versus-terminal distinction the policy relies on **is not derivable from the failure**: the exception type is a property of the adapter, not of the message's future deliverability.

### Decision

Retry is bounded by `OutboundEventGateway.max_attempts` (default `10`; `None` restores ADR-063's retry-forever policy). An entry that fails that many deliveries is **dead-lettered** instead of rescheduled.

- `OutboxStore` gains a fifth method, `mark_dead_lettered(message_id, *, error)`. `mark_failed` means "will retry"; the relay otherwise has no way to express "will not". A dead letter is never returned by `fetch_unpublished` again and is never reported as published.
- The budget is **uniform** — the relay does not classify failures, because it cannot know which are recoverable. Unknown topic, validation error, and broker error each consume one attempt from the same budget, so a genuinely broken row reaches a terminal state while every recoverable failure still gets the full budget to recover in.
- Exhaustion is logged at `ERROR`. Dead-lettering is the one outcome where the library stops trying, so it must never be silent.
- Dead letters stay **operator-visible and replayable**: the library removes them from the relay's working set but does not delete them. Whether they live behind a status column or in a separate store is an adapter decision.
- *Amended by [ADR-074](../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md) (§9.25):* `max_attempts` below `1` and the other invalid relay arguments raise `ValueError`.

### Rationale

A bounded budget is the only contract the relay can honour honestly. Classification (`RetryableError` / `TerminalError`) would require every broker adapter to implement a library-defined hierarchy correctly, and a mis-classified terminal error destroys a recoverable message on the first failure — an unbounded loss traded for a bounded delay. Sentinel-based dead-lettering through `mark_failed` was rejected because a sentinel `next_attempt_at` overloads one method with two meanings the store cannot distinguish.

### Consequences

- The relay always makes progress: a dead letter stops occupying a batch slot, so deliverable rows are no longer starved.
- Giving up on a message becomes an explicit, `ERROR`-logged event that alerting can bind to, and dead letters form a finite triage list.
- `OutboxStore` is now the largest storage port in the library at five methods — accepted because dead-lettering is a genuine second terminal outcome, not a convenience.
- Dead-lettering converts silent retry into an **operational obligation**: a dead letter nobody watches is worse than a row that keeps retrying.
- The uniform budget can terminate a row a later deploy would have made deliverable — the exact scenario ADR-063 protected against, now bounded rather than eliminated.

---

## 9.19 Upcast Events to the Class Version Exactly Once

### Context

ADR-042's chain stopped where no upcaster was registered, and `EventRegistry.deserialize` validated whatever the chain produced. The payload kept its stored `event_version`, so a re-serialized event was upcast again on the next read; a missing step was indistinguishable from "already current"; and a broad `except KeyError` turned validator bugs into `GenericDomainEvent` fallbacks (audit rows INF-14, ES-15, ES-14).

### Decision

With an `UpcasterRegistry` attached, `deserialize` tracks the version through the chain, writes it to the payload's `event_version`, and raises `UpcastError` when a chain from an older stored version stops short of the class's version (the default of its `event_version` field, or 1 without one) or goes past it. A stored version newer than the class (a rolling deploy) is validated unchanged, with no upcaster run and its own `event_version` kept. A stored version that is a string of digits is read as that `int`; any other stored version that is not an `int`, or a class that declares `event_version` without a default, raises `UpcastError`. A payload `event_version` that disagrees with the envelope's version, and a digit string too long to read, raise `UpcastError` too ([ADR-078](../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md), §9.29). Only `resolve()`'s `KeyError` means "unregistered". `UpcasterRegistry.register` refuses an upcaster without `source_type`, `source_version` or `target_version` (`TypeError`), a downgrade or a self-step (`ValueError`), and a second upcaster for the same step (`ValueError`).

### Rationale

The registry knows every hop's target version, so it, not each upcaster, is the right place to set the final version. Only `EventRegistry` knows both the chain and the target class, so the gap check lives there. Refusing bad registrations moves the failure to startup.

### Consequences

- A read-serialize-read round trip upcasts each stored event once.
- A stored event without a complete chain to the class's version now raises on read instead of validating with defaults; the fix is to register the missing (possibly pass-through) upcaster.
- Without an `UpcasterRegistry`, versioning stays opt-in and unchanged.
- Readers older than the writer keep working: a newer stored version is left to validation, following `hydrate_command()`, which strips unknown keys from saga commands written by a newer schema (arc42 §8.10 and §9.10).
- A class that redeclares `event_version: int` without a default can no longer be read through an `UpcasterRegistry`; it needs a default.

---

## 9.20 Rollback Drains Event Buffers and Is Idempotent

### Context

ADR-005 promises that handlers never see rolled-back state, and ADR-020 that the `CommandBus` owns the unit of work and wraps a failure in `CommandExecutionError`. The 0.3.0 audit found both broken whenever a repository outlives one unit of work, which the docs teach: `rollback()` cleared only the unit of work's own list, so a refused command's events stayed in the repository's buffer and the next commit published them under its own tracing IDs (CQRS-K5, FAKE-03, ES-24). Rollback also ran twice per failed dispatch, a raising rollback replaced the `CommandExecutionError` (CQRS-06), a clean exit without `commit()` did not roll back (CQRS-24), `collect_events()` handed out the internal list (CQRS-08), and a second `commit()` in one scope wrote the outbox and committed again (CQRS-10).

### Decision

- `rollback()` calls the new `_rollback()` storage hook, then drains every repository's buffer through `pull_events()` and discards the events, then starts a new scope. The drain and the reset run in `finally` blocks, so a raising hook or repository cannot skip them; every repository is drained even if one raises.
- `__aexit__` rolls back whenever the scope has not committed, with or without an exception. A committed scope is not rolled back but still drains its repositories on exit, so a change staged after the commit never reaches a later scope.
- `collect_events()` returns a copy.
- `CommandBus.dispatch()` leaves the rollback to `__aexit__` and wraps the failure outside the `async with` block, so a failed dispatch rolls back once and always raises `CommandExecutionError` with the original failure as `__cause__`. When the clean-up on exit fails too, a note (`clean-up also failed: <type>`) names its exception type, never its message. This amends ADR-020's description of the bus.
- A second `commit()` in one scope raises `UnitOfWorkAlreadyCommittedError` before any hook runs (D-CQRS-10). A handler's own first commit stays persisted and is not rolled back; a retry after a failed commit needs `rollback()` first.
- When the body and the rollback both raise, `__aexit__` lets the rollback's exception propagate with the body's as `__context__` (Python's default); a cancellation also passes through unwrapped, after one rollback. A cancelled dispatch whose clean-up fails now raises the cancellation itself, with a type-only note, and the storage error wins over a drain error ([ADR-078](../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md), §9.29).
- `FakeUnitOfWork` resets its rollback record on entry.

### Rationale

`pull_events()` is already in the `Repository` Protocol and is what `commit()` uses, so draining needs no new adapter method. Removing the bus's own rollback, rather than guarding a second call with a flag, is the only way to stop a subclass's storage rollback running twice per failed dispatch. The `_rollback()` hook puts the `try`/`finally` around the drain in the library, instead of in every adopter's override. Raising on a second commit makes a handler that commits against ADR-020 fail loudly instead of duplicating outbox rows; its committed events are never dispatched in-process, because the bus raises before it returns them.

### Consequences

- A refused command's events, and a change staged after a commit, are never published, whether the repository lives in the unit of work or outlives it: a committed scope drains its repositories on exit too.
- Storage rollback moves from overriding `rollback()` to the `_rollback()` hook; adapters that override `rollback()` should move that code.
- An exception from the committed-exit drain of a successful dispatch propagates unwrapped and no events are returned, though the commit persisted.
- Sharing a repository is safe only between units of work that run one after another; for concurrent dispatches, build the repository per unit of work (§11.8). `AggregateLockingBehavior` does not make sharing safe: its locks are per aggregate key, so commands on different aggregates still share the buffer, and although it holds them through the commit (ADR-079), it still releases them before the exit drain (B-19, F-6).
- Behaviour changes adopters may notice are listed in the upgrade notes: exit without commit calls `rollback()`, a second commit raises, `collect_events()` returns a new list each call.
- Undoing a `FakeRepository` save on rollback is not part of this decision; it belongs to the repository version and copy rule (W2-6).

---

## 9.21 DDD Copies, Identity Types, Event Stamping and Specifications

### Context

The 0.3.0 audit found small defects in the `pydomain.ddd` building blocks that contradicted ADR-007, ADR-011,
§8.11 and the specifications page, and left copy ownership (ADR-050) undecided: a shallow copy of an aggregate
shared its pending-event list with the original, the id guard raised a raw `TypeError` for `NewType`, alias and
`Annotated` ids, `stamp()` built events that failed to revalidate and shared their payload with the original, and
`DomainEvent`, `Command` and `Query` ignored a per-subclass `_id_generator`. Composite specifications overflowed
the stack on deep chains and dumped their children as empty objects (ADR-010).

### Decision

Every copy of an aggregate owns its event buffer. The id guard resolves `NewType`, aliases, `Annotated` and
unions recursively, raises `DomainError` for an id type it cannot check, and never writes into the caller's dict. `DomainEvent`, `Command` and `Query` fill their ids from `cls._id_generator`, as `Entity`
does. `stamp()` validates the two ids alone against their fields, raising `pydantic.ValidationError` for a
non-UUID, then returns a deep copy; no model validator of the event runs. Composite specifications are evaluated with an explicit stack, and their
dumps contain each child's fields; there is no round trip back to the leaf types, which is deferred to a Wave 2
Feature (backlog F-1).

### Rationale

Each change restores what an ADR or a docs page already promised, with the smallest mechanism the library already
uses elsewhere: the before-validator of ADR-007 for ids, Pydantic's copy hooks for the buffer, and the model's
own field validation for `stamp()`.

### Consequences

- A copy and its original both hold the events pending at copy time; saving both publishes them twice.
- `stamp()` costs a deep copy per event and raises for ids it used to accept.
- A composite specification's dump cannot be loaded back yet.
- Specification depth is bounded by memory, not by the recursion limit.

---

## 9.22 Library-Owned Failure Paths Never Carry Payload Values

### Context

The library carries user payloads that can hold personal data. The 0.3.0 audit (INF-10, INF-15) found their values in text pydomain itself builds: the outbox relay stored and logged `str(ValidationError)`, whose text lists every failing `input_value`; the inbound gateway logged the same error with its traceback; `hydrate_command` and `UpcastError` embedded the failing exception's text; the saga manager stored `str(exc)` in `state.error` and `failed_compensations`; and `OutboxEntry.payload`, `GenericDomainEvent.data`, `CompensationRecord.data` and two `SagaState` fields printed in `repr()`.

### Decision

- One private helper, `pydomain._describe.describe(exc)`, gives one line: for a `ValidationError` the type, model, error count and each location with its Pydantic error code (`ValidationError: 2 errors for X at amount (int_parsing), invoice_id (missing)`); for any other exception its type name only. Never an input value, a Pydantic `msg` or `str(exc)` (§8.13).
- The relay's stored errors and log lines, the inbound validation log, both `hydrate_command` warnings, the `UpcastError` that wraps a failing `_transform` (still chained `from` it), and the saga manager's stored errors (including the suspension reason of a failed dispatch) use it. Broker and store failures are logged without a traceback, and the inbound "invalid tracing IDs" warning names the fields that failed to parse, never their values.
- `OutboxEntry.payload`, `GenericDomainEvent.data`, `CompensationRecord.data`, `SagaState.pending_commands` and `SagaState.failed_compensations` are declared `Field(repr=False)`, which hides them from `repr()` and `str()` but not from attributes or `model_dump()`.
- Dict keys, unknown keys, a validator's `PydanticCustomError` type and a configured model `title` appear in descriptions and are not protected. Exceptions raised by user code keep their own text; handler-failure logs (ADR-046), including a `SubscriptionRunner`'s batch-failure traceback, are decided in W2-3.

### Rationale

Stored errors are data at rest next to the row they describe; they outlive the payload and are read by operators who need the failing field, not its value. One helper keeps the rule in one place, and Pydantic's own `repr=False` hides a field without a custom `__repr__`. Pydantic's error codes come from a fixed vocabulary and carry no input.

### Consequences

- Stored error texts and log lines change shape (upgrade notes); `state.error` holds an exception's type name.
- The relay no longer logs a broker's or store's stack trace.
- Operators see which fields failed; reproducing a failure needs the payload itself, read under the application's access rules.
- The content sentinel tests (`tests/test_sensitive_payloads.py`) guard every path listed above (§10.6).

---

## 9.23 Shipped Fakes Conform to Their Protocol Contracts

### Context

The 0.3.0 audit found two fakes in `pydomain.testing` that broke their own docstrings or ADR-041:
`InMemoryMessageSubscriber.simulate_message()` returned silently for a topic with no handler although it
promised `KeyError` (FAKE-09), and `FakeEventStore` recorded a `command_id` before the version check, so the retry
ADR-041 prescribes after a `ConcurrencyError` raised `DuplicateCommandError` (ES-03). `FakeEventStore` also
settled two cases the `EventStore` Protocol left open in a way no real store does: an empty append created a
stream (ES-04), and a negative `from_version` read from the end of the stream or the global log (ES-05).

### Decision

The fakes keep the promises of their docstrings and governing ADRs, and every promise that belongs to a
Protocol is pinned by a case in its `*Contract` suite (ADR-067). `simulate_message()` raises `KeyError` for an
unknown topic. `FakeEventStore` records a `command_id` only when the append stores events. `read_stream` and
`read_all` raise `ValueError` for a negative `from_version`, `read_stream` before the stream lookup, which the
`EventStore` Protocol now states (D-ES-05). An empty append runs the duplicate and version checks and, when they pass,
stores nothing, creates no stream and records no `command_id`; the Protocol states this too. The
`MessageSubscriber` Protocol gains no case, because `simulate_message()` is not part of it.

### Rationale

A fake that is looser or stricter than its Protocol makes tests pass or fail for reasons production never sees.
Contract cases make the same promises executable against adopters' adapters, so the next drift shows up as a
failing case.

### Consequences

- `EventStoreContract` gains a retry case, two negative-offset cases and five empty-append cases; an adopter's
  store that slices a negative offset, or creates a stream on an empty append, fails them until it changes.
- Tests that relied on the old fake behaviour change (upgrade notes).

---

## 9.24 Safe Bootstrap Wiring and the Application's Public Accessors

### Context

ADR-047 makes `bootstrap()` the composition root and tells users to register handlers on the bus after bootstrap. The 0.3.0 audit found that `bootstrap(inbound_gateways=[gw])` without `message_bus=` builds a bus the gateway does not use, so inbound events are lost with no error (INF-16, taught by the inbound how-to, A27); that `Application` has no `message_bus` or `event_registry`, which 15 docs pages call (INF-17, A26); that a start failing part-way leaves the started consumers running (INF-06); and that one failing stop skips the rest of `shutdown()` (INF-07).

### Decision

- `Application` gets read-only `message_bus` and `event_registry` properties; built without a registry, it creates one, so `event_registry` is never `None`. `InboundEventGateway` exposes its bus as a read-only `message_bus`.
- `bootstrap()` raises `ValueError`, naming the gateway's index and type, when a gateway's bus is not the application's bus, including when `message_bus=` is omitted, or when one gateway is listed twice (B-46). The check runs before anything starts.
- `Application` stores a copy of `inbound_gateways` and returns a new list each time (B-47).
- `shutdown()` runs every stop in the existing order: inbound gateways, then the outbound gateway before the broker it publishes through ([ADR-063](../adr/ADR-063-outbound-event-gateway-and-outbox.md) point 7). The first `Exception` is re-raised after the last stop, with a note `clean-up also failed: <type>` for each later one, naming the type only. A `BaseException` such as a cancellation propagates at once.
- A start that raises stops the parts started before it, in shutdown order, never the part that failed, and re-raises the start error with the same notes. A cancellation during that clean-up does not interrupt it: `bootstrap()` finishes it, then re-raises the cancellation with the start error as context (B-45).
- A `bootstrap()` cancelled while starting does the same clean-up inside `asyncio.shield`, then re-raises the cancellation with the same notes (B-40). A `KeyboardInterrupt` or `SystemExit` during a start propagates at once, without clean-up (B-41). `shutdown()` still stops at once on a cancellation. The module keeps a reference to each clean-up task until it ends, and an abandoned one logs its failures by type.
- Start order and auto-start (INF-05) are unchanged; W2-4 decides them.

### Rationale

The check turns a silent loss of every inbound event into a startup error, at the only point where the library sees both buses. `ValueError` matches how `pydomain.infrastructure` already reports bad arguments. Re-raising the first error with type-only notes keeps `except` clauses working, keeps content out of the notes, and matches ADR-068's clean-up note. Every decision is in the [decision log](../others/decision-log.md#12-library-behaviour), B-22 to B-28, B-40, B-41 and B-45 to B-47.

### Consequences

- Code that passes inbound gateways without `message_bus=` now fails at startup; the docs pass the bus everywhere.
- Later stop errors survive only as type names in notes.
- A hanging stop still blocks `shutdown()`, and a hanging clean-up stop blocks a cancelled `bootstrap()` until a second cancellation, after which the shielded clean-up keeps running in the background; there is no timeout.

---

## 9.25 Outbox Relay and Registry Contracts

### Context

The 0.3.0 audit found the outbound path breaking promises of ADR-061, ADR-063 and ADR-065: one failing mark republished the whole batch (INF-09); `stop()` right after `start()` left the relay running (INF-11); `batch_size` and `max_attempts` of `0` were accepted (INF-12, INF-18); delivered events carried no tracing, and entries cited the command instead of the domain event (INF-13, CQRS-14, A4); a second class on a topic and a wrong translator class created poison rows (CQRS-16, CQRS-25); nested non-JSON values passed validation (CQRS-15); and `FakeOutboxStore` had no claim and returned everything for `limit=0` (FAKE-05).

### Decision

- `run_once()` marks each entry published as soon as the broker accepts it. A store error ends the pass; only delivery failures are independent (B-64).
- `fetch_unpublished` claims what it returns until a mark or a stale claim; `mark_failed` releases it; `limit < 1` raises `ValueError`. The Protocol states this and `OutboxStoreContract` checks it, but the port gains no claim methods. `FakeOutboxStore` leases for `claim_timeout` (default five minutes) on its clock (B-57, B-58).
- The relay raises `ValueError`, naming the argument, for `batch_size` or `max_attempts` below `1`, a negative delay or sleep, and `max_delay < base_delay` (B-59).
- `register_translation` raises `ValueError` when another domain type bound the topic to a different class; re-registering the same domain type still overwrites. `to_entry` raises `TypeError` unless the translator returned exactly the registered class (B-60, B-61).
- Integration event payloads are strict JSON at any depth, reported by field, type and path; `stamp()` validates its ids (B-62).
- An entry's `causation_id` is the domain event's `event_id`, `None` with the correlation for an unstamped event. At delivery the relay stamps both over the translator's and passes them as `correlation_id`/`causation_id` headers (B-63).
- `stop()` takes effect before the background task starts, and a `start()` during an in-flight `stop()` starts a fresh relay while every `stop()` waits for the run it stopped (B-72). With stale claims, attempts can exceed `max_attempts` by one per stale redelivery (B-80). A publish that never returns still blocks `stop()`, a dead task restarts only after `stop()`, a `stop()` before a directly created `run()` task begins is lost, and after `start()` a task created for `run()` makes `stop()` wait forever: deferred to Wave 2 (B-65, B-72, F-7).
- The strict-JSON walk is iterative, with a cycle guard and a 100-level limit; the relay treats any exception from validating a stored payload as a delivery failure (B-70). A published entry wins in either order: later failure marks are no-ops (B-71), and a publication after a dead letter clears it (B-79, amending ADR-065). A topic stays bound after its domain type moves, because old rows still resolve to the original class (B-73). ADR-022 is amended for the deeper check.

### Rationale

Per-entry marking shrinks the duplicate window to the entry in flight, which is the at-least-once window ADR-063 already accepts. Stating the claim in the Protocol, without methods, settles what the docstring promised while keeping the locking strategy in the adapter (ADR-063, ADR-065 point 6). Exact class checks at registration and write time move poison rows from delivery, where they burn the retry budget, to the code that caused them. Every decision is in the [decision log](../others/decision-log.md#12-library-behaviour), B-57 to B-65, B-70 to B-73 and B-79.

### Consequences

- Broker adapters must accept `**kwargs`, or every stamped delivery fails; the docs' examples now do.
- Tests that inspect `FakeOutboxStore` through `fetch_unpublished` claim the rows they read.
- One `mark_published` call per entry instead of one per batch.
- Rows written before the change keep the command id as `causation_id`.

---

## 9.26 Restore Snapshots by Validation, Not Attribute Assignment

### Context

ADR-043 captures a snapshot with `model_dump(mode="python")`, and ADR-053 lets a schema policy reject a stale one, but neither said how a snapshot becomes an aggregate again. `get_by_id()` assigned each state key with `setattr`, without validation or copies. The 0.3.0 audit found that nested models came back as dicts, that a computed field broke every load (ES-08), and that replaying the tail changed the stored snapshot, so with `FakeSnapshotStore` each load added the tail again (ES-09).

### Decision

- `get_by_id()` restores with `aggregate_cls.model_validate(state, by_alias=False, by_name=True)`, where `state` is the snapshot's state without top-level computed keys, with top-level alias keys renamed to field names (a field given under two keys is ambiguous and raises, B-78), and with the requested id and the snapshot's version, then replays the tail; aliased fields keep their values, also when an alias equals another field's name (B-66, B-74).
- The aggregate's validators and `model_post_init` run on the restored state (B-53); private attributes start from their defaults, so derive them in `model_post_init` and keep them current in `_when` (B-54).
- Any `Exception` while the state is validated, a `ValidationError` or one from the aggregate's own validators or `model_post_init`, raises `SnapshotRestoreError(DomainError)`, its message built with `describe()` (locations, which include dict keys, and error codes, or a type name), with no chain and no fallback to replay (B-55, B-67). Keys of removed fields are ignored under the default `extra="ignore"` and rejected under `extra="forbid"`.
- `_take_snapshot()` dumps with `by_alias=False, exclude_computed_fields=True`, leaving computed fields out at every depth; the restore drops top-level computed keys from older snapshots, even with `extra="forbid"`, while nested computed or alias keys in old snapshots need a `_snapshot_schema_version` bump (B-56, B-66, B-74). The Pydantic floor is 2.12.5 (B-75).
- The `SnapshotStore` Protocol promises that a saved or returned snapshot can be changed without changing what the store holds; two `SnapshotStoreContract` cases pin it, and `FakeSnapshotStore` stores and returns deep copies.

### Rationale

Validation is how Pydantic builds a model from a dict: it rebuilds nested models and copies containers, so a snapshot plus its tail equals a full replay and the snapshot is never shared with the aggregate. Making the store promise isolation keeps the repository correct with any adapter, not only with one that copies. A value-free error, not a silent replay, makes a schema change visible, as ADR-053 intends.

### Consequences

- Aggregates with nested models or computed fields can use snapshots.
- Restore costs one validation of the state, and validators with side effects run on every load.
- State that no longer validates fails with `SnapshotRestoreError` where `setattr` accepted it.

---

## 9.27 Release Every Lock Under Cancellation and Failing Releases

### Context

ADR-019 releases `AggregateLockingBehavior`'s locks in reverse order and says this prevents lock leaks. The 0.3.0 audit found that a dispatch cancelled while waiting on its second key keeps the first, because the acquire loop cleans up in `except Exception` and `CancelledError` is a `BaseException` (CQRS-01); that one `release()` that raises skips every later key and rolls back a command whose handler succeeded (CQRS-02); and that a same-key nested dispatch deadlocks on the non-reentrant lock (CQRS-03).

### Decision

- One block covers the acquire loop and the handler and releases, in reverse order, every key whose `acquire()` returned, after the handler returns or after any `BaseException`. Each `release()` runs in its own `try` that catches `BaseException`, so every key gets its release.
- The releases run inline, in the acquiring task (B-51). When a cancellation of that task arrives during a release (a second `cancel()`, an `asyncio.timeout`, an anyio cancel scope), the interrupted key and the rest move to one background task, held by a strong reference until it ends, and the cancellation propagates at once, also when the provider converts or swallows it; the background task logs its failures by type. The interrupted key is retried there, so `release()` must be ownership-safe, not just idempotent: a late release never frees another holder's lock. `FakeLockProvider` keeps a per-acquire token in the calling context: a release with a stale token, whose hold already ended, does nothing, and one from a context with no token for the key raises `RuntimeError` (B-68). A matching release drops its token, so a second release in the same context raises too and the tokens stay bounded (B-81). `LockProviderContract` pins that a late release never frees the next holder's key. With no asyncio task (trio) the releases run inline with no hand-over (B-69).
- A failed release after a successful handler does not fail the command: the result is returned (the commit has already happened, ADR-079), and a WARNING on `pydomain.pipeline` names the message type and the error's type, never the key. After a failed handler the handler's error propagates with a `clean-up also failed: <type>` note per failed release. A release's `BaseException` (a cancellation) is re-raised after the remaining releases (B-50).
- Locks are not reentrant (B-48): a handler must not dispatch a nested command that resolves to a key it holds. `FakeLockProvider` raises `LockReentryError`, a new `CQRSError`, on a same-task re-acquire instead of deadlocking (B-52); `LockProviderContract` pins only that the second acquire is never granted (it may raise any exception or block) and that the first hold is still held afterwards. A nested dispatch spawned in a new task still deadlocks.
- A cancelled `acquire()` never holds the key, stated in the `LockProvider` Protocol and pinned by `LockProviderContract`.

### Rationale

A lock left held blocks every later command on its aggregate until the process restarts or a lease expires, which is worse than a lock released after a failure. The provider owns recovery from its own release failure; the command's outcome is already decided by its handler. Releasing in the acquiring task keeps providers whose locks belong to that task working; the hand-over keeps the releases going when that task is cancelled. Type-only notes follow ADR-068 and ADR-073. Every decision is in the [decision log](../others/decision-log.md#12-library-behaviour), B-48, B-50 to B-52, B-68 and B-69.

### Consequences

- A release error after a successful handler no longer reaches the caller of `dispatch()`; it is logged.
- Only after a cancellation during the releases does `release()` run in another task; a task-owned provider whose `release()` awaits refuses those releases, and its lease or TTL frees the keys. The caller returns before the background releases finish.
- A provider whose `release()` is only idempotent can lose mutual exclusion through the retried release; every provider needs a per-acquire token.
- The locks are held through the commit since ADR-079 (phase 1, DCE-143), and still released before the exit drain. The behavior still does not protect a shared repository's event buffer (B-19).

---

## 9.28 Saga Failure and Recovery Keep Every Command

### Context

ADR-055 says a `fail=True` step dispatches its forward command, then fails; ADR-035 says recovery re-dispatches every unconfirmed pending command. The 0.3.0 audit found that `execute_compensations()` discarded the forward command (SAGA-01), that recovery deleted a command it could not hydrate (SAGA-11a), counted one failed attempt twice (SAGA-11b), read `max_retries=0` as "unlimited" on the event path and "fail at once" in recovery (SAGA-11c), and stopped the batch at the first failing saga (SAGA-21). Wired to a `CommandBus`, as its annotation said, the manager never saw its commands' events (SAGA-19). The stalled-saga contract omitted `COMPENSATING` (A29), and `FakeSagaRepository` re-emitted drained events (FAKE-12).

### Decision

- `execute_compensations()` sets the queued forward commands aside; the manager sends them first, caused by the step's event (the saga id on the timeout path), then the compensations. A failing forward command is recorded in `failed_compensations` and the saga ends `FAILED` (B-83). Without compensations, a raising handler's queued commands are still sent (SAGA-12, W2-5).
- Recovery sends pending commands in order and stops at the first unhydratable one, which stays pending with the commands after it (B-84, B-92). The attempt counts once; a running saga is suspended with `Could not hydrate <CommandType>`, an already suspended one keeps its suspension, and the suspension recovery created, marked `"suspended_by_recovery"` on the entry, ends once the commands are sent; a user's suspension is never resumed or rewritten (B-91, B-95), and any event resumes the saga from recovery's suspension, past `should_resume()` (B-98). Every saga recovery ends `FAILED` with failed compensations is reported (B-99). A `COMPENSATING` saga always ends through the compensation path, `FAILED` when any compensation failed, in one recovery (B-96).
- A failing forward command is stored and logged with its handler's own exception type (a failing compensation keeps the bus's `CommandExecutionError`, F-9); commands queued before a handler or `on_timeout()` raised are dropped when compensation runs (B-83).
- `max_retries: int | None = Field(default=3, ge=0)` is the number of retries after the first attempt, on both paths: the saga fails once `retry_count > max_retries`, `None` is unlimited, and a negative value raises `ValidationError` on construction or load, not on assignment (B-49, B-85, B-93, B-97). Each failed attempt counts once.
- `recover_pending_sagas()` recovers each saga in its own `try`, logs a failure by saga id and exception type, and returns the ids it did not recover (B-86).
- `SagaManager` takes a `CommandDispatcher` (its `dispatch` publishes the command's events) and raises `TypeError` for a `CommandBus` or a subclass; `bind_to` takes an `EventHandlerRegistrar` (B-87).
- A stalled saga has pending commands or is `COMPENSATING`; a repository hands each event over once. `SagaRepositoryContract` pins both.

### Rationale

A saga's commands are its side effects: losing one silently leaves the business process half done with no record. Keeping the unhydratable command pending reuses the retry rules instead of adding a field; one comparison on both paths makes `max_retries` mean what it says. Refusing the `CommandBus` turns a silent stall into an error at startup. Every decision is in the [decision log](../others/decision-log.md#12-library-behaviour), B-49, B-83 to B-87, B-91 to B-93 and B-95 to B-100, and in the backlog, F-9.

### Consequences

- `max_retries=0` now fails a saga after one failed dispatch on the event path, where it retried forever. With the default `3`, a command whose dispatch keeps failing is now tried 4 times in all (first dispatch and 3 recovery attempts), where it was tried twice, because a failed recovery attempt counted twice. `None` is the way to say "unlimited"; a negative value is refused.
- `recover_pending_sagas()` returns a list and no longer raises a saga's dispatch error; `SagaManager(..., CommandBus())` raises `TypeError`.
- The forward commands sent on failure are not checkpointed, and exhausted retries still fail without compensating on the event path: both are W2-5.

---

## 9.29 Clean-Up Errors, Conflicting Stored Versions and Per-Class `configure()`

### Context

The Wave 1 review probes and audit row DDD-08 found five edge cases in the decisions of §9.19 to §9.24: a cancelled dispatch whose rollback raised reached its caller as the bare storage error; a drain error replaced the storage error it followed; with an `UpcasterRegistry`, an envelope and a payload that disagree about the version were read anyway, and a digit string too long for `int()` raised a bare `ValueError`; `shutdown()` repeated notes; and `Sub.configure()` changed the base class's id generator.

### Decision

- `CommandBus.dispatch()` re-raises a cancellation (any `BaseException` that is not an `Exception`) whose clean-up on exit failed, with a type-only note `clean-up also failed: <Type>`; the clean-up error is its hidden `__context__` (B-88).
- `rollback()` re-raises the storage error when `_rollback()` and the drain both raise, with a note `draining the event buffers also failed: <Type>` (B-88).
- With an `UpcasterRegistry`, the payload's `event_version` is read like the envelope's version, and two versions that disagree, a missing envelope version with a payload not at v1, or a digit string too long to read raise `UpcastError` naming the type only (B-89).
- `shutdown()` and a failed start's clean-up note each failure instance once per error, and never the raised error on itself (B-90, B-102).
- `configure()` sets the generator of the class it is called on and its subclasses; the most derived class's own generator wins (B-101).

### Rationale

A task that was cancelled must see its cancellation, and the storage failure is the one that says the transaction may not have rolled back. Two stored versions that disagree mean the store is inconsistent; reading either one can upcast twice. A repeated note says nothing new. `Sub.configure()` reads as per-class, and the class-variable override already is. Every decision is in the [decision log](../others/decision-log.md#12-library-behaviour), B-88 to B-90, B-101 and B-102.

### Consequences

- A cancelled dispatch whose rollback fails raises the cancellation; a stored event whose versions disagree raises `UpcastError`; a subclass's `configure()` no longer changes other classes. The upgrade notes list each change.
- Direct users of a unit of work keep Python's default for a body and a rollback that both fail (§9.20).

---

## 9.30 Commit Inside the Command Pipeline

> **Accepted** ([ADR-079](../adr/ADR-079-commit-inside-the-command-pipeline.md)), implemented in four phases. Phase 1 (DCE-143) landed: the commit runs in the terminal step, the locks are held through it, the result is recorded after it, behaviors see `ctx.new_events`, a pipeline that never reaches the handler commits nothing, a failed post-commit record is a WARNING, and `FakeUnitOfWork(fail_at=...)` injects commit failures (§6.1). Phase 2 (DCE-144) landed: a second `next()` in a command pipeline raises `PipelineReentryError`. Phase 3 (DCE-145) landed: `_commit()` and `_rollback()` run to completion under cancellation (`shield_hooks = False` opts out), `anyio>=4` is a runtime dependency, and a command that committed and then raised is recorded (§5.3.7, §6.1, §10.6). Phase 4 (DCE-146) landed: `MessageReservationStore`, which `FakeProcessedMessageStore` implements and `MessageReservationStoreContract` checks, lets `IdempotencyBehavior` reserve a command before its handler, and a concurrent duplicate gets `CommandInProgressError` (§5.3.5, §5.3.6, §10.6).

### Context

ADR-020 has the bus commit after the whole pipeline returns, so every behavior's work after `next()` runs before the commit. The 0.3.0 audit found that `IdempotencyBehavior` stores the result before a commit that can still fail (CQRS-K1), that the locks are released before the commit, losing updates (CQRS-K3), that two concurrent dispatches of one `command_id` both run (CQRS-04), that neither the commit nor the rollback is shielded from cancellation (CQRS-07), that a second `next()` commits two runs (CQRS-21), that behaviors never see `ctx.new_events` (CQRS-11), and that `FakeUnitOfWork` cannot make a commit fail (FAKE-04).

### Decision

- The bus's own terminal step runs the handler, then `uow.commit()`, then sets `ctx.new_events`. The bus still creates the unit of work, enters `async with uow:` before the first behavior, and rolls back or drains on exit (B-103). A pipeline that never reaches the handler commits nothing (B-116).
- The locks are therefore held through the commit, with no change to `AggregateLockingBehavior`; a `LockProvider` lease must cover the commit (B-104).
- `IdempotencyBehavior` records the result after the commit: at-least-once, with the gap (a crash, or an error after the storage commit took effect) documented and closed in 0.5.0 (W3-6) (B-105).
- A new optional Protocol, `MessageReservationStore`, lets `IdempotencyBehavior` reserve a command before the handler: `reserve` returns a token (`None` when the id is taken), `release(message_id, token)` frees only that reservation, and `set` completes it (B-106, B-117, B-120). A store that implements only `ProcessedMessageStore` keeps the old flow and its known limit. A concurrent duplicate gets `CommandInProgressError`, a new `CQRSError` (B-107).
- Once the commit completed, the result is recorded on any exception that follows, then the exception is re-raised: a committed command is never run again (B-109, B-121). `reserve`, `release` and the post-commit `set` are always protected from cancellation, so a store must work from any task; a token reserved while the caller was cancelled is released at once (B-123, B-125).
- `AbstractUnitOfWork` shields `_commit()` and `_rollback()`: each runs inside `anyio.CancelScope(shield=True)` and, under asyncio, in a task built with the `asyncio.Task` constructor in the caller's context, awaited through a private future rather than `asyncio.shield`. Under a native asyncio cancellation the helper re-raises once the hook ends; under an anyio cancel scope anyio delivers the cancellation again at the next checkpoint. With no async library running the hook runs inline. `anyio>=4` becomes a runtime dependency, tested at its floor and imported only by `pydomain.cqrs.unit_of_work` (B-108, B-119, B-122, B-128, B-131, B-133). A completed `_commit()` marks the scope committed. A unit of work whose storage is keyed by its task sets `shield_hooks = False`, and its hooks run inline without the shield (B-115). `_flush()` and `_write_outbox()` are not shielded (B-110). A hook's own `CancelledError`, with the caller not cancelled, is that hook's failure: a private `CQRSError` that the bus wraps in `CommandExecutionError` (B-126, B-130). A cancellation pending before `_commit()` starts still lets it complete (B-132). A post-commit `set` that fails is a type-only WARNING with the result returned (B-129), or, when `next()` raised after the commit, a type-only note on the re-raised error (B-134).
- A second `next()` in a command pipeline raises `PipelineReentryError`, a new `CQRSError`; query and event pipelines are unchanged (B-111).
- The slot table says what each side of `next()` means: after it returns with the handler reached, the work is committed and `ctx.new_events` is set; after a short-circuit, nothing is committed; after it raises, either the handler or the commit failed (or a refusal: a duplicate in progress, a second `next()`) and the rollback runs at the exit, or the commit completed and no rollback runs, with `ctx.new_events` set (B-112). An empty `ctx.new_events` does not say whether a commit happened.
- The events of a command that committed and then failed or was cancelled are not dispatched in-process; the outbox path delivers them (B-124, backlog F-10).

### Rationale

Putting the commit inside `next()` makes every behavior's "after" mean "after the commit" with one change in one place, and keeps ADR-068's single rollback on exit. A reservation needs an ownership-safe release, which `check_and_set` does not have; a separate optional Protocol adds it without breaking existing stores. A commit that took effect must not be rolled back, or run again, because the caller gave up waiting. Every decision is in the [decision log](../others/decision-log.md#12-library-behaviour), B-103 to B-134.

### Consequences

- Seven behaviour changes for 0.4.0, each with an upgrade note (B-114); none removes a public name. `anyio` joins `pydantic` and `uuid-utils` as a runtime dependency.
- Under asyncio the shielded hooks run in their own task, in the caller's context; storage keyed by the current task silently loses writes there and must opt out with `shield_hooks = False` (B-119).
- The rollback and the exit drain still run after the release, and a shared repository is still unsafe for concurrent dispatches (B-19, F-6, B-113).

---

## 9.31 Identify Delivered Events for Event Idempotency

> **Accepted** ([ADR-080](../adr/ADR-080-identify-delivered-events-for-event-idempotency.md)), implemented in three phases under DCE-131 (`design-docs/03-event-idempotency.frame.md`). Phase 1 (DCE-148) landed: `FakeProcessedMessageStore` keeps `check_and_set` marks apart from results, `ProcessedMessageStoreContract` pins it, and an outbox store refuses a duplicate `message_id` with `CQRSError` (§5.3.6, §5.3.11). Phase 2 (DCE-149) landed: the `InboundEventGateway` derives a translated event's `event_id` from the integration event's, unless the translator set it, and a payload without `event_id` is dispatched with a WARNING; `DomainEvent` records whether it generated its id, and equality ignores that record (§5.2.4, §8.2). Phase 3 (DCE-150) landed: `EventIdempotencyBehavior` keys on the event's `event_id` and its consumer, marks the event after its handler succeeds (through a reservation when the store supports one), and raises `EventInProgressError` for a concurrent duplicate and `EventIdempotencyMisuseError` for one instance on two handlers (§5.3.5, §5.3.10). All three phases are implemented.

### Context

`EventIdempotencyBehavior` marks `causation_id` with `check_and_set` before the handler (§9.16). Every event of one command shares that key, so only the first reaches an idempotent handler, two handlers sharing a store share it, and the fake's shared key space skips them all (CQRS-05). A failed handler stays marked, so its redelivery is skipped (CQRS-K2). An inbound domain event gets a new `event_id` on every redelivery, and without `correlation_id` nothing is deduplicated (INF-02).

### Decision

- An event is identified by its `event_id` on both paths; `causation_id` is tracing only. The inbound gateway derives the domain `event_id` from the integration event's id with `uuid5`, unless the translator set one (a private marker on the event says whether its id was generated, B-156); a payload without `event_id` is dispatched with a WARNING that names the integration class and the topic only, and is not deduplicated (B-145, B-146, B-152). The derived id ignores the topic (B-160), and `DomainEvent` equality ignores the marker (B-158).
- The key is `uuid5(uuid5(<namespace>, consumer), str(event_id))`, a `UUID`; `consumer=` is optional in 0.4.0, falling back to the handler's `module:qualname` with a `DeprecationWarning`, and required in 0.5.0 (W3-7); one behavior instance serves one handler and raises on a second (B-147, B-148, B-159).
- The mark comes after the handler succeeds: with a `MessageReservationStore` the behavior reserves, runs the handler, then completes with `set`, and a concurrent duplicate gets `EventInProgressError`; without one it checks `contains`, runs the handler, then calls `check_and_set`; every store call that changes state (`reserve`, `release`, `set`, `check_and_set`) runs under the cancellation shield, so either store must work from any task, and the reads (`get`, `contains`) do not (B-149, B-150, B-174, B-175).
- `ProcessedMessageStore` is unchanged; the fake keeps marks apart from results, and the contract pins that a mark is not a result (B-151). An outbox store refuses a duplicate `message_id` (B-155).
- A failed handler releases its reservation and its error is re-raised unchanged, for W2-3 to NACK (B-153).

### Rationale

The `event_id` is the only id that is distinct per event and, once the gateway derives it, stable per delivery; adding the consumer separates handlers without a Protocol change. Marking after success with a reservation gives at-least-once handling with no lost events. Every decision is in the [decision log](../others/decision-log.md#12-library-behaviour), B-145 to B-179. The decision was implemented in three phases, planned in `design-docs/03-event-idempotency.frame.md` (B-163).

### Consequences

- Ten behaviour changes for 0.4.0, each with an upgrade note (B-154, B-161); no public name is removed, and two exceptions are added and exported, `EventInProgressError` and `EventIdempotencyMisuseError`. The notes on ADR-058, 060, 061, 062 and 063 come with ADR-080's acceptance (B-162).
- Since DCE-152 (ADR-081, §9.32), a failure on a direct dispatch, `EventInProgressError` included, reaches the inbound gateway inside `EventHandlersFailed`, and the subscriber NACKs it.

## 9.32 Handler Failures Reach the Inbound Gateway, and Failure Logs Carry No Content

> **Accepted** ([ADR-081](../adr/ADR-081-handler-failures-reach-the-inbound-gateway.md)), implemented in three phases under DCE-132 (`design-docs/04-handler-failure-semantics.frame.md`). Phase 1 (DCE-152) landed: a direct dispatch raises `EventHandlersFailed` once every handler has run, the inbound gateway re-raises it so the subscriber NACKs, a non-`ValidationError` from validation and a translator result that is not a `DomainEvent` are ACKed as translation failures, and `InMemoryMessageSubscriber.outcomes` records each delivery (§5.3.10, §5.3.11, §6.7). Phases 2 (value-free failure logs, F-9) and 3 (handler snapshot, `LoggingBehavior`) are planned; until they land, the handler-failure lines still carry `exc_info`.

### Context

`EventBus` logs and swallows every handler failure (ADR-046), so `MessageBus.dispatch(DomainEvent)` always returns. The `InboundEventGateway`'s re-raise, which ADR-060 says lets the subscriber NACK, is unreachable through a real bus, and every failed inbound delivery is ACKed and lost (INF-01, A1). A concurrent duplicate refused with `EventInProgressError` is lost when the first delivery then fails. The handler-failure logs that ADR-071 left open carry exception messages and tracebacks, and a content sentinel reached each of them (CQRS-27). A failed compensation stores the bus's error type, while a failed forward command stores its handler's (F-9). Smaller rows: a handler registered during a dispatch runs in it (CQRS-18), `LoggingBehavior` fails on a raising formatter and is silent on a cancellation (CQRS-20), and the in-memory subscriber records no outcome (FAKE-09).

### Decision

- `EventBus.dispatch(event)`, and with it `MessageBus.dispatch(DomainEvent)`, runs every handler and then raises `EventHandlersFailed(ExceptionGroup, CQRSError)` if any failed. Its message names the event type and the count, and `.exceptions` holds each handler's own exception, after the idempotency release (B-180, B-181). `dispatch_many`, the post-command path, keeps logging and swallowing, so in-process isolation stays.
- The gateway propagates that error, so the subscriber NACKs every handler failure, `EventInProgressError` and `EventIdempotencyMisuseError` included (B-182). Unknown topics, validation failures and translator errors stay ACKed as poison, and so do a translator result that is not a `DomainEvent` and any other `Exception` from validation (B-199, B-200). Saga failures stay inside `SagaManager.handle`. A cancellation propagates unchanged (B-183).
- Every handler-failure line (`EventBus`, gateway translation, the saga manager's compensation, handler and timeout lines, the `SubscriptionRunner`'s batch line) carries a type-only description and a value-free stack, keeping only pydomain's own type-only notes, and never `exc_info` (B-184, B-196); handlers are named by `__qualname__`, never `repr()` (B-201). The gateway's dispatch line is one type-only line without a stack (B-185).
- A failed compensation stores its handler's own error type (B-186). `EventBus` takes a snapshot of the handlers at the start of each dispatch (B-187). `LoggingBehavior` logs a formatter failure as a WARNING and a cancellation at INFO (B-188, B-189). `InMemoryMessageSubscriber.outcomes` records `ack` or `nack <Type>` per delivery (B-190, B-198). `EventHandlersFailed` pickles and copies intact, recounts when split, and its `repr()` leaves out the members (B-195).

### Rationale

The NACK belongs where the broker can act on it, and only the direct-dispatch path has such a caller. The post-command path keeps ADR-046's isolation: the command has committed, so no caller could act on the failure. An exception group keeps every handler's own exception, as ADR-080 point 6 requires, and lets callers match members with `except*`. A value-free stack keeps the debugging value of a traceback without the content ADR-071 forbids. Every decision is in the [decision log](../others/decision-log.md#12-library-behaviour), B-180 to B-204.

### Consequences

- Ten behaviour changes for 0.4.0, each with an upgrade note; one exception is added and exported, and one fake attribute is added. Notes on ADR-046, 058, 060, 071, 077 and 080, and the README marks, come with ADR-081's acceptance (B-192, B-203, B-204). Phase 1 adds §6 "Inbound delivery"; each phase updates this section's status line.
- A NACK redelivers an event to every handler, including those that succeeded, so inbound handlers should use `EventIdempotencyBehavior` (B-191).
- Log handlers that read `exc_info` from these records get none.

---

## Section → ADR Mapping

The narrative sections above correspond to these formal Architecture Decision Records:

| Section | ADR |
|---------|-----|
| 9.1 Protocol over ABC | [ADR-001](../adr/ADR-001-protocol-over-abc-for-interfaces.md) |
| 9.2 Pydantic v2 only | [ADR-002](../adr/ADR-002-pydantic-v2-only.md) |
| 9.3 Two Projection Types | [ADR-024](../adr/ADR-024-two-projection-types.md) |
| 9.4 isinstance dispatch | [ADR-012](../adr/ADR-012-isinstance-dispatch-in-aggregates.md) |
| 9.5 Integration Events Primitive Payloads | [ADR-022](../adr/ADR-022-integration-events-primitive-payloads.md) |
| 9.6 Saga State as AggregateRoot | [ADR-027](../adr/ADR-027-saga-state-as-aggregate-root.md) |
| 9.7 Saga on DSL | [ADR-028](../adr/ADR-028-saga-on-dsl.md) |
| 9.8 Publish After Commit | [ADR-005](../adr/ADR-005-publish-after-commit.md) |
| 9.9 Entity Auto-ID | [ADR-007](../adr/ADR-007-entity-auto-id-with-runtime-guard.md) |
| 9.10 frozen commands/queries | [ADR-014](../adr/ADR-014-frozen-commands-queries.md) |
| 9.11 DomainService Marker | [ADR-009](../adr/ADR-009-domain-service-marker.md) |
| 9.12 No Generic ReadStore | [ADR-026](../adr/ADR-026-no-generic-readstore.md) |
| 9.13 Declarative Saga Failure | [ADR-055](../adr/ADR-055-declarative-saga-failure-fail-true.md) |
| 9.14 Declarative Resume Authorization | [ADR-056](../adr/ADR-056-declarative-resume-authorization-resumes-from.md) |
| 9.15 Default Timeout Sentinel | [ADR-057](../adr/ADR-057-default-timeout-sentinel-step-override.md) |
| DomainEvent Dispatch | [ADR-058](../adr/ADR-058-messagebus-dispatch-domain-event.md) |
| MessageSubscriber Protocol | [ADR-059](../adr/ADR-059-message-subscriber-protocol.md) |
| InboundEventGateway | [ADR-060](../adr/ADR-060-inbound-event-gateway.md) |
| Integration Event Tracing | [ADR-061](../adr/ADR-061-integration-event-tracing.md) |
| EventBus Application Layer Peer | [ADR-062](../adr/ADR-062-eventbus-application-layer-peer.md) |
| Outbound Event Gateway and Outbox | [ADR-063](../adr/ADR-063-outbound-event-gateway-and-outbox.md) |
| Outbox Writer Composition | [ADR-064](../adr/ADR-064-outbox-writer-composition.md) |
| Outbox Retry Policy and Dead-Letter Queue | [ADR-065](../adr/ADR-065-outbox-retry-policy-and-dead-letter.md) |
| Test-First Fixes with Documentation | [ADR-066](../adr/ADR-066-test-first-fixes-with-documentation.md) |
| Protocol Contract Suites | [ADR-067](../adr/ADR-067-protocol-contract-suites.md) |
| 9.19 Upcast to the Class Version Once | [ADR-069](../adr/ADR-069-upcast-to-class-version-once.md) |
| 9.20 Rollback Drains Event Buffers | [ADR-068](../adr/ADR-068-rollback-drains-event-buffers.md) |
| 9.21 DDD Copies, Id Types, Stamping and Specifications | [ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md) |
| 9.22 Failure Paths Carry No Payload Values | [ADR-071](../adr/ADR-071-library-failure-paths-carry-no-payload-values.md) |
| 9.23 Shipped Fakes Conform to Their Protocol Contracts | [ADR-072](../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md) |
| 9.24 Safe Bootstrap Wiring | [ADR-073](../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md) |
| 9.25 Outbox Relay and Registry Contracts | [ADR-074](../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md) |
| 9.26 Restore Snapshots by Validation | [ADR-075](../adr/ADR-075-restore-snapshots-by-validation.md) |
| 9.27 Release Every Lock | [ADR-076](../adr/ADR-076-release-every-lock-under-cancellation-and-failing-releases.md) |
| 9.28 Saga Failure and Recovery Keep Every Command | [ADR-077](../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md) |
| 9.29 Clean-Up Errors, Stored Versions and Per-Class configure | [ADR-078](../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md) |
| 9.30 Commit Inside the Command Pipeline | [ADR-079](../adr/ADR-079-commit-inside-the-command-pipeline.md) |
| 9.31 Identify Delivered Events for Event Idempotency | [ADR-080](../adr/ADR-080-identify-delivered-events-for-event-idempotency.md) |
| 9.32 Handler Failures Reach the Inbound Gateway | [ADR-081](../adr/ADR-081-handler-failures-reach-the-inbound-gateway.md) |

---

## ADR Reference — All 81 Decisions

### Base / Foundational (001–005)

| ADR | Title |
|-----|-------|
| [ADR-001](../adr/ADR-001-protocol-over-abc-for-interfaces.md) | Protocol over ABC for interfaces |
| [ADR-002](../adr/ADR-002-pydantic-v2-only.md) | Pydantic v2 only — no v1 compatibility shims (amended by ADR-075) |
| [ADR-003](../adr/ADR-003-async-only-public-api.md) | Async-only public API |
| [ADR-004](../adr/ADR-004-exception-hierarchy-by-layer.md) | Exception hierarchy by layer |
| [ADR-005](../adr/ADR-005-publish-after-commit.md) | Publish events after commit, never before |

### DDD Module (006–013)

| ADR | Title |
|-----|-------|
| [ADR-006](../adr/ADR-006-entity-identity-semantics.md) | Entity identity semantics — `id: TId` with structural equality |
| [ADR-007](../adr/ADR-007-entity-auto-id-with-runtime-guard.md) | Entity auto-ID with runtime type guard (amended by ADR-070) |
| [ADR-008](../adr/ADR-008-uuidv7-time-ordered-identity.md) | UUIDv7 time-ordered identity generation (see ADR-078) |
| [ADR-009](../adr/ADR-009-domain-service-marker.md) | DomainService as a marker class, not a base class |
| [ADR-010](../adr/ADR-010-specification-abc-basemodel-hybrid.md) | Specification as ABC + BaseModel hybrid (amended by ADR-070) |
| [ADR-011](../adr/ADR-011-domain-event-stamp-immutability.md) | DomainEvent `stamp()` preserves immutability (amended by ADR-070) |
| [ADR-012](../adr/ADR-012-isinstance-dispatch-in-aggregates.md) | isinstance dispatch in aggregate `_when()` |
| [ADR-013](../adr/ADR-013-factory-vs-reconstitution-factory.md) | Factory vs ReconstitutionFactory separation |

### CQRS Module (014–026)

| ADR | Title |
|-----|-------|
| [ADR-014](../adr/ADR-014-frozen-commands-queries.md) | `frozen=True` and `extra="forbid"` on commands and queries |
| [ADR-015](../adr/ADR-015-typed-command-query-generics.md) | Typed `Command[TResult]` / `Query[TResult]` with generic result binding |
| [ADR-016](../adr/ADR-016-handler-signature-asymmetry.md) | Handler signature asymmetry — CommandHandler gets UoW, others don't |
| [ADR-017](../adr/ADR-017-onion-style-pipeline-behaviors.md) | Onion-style pipeline behaviors |
| [ADR-018](../adr/ADR-018-missing-sentinel-idempotency.md) | MISSING sentinel for idempotency |
| [ADR-019](../adr/ADR-019-sorted-lock-keys-deadlock-prevention.md) | Sorted lock keys for deadlock prevention (amended by ADR-076) |
| [ADR-020](../adr/ADR-020-commandbus-owns-uow-lifecycle.md) | CommandBus owns UoW lifecycle (amended by ADR-068) |
| [ADR-021](../adr/ADR-021-correlation-causation-propagation.md) | Correlation/Causation propagation via UoW stamping |
| [ADR-022](../adr/ADR-022-integration-events-primitive-payloads.md) | Integration events — primitive-only payloads |
| [ADR-023](../adr/ADR-023-integration-event-bypasses-id-generator.md) | IntegrationEvent bypasses IdGenerator — uses `uuid7` directly |
| [ADR-024](../adr/ADR-024-two-projection-types.md) | Two separate projection types by naming convention |
| [ADR-025](../adr/ADR-025-projection-split-across-layers.md) | Projection split across layers — CQRS Protocol, ES implementation |
| [ADR-026](../adr/ADR-026-no-generic-readstore.md) | No generic ReadStore protocol — user-defined read contracts |

### SAGA Subsystem (027–036)

| ADR | Title |
|-----|-------|
| [ADR-027](../adr/ADR-027-saga-state-as-aggregate-root.md) | Saga state as AggregateRoot |
| [ADR-028](../adr/ADR-028-saga-on-dsl.md) | Saga `on()` DSL for unified command and compensation |
| [ADR-029](../adr/ADR-029-generic-saga-parameterized-by-state.md) | Generic `Saga[S: SagaState]` parameterized by state type |
| [ADR-030](../adr/ADR-030-saga-registry-auto-binding.md) | SagaRegistry auto-binding via `listens_to` |
| [ADR-031](../adr/ADR-031-saga-manager-separate-orchestrator.md) | SagaManager as separate orchestrator (amended by ADR-077) |
| [ADR-032](../adr/ADR-032-saga-correlation-propagation.md) | Saga correlation via `event.correlation_id` |
| [ADR-033](../adr/ADR-033-lifo-compensation-stack.md) | LIFO compensation stack via serialized `CompensationRecord` |
| [ADR-034](../adr/ADR-034-saga-suspension-with-timeout.md) | Saga suspension with timeout (human-in-the-loop) |
| [ADR-035](../adr/ADR-035-crash-recovery-pending-commands.md) | Crash recovery via `pending_commands` per-command tracking (amended by ADR-077) |
| [ADR-036](../adr/ADR-036-saga-idempotency-processed-events.md) | Saga idempotency via `processed_event_ids` set |

### Event Sourcing Module (037–043)

| ADR | Title |
|-----|-------|
| [ADR-037](../adr/ADR-037-eventstream-frozen-value-object.md) | EventStream as frozen value object |
| [ADR-038](../adr/ADR-038-dual-mode-event-application.md) | Dual-mode event application — `_apply()` records, `_replay()` reconstitutes |
| [ADR-039](../adr/ADR-039-convention-dispatch-projections.md) | Convention dispatch in projections — `_when_{TypeName}` methods |
| [ADR-040](../adr/ADR-040-event-sourced-repository-concrete-base.md) | EventSourcedRepository as concrete base class |
| [ADR-041](../adr/ADR-041-optimistic-concurrency-command-idempotency.md) | Optimistic concurrency via `expected_version` + `command_id` idempotency |
| [ADR-042](../adr/ADR-042-event-upcaster-chain-cycle-detection.md) | EventUpcaster chain with cycle detection (amended by ADR-069) |
| [ADR-043](../adr/ADR-043-snapshot-policy-pluggable-protocol.md) | Snapshot policy as pluggable protocol (amended by ADR-075) |

### Infrastructure (044–049)

| ADR | Title |
|-----|-------|
| [ADR-044](../adr/ADR-044-dynamic-event-registry-generic-fallback.md) | Dynamic event registry with `GenericDomainEvent` fallback |
| [ADR-045](../adr/ADR-045-messagebus-level3-facade.md) | MessageBus as Level 3 facade |
| [ADR-046](../adr/ADR-046-event-handlers-fail-independently.md) | Event handlers fail independently — per-handler try/except |
| [ADR-047](../adr/ADR-047-bootstrap-composition-root.md) | `bootstrap()` composition root (amended by ADR-073) |
| [ADR-048](../adr/ADR-048-subscription-runner-at-least-once.md) | SubscriptionRunner at-least-once delivery |
| [ADR-049](../adr/ADR-049-catch-up-subscriptions-polling.md) | Catch-up subscriptions via polling SubscriptionRunner |

### Cross-Cutting (050–052)

| ADR | Title |
|-----|-------|
| [ADR-050](../adr/ADR-050-aggregate-pending-events-private-attr.md) | AggregateRoot `_pending_events` as `PrivateAttr` (amended by ADR-070) |
| [ADR-051](../adr/ADR-051-message-broker-separate-boundary.md) | `MessageBroker` Protocol — separate boundary from MessageBus |
| [ADR-052](../adr/ADR-052-checkpoint-store-vs-snapshot-store.md) | `CheckpointStore` vs `SnapshotStore` — two separate persistence concerns |

### Snapshot Schema (053)

| ADR | Title |
|-----|-------|
| [ADR-053](../adr/ADR-053-snapshot-schema-version-policy.md) | Snapshot schema version policy for stale snapshot detection (amended by ADR-075) |

### SAGA Pruning Policy (054)

| ADR | Title |
|-----|-------|
| [ADR-054](../adr/ADR-054-saga-pruning-policy-pluggable-protocol.md) | Saga pruning policy as pluggable protocol |

### Declarative Failure (055)

| ADR | Title |
|-----|-------|
| [ADR-055](../adr/ADR-055-declarative-saga-failure-fail-true.md) | Declarative saga failure via `fail=True` and callable reason parameters (amended by ADR-077) |

### Resume Authorization (056)

| ADR | Title |
|-----|-------|
| [ADR-056](../adr/ADR-056-declarative-resume-authorization-resumes-from.md) | Declarative resume authorization via `resumes_from` and `should_resume` |

### Default Timeout Sentinel (057)

| ADR | Title |
|-----|-------|
| [ADR-057](../adr/ADR-057-default-timeout-sentinel-step-override.md) | Class-level default timeout with sentinel and step overrides |

### DomainEvent Dispatch (058)

| ADR | Title |
|-----|-------|
| [ADR-058](../adr/ADR-058-messagebus-dispatch-domain-event.md) | MessageBus dispatch extended for DomainEvent |

### MessageSubscriber Protocol (059)

| ADR | Title |
|-----|-------|
| [ADR-059](../adr/ADR-059-message-subscriber-protocol.md) | MessageSubscriber Protocol — subscriber-side counterpart to MessageBroker |

### InboundEventGateway (060)

| ADR | Title |
|-----|-------|
| [ADR-060](../adr/ADR-060-inbound-event-gateway.md) | InboundEventGateway — bridging external brokers to the internal MessageBus |

### Integration Event Tracing (061)

| ADR | Title |
|-----|-------|
| [ADR-061](../adr/ADR-061-integration-event-tracing.md) | Integration Event Distributed Tracing — `correlation_id` / `causation_id` |

### EventBus Application Layer Peer (062)

| ADR | Title |
|-----|-------|
| [ADR-062](../adr/ADR-062-eventbus-application-layer-peer.md) | EventBus as First-Class Citizen in the Application Layer |

### Outbound Event Gateway and Outbox (063)

| ADR | Title |
|-----|-------|
| [ADR-063](../adr/ADR-063-outbound-event-gateway-and-outbox.md) | Outbound Event Gateway and Transactional Outbox (amended by ADR-065, ADR-074) |

### Outbox Writer Composition (064)

| ADR | Title |
|-----|-------|
| [ADR-064](../adr/ADR-064-outbox-writer-composition.md) | `OutboxWriter` — library-owned write-side join by composition |

### Outbox Retry Policy and Dead-Letter Queue (065)

| ADR | Title |
|-----|-------|
| [ADR-065](../adr/ADR-065-outbox-retry-policy-and-dead-letter.md) | Outbox Retry Policy and Dead-Letter Queue (amended by ADR-074) |

### Test-First Fixes with Documentation (066)

| ADR | Title |
|-----|-------|
| [ADR-066](../adr/ADR-066-test-first-fixes-with-documentation.md) | Test-First Fixes with Documentation in the Same Change |

### Protocol Contract Suites (067)

| ADR | Title |
|-----|-------|
| [ADR-067](../adr/ADR-067-protocol-contract-suites.md) | Protocol Contract Suites in `pydomain.testing.contracts` |

### Rollback Drains Event Buffers (068)

| ADR | Title |
|-----|-------|
| [ADR-068](../adr/ADR-068-rollback-drains-event-buffers.md) | Rollback Drains Event Buffers and Is Idempotent (amended by ADR-078) |

### Upcast to the Class Version Once (069)

| ADR | Title |
|-----|-------|
| [ADR-069](../adr/ADR-069-upcast-to-class-version-once.md) | Upcast Events to the Class Version Exactly Once (amended by ADR-078) |

### DDD Copies, Id Types, Event Stamping and Specifications (070)

| ADR | Title |
|-----|-------|
| [ADR-070](../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md) | Aggregate Copies, Identity Types, Event Stamping and Composite Specifications (amended by ADR-078) |

### Failure Paths Carry No Payload Values (071)

| ADR | Title |
|-----|-------|
| [ADR-071](../adr/ADR-071-library-failure-paths-carry-no-payload-values.md) | Library-Owned Failure Paths Never Carry Payload Values |

### Shipped Fakes Conform to Their Protocol Contracts (072)

| ADR | Title |
|-----|-------|
| [ADR-072](../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md) | Shipped Fakes Conform to Their Protocol Contracts |

### Safe Bootstrap Wiring (073)

| ADR | Title |
|-----|-------|
| [ADR-073](../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md) | Safe Bootstrap Wiring and the Application's Public Accessors (amended by ADR-078) |

### Outbox Relay and Registry Contracts (074)

| ADR | Title |
|-----|-------|
| [ADR-074](../adr/ADR-074-outbox-relay-and-registry-honour-their-contracts.md) | The Outbox Relay and Registry Honour Their Contracts |

### Restore Snapshots by Validation (075)

| ADR | Title |
|-----|-------|
| [ADR-075](../adr/ADR-075-restore-snapshots-by-validation.md) | Restore Snapshots by Validation, Not Attribute Assignment |

### Release Every Lock (076)

| ADR | Title |
|-----|-------|
| [ADR-076](../adr/ADR-076-release-every-lock-under-cancellation-and-failing-releases.md) | Release Every Lock Under Cancellation and Failing Releases |

### Saga Failure and Recovery Keep Every Command (077)

| ADR | Title |
|-----|-------|
| [ADR-077](../adr/ADR-077-saga-failure-and-recovery-keep-every-command.md) | Saga Failure and Recovery Keep Every Command |

### Clean-Up Errors, Stored Versions and Per-Class configure (078)

| ADR | Title |
|-----|-------|
| [ADR-078](../adr/ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md) | Clean-Up Errors, Conflicting Stored Versions and Per-Class `configure()` |

### Commit Inside the Command Pipeline (079)

| ADR | Title |
|-----|-------|
| [ADR-079](../adr/ADR-079-commit-inside-the-command-pipeline.md) | Commit Inside the Command Pipeline |

### Identify Delivered Events for Event Idempotency (080)

| ADR | Title |
|-----|-------|
| [ADR-080](../adr/ADR-080-identify-delivered-events-for-event-idempotency.md) | Identify Delivered Events for Event Idempotency |

### Handler Failures Reach the Inbound Gateway (081)

| ADR | Title |
|-----|-------|
| [ADR-081](../adr/ADR-081-handler-failures-reach-the-inbound-gateway.md) | Handler Failures Reach the Inbound Gateway, and Failure Logs Carry No Content |
