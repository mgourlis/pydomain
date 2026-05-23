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

Target `pydantic >= 2.7` exclusively. No v1 compatibility layer.

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
self.on(OrderCreated,
    send=lambda e: ReserveItems(order_id=e.order_id),
    step="reserving",
    compensate=lambda e: CancelReservation(order_id=e.order_id))
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

Once events are dispatched, individual handler failures do **not** affect other handlers. The `MessageBus._dispatch_events()` catches and logs per-handler exceptions:

```
Event dispatched to 3 handlers
  Handler A → succeeds
  Handler B → raises exception (logged, caught)
  Handler C → succeeds (unaffected by Handler B)
```

This design recognizes that after commit, the event **happened**. One handler's failure should not prevent others from processing the event.

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
def calculate_total_price(items: list[LineItem], tax_rate: TaxRate) -> Money:
    ...
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

---

## ADR Reference — All 60 Decisions

### Base / Foundational (001–005)

| ADR | Title |
|-----|-------|
| [ADR-001](../adr/ADR-001-protocol-over-abc-for-interfaces.md) | Protocol over ABC for interfaces |
| [ADR-002](../adr/ADR-002-pydantic-v2-only.md) | Pydantic v2 only — no v1 compatibility shims |
| [ADR-003](../adr/ADR-003-async-only-public-api.md) | Async-only public API |
| [ADR-004](../adr/ADR-004-exception-hierarchy-by-layer.md) | Exception hierarchy by layer |
| [ADR-005](../adr/ADR-005-publish-after-commit.md) | Publish events after commit, never before |

### DDD Module (006–013)

| ADR | Title |
|-----|-------|
| [ADR-006](../adr/ADR-006-entity-identity-semantics.md) | Entity identity semantics — `id: TId` with structural equality |
| [ADR-007](../adr/ADR-007-entity-auto-id-with-runtime-guard.md) | Entity auto-ID with runtime type guard |
| [ADR-008](../adr/ADR-008-uuidv7-time-ordered-identity.md) | UUIDv7 time-ordered identity generation |
| [ADR-009](../adr/ADR-009-domain-service-marker.md) | DomainService as a marker class, not a base class |
| [ADR-010](../adr/ADR-010-specification-abc-basemodel-hybrid.md) | Specification as ABC + BaseModel hybrid |
| [ADR-011](../adr/ADR-011-domainevent-stamp-immutability.md) | DomainEvent `stamp()` preserves immutability |
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
| [ADR-019](../adr/ADR-019-sorted-lock-keys-deadlock-prevention.md) | Sorted lock keys for deadlock prevention |
| [ADR-020](../adr/ADR-020-commandbus-owns-uow-lifecycle.md) | CommandBus owns UoW lifecycle |
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
| [ADR-031](../adr/ADR-031-saga-manager-separate-orchestrator.md) | SagaManager as separate orchestrator |
| [ADR-032](../adr/ADR-032-saga-correlation-propagation.md) | Saga correlation via `event.correlation_id` |
| [ADR-033](../adr/ADR-033-lifo-compensation-stack.md) | LIFO compensation stack via serialized `CompensationRecord` |
| [ADR-034](../adr/ADR-034-saga-suspension-with-timeout.md) | Saga suspension with timeout (human-in-the-loop) |
| [ADR-035](../adr/ADR-035-crash-recovery-pending-commands.md) | Crash recovery via `pending_commands` per-command tracking |
| [ADR-036](../adr/ADR-036-saga-idempotency-processed-events.md) | Saga idempotency via `processed_event_ids` set |

### Event Sourcing Module (037–043)

| ADR | Title |
|-----|-------|
| [ADR-037](../adr/ADR-037-eventstream-frozen-value-object.md) | EventStream as frozen value object |
| [ADR-038](../adr/ADR-038-dual-mode-event-application.md) | Dual-mode event application — `_apply()` records, `_replay()` reconstitutes |
| [ADR-039](../adr/ADR-039-convention-dispatch-projections.md) | Convention dispatch in projections — `_when_{TypeName}` methods |
| [ADR-040](../adr/ADR-040-event-sourced-repository-concrete-base.md) | EventSourcedRepository as concrete base class |
| [ADR-041](../adr/ADR-041-optimistic-concurrency-command-idempotency.md) | Optimistic concurrency via `expected_version` + `command_id` idempotency |
| [ADR-042](../adr/ADR-042-event-upcaster-chain-cycle-detection.md) | EventUpcaster chain with cycle detection |
| [ADR-043](../adr/ADR-043-snapshot-policy-pluggable-protocol.md) | Snapshot policy as pluggable protocol |

### Infrastructure (044–049)

| ADR | Title |
|-----|-------|
| [ADR-044](../adr/ADR-044-dynamic-event-registry-generic-fallback.md) | Dynamic event registry with `GenericDomainEvent` fallback |
| [ADR-045](../adr/ADR-045-messagebus-level3-facade.md) | MessageBus as Level 3 facade |
| [ADR-046](../adr/ADR-046-event-handlers-fail-independently.md) | Event handlers fail independently — per-handler try/except |
| [ADR-047](../adr/ADR-047-bootstrap-composition-root.md) | `bootstrap()` composition root |
| [ADR-048](../adr/ADR-048-subscription-runner-at-least-once.md) | SubscriptionRunner at-least-once delivery |
| [ADR-049](../adr/ADR-049-catch-up-subscriptions-polling.md) | Catch-up subscriptions via polling SubscriptionRunner |

### Cross-Cutting (050–052)

| ADR | Title |
|-----|-------|
| [ADR-050](../adr/ADR-050-aggregate-pending-events-private-attr.md) | AggregateRoot `_pending_events` as `PrivateAttr` |
| [ADR-051](../adr/ADR-051-message-broker-separate-boundary.md) | `MessageBroker` Protocol — separate boundary from MessageBus |
| [ADR-052](../adr/ADR-052-checkpoint-store-vs-snapshot-store.md) | `CheckpointStore` vs `SnapshotStore` — two separate persistence concerns |

### Snapshot Schema (053)

| ADR | Title |
|-----|-------|
| [ADR-053](../adr/ADR-053-snapshot-schema-version-policy.md) | Snapshot schema version policy for stale snapshot detection |

### SAGA Pruning Policy (054)

| ADR | Title |
|-----|-------|
| [ADR-054](../adr/ADR-054-saga-pruning-policy-pluggable-protocol.md) | Saga pruning policy as pluggable protocol |

### Declarative Failure (055)

| ADR | Title |
|-----|-------|
| [ADR-055](../adr/ADR-055-declarative-saga-failure-fail-true.md) | Declarative saga failure via `fail=True` and callable reason parameters |

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
