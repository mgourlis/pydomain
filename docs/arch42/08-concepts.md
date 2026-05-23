# 8. Cross-Cutting Concepts

This section documents the recurring patterns, conventions, and mechanisms that span multiple building blocks in the `pydomain` library. These are not isolated to a single module — they are the shared conceptual foundation that every module builds on.

---

## 8.1 Frozen vs. Mutable — The Immutability Axis

The most fundamental distinction in the library is between **mutable** and **immutable** Pydantic models. This is expressed via `ConfigDict(frozen=...)` and shapes the entire design:

```
                         Pydantic BaseModel
                                │
                  ┌─────────────┴─────────────┐
                  │                           │
            frozen=False                frozen=True
                  │                           │
         ┌────────┴────────┐        ┌─────────┴─────────┐
         │   Entity[TId]   │        │   ValueObject      │
         │   (mutable)     │        │   (immutable)      │
         │                 │        │                     │
         │  • id: TId      │        │  • No id field     │
         │  • version: int │        │  • Structural eq   │
         │  • Identity eq  │        │  • Closure of ops  │
         └────────┬────────┘        │    via model_copy   │
                  │                 └─────────────────────┘
         ┌────────┴────────┐
         │ AggregateRoot   │        frozen=True (continued)
         │ [TId]           │        ┌─────────────────────┐
         │                 │        │  DomainEvent        │
         │  • _pending_    │        │  Command[TResult]   │
         │    events       │        │  Query[TResult]     │
         │    (PrivateAttr)│        │  IntegrationEvent   │
         │  • pull_events()│        │  CommandResult      │
         └─────────────────┘        │  QueryResult        │
                                    │  SagaState fields   │
                                    └─────────────────────┘
```

### How it works

| Concept | `frozen` | Equality | Mutation pattern |
|---------|----------|----------|------------------|
| `Entity[TId]` | `False` | By `id` field | Direct field assignment |
| `ValueObject` | `True` | Structural (all fields) | `model_copy(update={...})` |
| `AggregateRoot[TId]` | `False` | By `id` field | Direct + `_add_event()` |
| `DomainEvent` | `True` | Structural | `model_copy()` via `stamp()` |
| `Command[TResult]` | `True` | Structural | Never mutated |
| `Query[TResult]` | `True` | Structural | Never mutated |
| `IntegrationEvent` | `True` | Structural | Never mutated |

### Why this matters

- **Frozen models** get structural equality for free (Pydantic default) and hashability — essential for `ValueObject` comparison and using events in sets.
- **Mutable models** allow aggregate state changes via direct field assignment during command handling. Pydantic's `PrivateAttr` mechanism enables mutable internal state (`_pending_events` list) even on models that might otherwise be frozen.
- The `AggregateRoot` uses `PrivateAttr` specifically because the `_pending_events` list is mutable by nature — events are accumulated, then drained.

---

## 8.2 Domain Event Tracing — Correlation and Causation

Every domain event carries optional tracing IDs that allow reconstructing the full causal chain across aggregate boundaries, saga steps, and distributed services.

### The tracing chain

```
Command (correlation_id, causation_id)
    │
    │  CommandBus resolves:
    │    correlation_id = cmd.correlation_id or cmd.command_id
    │    causation_id   = cmd.causation_id   or cmd.command_id
    │
    ▼
UnitOfWork._collect_and_stamp()
    │  For each event from each repo:
    │    stamped = event.stamp(
    │        correlation_id=self._correlation_id,
    │        causation_id=self._causation_id,
    │    )
    │
    ▼
DomainEvent (correlation_id, causation_id)  ← new frozen copy
    │
    │  SagaManager propagates to commands:
    │    cmd.correlation_id = state.correlation_id
    │    cmd.causation_id   = state.id
    │
    ▼
Next Command (correlation_id, causation_id)
```

### Fields on `DomainEvent`

| Field | Type | Default | Set by |
|-------|------|---------|--------|
| `event_id` | `UUID` | Auto (UUIDv7) | `DomainEvent.__init__` |
| `occurred_at` | `datetime` | Auto (UTC now) | `DomainEvent.__init__` |
| `event_version` | `int` | `1` | Subclass declaration |
| `correlation_id` | `UUID \| None` | `None` | `UnitOfWork._collect_and_stamp()` |
| `causation_id` | `UUID \| None` | `None` | `UnitOfWork._collect_and_stamp()` |

### Key properties

- **Immutability preserved**: `stamp()` returns a new frozen copy via `model_copy(update=...)`. The original event is never mutated.
- **Aggregate is unaware**: The aggregate records events with `correlation_id=None`. Tracing is stamped later by the infrastructure — the domain layer stays pure.
- **Saga tracing**: The `SagaManager` propagates `correlation_id` from the saga state (constant across the saga) and sets `causation_id` to `state.id` (the saga instance identity).

---

## 8.3 Event Collection and Publish-After-Commit

The library enforces **publish-after-commit** semantics: domain events are only dispatched to handlers *after* the Unit of Work has successfully committed. This guarantees that event handlers never see uncommitted state.

### The collection flow

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Handler mutates aggregate                                    │
│    agg.submit() → calls _add_event(OrderSubmitted)              │
│    Event buffered in aggregate._pending_events                  │
│                                                                 │
│ 2. UoW.commit() is called                                       │
│    ┌─────────────────────────────────────────────────────────┐  │
│    │ a. _flush()          → persist to storage               │  │
│    │ b. _collect_and_stamp()                                  │  │
│    │      repo.pull_events() → drains aggregate buffer       │  │
│    │      event.stamp(...)    → new copies with tracing IDs  │  │
│    │      self._events.append(stamped)                       │  │
│    │ c. _write_outbox()   → optional outbox write            │  │
│    │ d. _commit()         → commit underlying transaction    │  │
│    └─────────────────────────────────────────────────────────┘  │
│                                                                 │
│ 3. CommandBus returns (result, events) to MessageBus            │
│                                                                 │
│ 4. MessageBus._dispatch_events(events)                          │
│    → Events now visible to registered handlers                  │
│    → SagaManager, projections, integration event publishers     │
│                                                                 │
│ 5. UoW.__aexit__() — if not committed and no exception: no-op  │
│                      if exception: rollback()                   │
└─────────────────────────────────────────────────────────────────┘
```

### Failure guarantee

| Scenario | Events dispatched? | State persisted? |
|----------|-------------------|------------------|
| Handler succeeds, commit succeeds | ✅ Yes | ✅ Yes |
| Handler raises | ❌ No | ❌ No (rollback) |
| Commit fails | ❌ No | ❌ No (rollback) |
| Event handler fails | ✅ Yes (already committed) | ✅ Yes (other handlers continue) |

---

## 8.4 Generic Result Binding — `Command[TResult]` and `Query[TResult]`

Commands and queries bind their result type at the class level using Python 3.12 generics, making `dispatch()` return type explicit and safe.

### Type binding pattern

```python
# Define a command with its result type
class PlaceOrder(Command[PlaceOrderResult]):
    order_id: UUID
    customer_id: UUID

# Define the result
class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str

# Handler returns the bound type
async def handle_place_order(cmd: PlaceOrder, uow: UnitOfWork) -> PlaceOrderResult:
    ...
    return PlaceOrderResult(order_id=cmd.order_id, status="placed")

# dispatch() returns the bound type
result: PlaceOrderResult = await bus.dispatch(PlaceOrder(...))
```

### Result type hierarchy

```
CommandResult (frozen)
├── EmptyCommandResult          ← void / no meaningful output
└── <user-defined results>      ← PlaceOrderResult, etc.

QueryResult (frozen)
└── <user-defined results>      ← GetOrderResult, etc.
```

### Key points

| Aspect | Command | Query |
|--------|---------|-------|
| Base class | `Command[TResult]` | `Query[TResult]` |
| Result base | `CommandResult` | `QueryResult` |
| Auto-generated ID | `command_id` (UUIDv7) | `query_id` (UUIDv7) |
| Tracing fields | `correlation_id`, `causation_id` | None |
| `extra` config | `"forbid"` | `"forbid"` |
| Void result | `EmptyCommandResult` | N/A (queries always return data) |

---

## 8.5 Pipeline Behavior Middleware

Cross-cutting concerns are implemented as pipeline behaviors that wrap message handlers in an onion (decorator) pattern. The pipeline is composed at registration time and reused across dispatches.

### Pipeline composition

```
MessagePipeline.execute(ctx, message)
    │
    ▼
┌──────────────────────────────────┐
│ LoggingBehavior                  │  ← outermost
│   ┌──────────────────────────┐   │
│   │ ValidationBehavior       │   │  (user-registered validators)
│   │   ┌──────────────────┐   │   │
│   │   │ IdempotencyBehavior│  │   │  (ProcessedCommandStore)
│   │   │   ┌────────────┐ │   │   │
│   │   │   │ Locking    │ │   │   │  (LockProvider + LockKeyResolver)
│   │   │   │ Behavior   │ │   │   │
│   │   │   │  ┌───────┐ │ │   │   │
│   │   │   │  │handler│ │ │   │   │  ← terminal
│   │   │   │  └───────┘ │ │   │   │
│   │   │   └────────────┘ │   │   │
│   │   └──────────────────┘   │   │
│   └──────────────────────────┘   │
└──────────────────────────────────┘
```

### Handler invocation by message kind

| Kind | Terminal signature | UoW passed? |
|------|-------------------|-------------|
| `COMMAND` | `handler(message, uow)` | ✅ Yes |
| `QUERY` | `handler(message)` | ❌ No |
| `EVENT` | `handler(message)` | ❌ No |

### Built-in behaviors

| Behavior | Slot | Purpose | Mechanism |
|----------|------|---------|-----------|
| `LoggingBehavior` | 1 | Structured entry/success/failure logging | `time.perf_counter()` timing, payload formatter |
| `ValidationBehavior` | 2 | Pre-handler validation | User-registered validators per message type |
| `IdempotencyBehavior` | 3 | Duplicate command detection | `ProcessedCommandStore.get/set` by `command_id` |
| `AggregateLockingBehavior` | 4 | Prevent concurrent aggregate mutations | `LockProvider.acquire/release` in sorted key order |

---

## 8.6 Two Projection Styles

The library provides two projection abstractions in different modules, each serving a distinct purpose:

### Comparison

| Aspect | `Projection[StateT]` (cqrs) | `EventSourcedProjection` (es) |
|--------|------------------------------|-------------------------------|
| Module | `pydomain.cqrs.projection` | `pydomain.es.projection` |
| Kind | `Protocol` (structural) | `ABC` (inheritance) |
| Concern | Pure CQRS read-model contract | Event-store-specific projection |
| Methods | `apply()`, `rebuild()` | `handle()`, `apply()`, `rebuild()` |
| Handler dispatch | User-implemented | Convention: `_when_{EventType}` |
| Checkpoint tracking | None | Built-in (`_checkpoint` counter) |
| Use case | Generic read-model interface | Catch-up subscriptions from event log |

### Why two types

The `Projection` protocol captures the **CQRS essence** — applying events and rebuilding — without coupling to any particular event-delivery mechanism. The `EventSourcedProjection` ABC adds event-store-specific concerns (checkpoint tracking, handler dispatch convention) that only make sense when reading from a versioned event stream.

A class can satisfy both:

```python
class OrderSummaryProjection(EventSourcedProjection):
    """Satisfies Projection[StateT] protocol AND EventSourcedProjection ABC."""
    name: ClassVar[str] = "order_summary"
    version: ClassVar[int] = 1

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        ...
```

---

## 8.7 Integration Events — Cross-Boundary Messaging

`IntegrationEvent` is the mechanism for publishing domain facts to external consumers via a `MessageBroker`. Unlike `DomainEvent`, it restricts payloads to primitive types.

### Domain Event vs Integration Event

| Aspect | `DomainEvent` | `IntegrationEvent` |
|--------|--------------|-------------------|
| Module | `pydomain.ddd` | `pydomain.cqrs` |
| Frozen | ✅ | ✅ |
| Fields | Any Pydantic-compatible types | **Primitives only**: str, int, float, bool, dict, list, None |
| `event_id` | `UUID` | `str` (stringified UUID) |
| `occurred_at` | `datetime` | `str` (ISO 8601) |
| Tracing | `correlation_id`, `causation_id` | None |
| Validation | Pydantic field validators | `@model_validator` enforces primitive-only fields |

### Why primitives only

Integration events are serialized to message brokers (RabbitMQ, Kafka, etc.) that may not support complex Python types. By restricting to JSON-native primitives, the `IntegrationEvent` class guarantees that no custom serialization logic is needed — `model_dump()` always produces a broker-safe dict.

### Typical flow

```
DomainEvent (from aggregate)
    │
    ▼
EventHandler translates:
    integration = OrderCreatedIntegrationEvent(
        order_id=str(event.order_id),
        customer_id=str(event.customer_id),
    )
    │
    ▼
MessageBroker.publish(topic, integration.model_dump())
```

---

## 8.8 Event Versioning via Upcasting

Event schemas evolve over time. The library handles this through **upcasting** — transforming old event payloads to the current schema at deserialization time, without modifying the event log.

### Upcaster chain

```
EventStore (raw dict, version=1)
    │
    ▼
EventRegistry.deserialize(data)
    │  resolve("OrderPlaced", version=1)
    │  → [OrderPlacedV1ToV2, OrderPlacedV2ToV3]
    │
    ▼
Upcaster chain:
    payload = OrderPlacedV1ToV2().upcast(payload)    # v1 → v2
    payload = OrderPlacedV2ToV3().upcast(payload)    # v2 → v3
    │
    ▼
OrderPlaced.model_validate(payload)                  # v3 schema
```

### Key properties

| Property | Implementation |
|----------|---------------|
| **Append-only** | Events in the store are never modified — upcasting happens at read time |
| **Chain resolution** | `UpcasterRegistry.resolve()` follows `(source_type, source_version)` → `target_version` hops until no further upcaster exists |
| **Cycle detection** | The registry tracks visited versions and raises `UpcastError` on cycles |
| **Opt-in** | Upcasting only runs if an `UpcasterRegistry` is configured on the `EventRegistry` |
| **Weak-schema fallback** | Unknown event types deserialize as `GenericDomainEvent` (raw dict) instead of raising |

### Upcaster contract

```python
class OrderPlacedV1ToV2(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        event["discount"] = event.get("discount", 0.0)  # additive field
        return event
```

---

## 8.9 Snapshotting — Performance Optimization for Event Replay

Snapshots cache derived aggregate state at a point in time to avoid replaying the entire event stream on every load.

### Snapshot lifecycle

```
save(aggregate)
    │
    ├── pull_events() → persist to EventStore
    │
    └── SnapshotPolicy.should_snapshot()?
         │
         YES → aggregate._take_snapshot()
         │         model_dump(mode='python')
         │         pop('version')
         │         → Snapshot(aggregate_id, version, state)
         │
         └── SnapshotStore.save(aggregate_type, snapshot)

get_by_id(id)
    │
    ├── SnapshotStore.get(type, id)?
    │   │
    │   FOUND → restore fields from snapshot.state
    │          set version = snapshot.version
    │          read_stream(id, from_version=snapshot.version)
    │          _replay() remaining events
    │
    └── NOT FOUND → full replay from version 0
```

### Snapshot policy

The default `SnapshotThresholdPolicy` snapshots every N events:

```python
SnapshotThresholdPolicy(threshold=10)
# Triggers when current_version % 10 == 0
```

Setting `threshold=0` snapshots on every save (useful for small aggregates).

---

## 8.10 Saga State as Aggregate — Idempotency and Compensation

`SagaState` is itself an `AggregateRoot[UUID]`, gaining optimistic concurrency, event tracking, and repository support for free.

### Why an aggregate

| Capability | Provided by `AggregateRoot` |
|------------|---------------------------|
| Optimistic concurrency | `version` field checked on save |
| Event collection | `pull_events()` for saga lifecycle events |
| Identity | `id` (UUID) for correlation lookup |
| Repository pattern | Works with standard `Repository` protocol |

### Idempotency mechanism

```python
# SagaState tracks processed events
processed_event_ids: set[UUID] = Field(default_factory=set)

# Saga.handle() skips duplicates
if self.state.is_event_processed(event.event_id):
    return  # idempotent — already processed
```

### Compensation stack

```python
# Forward step registers compensation (LIFO)
self.on(OrderCreated,
    send=lambda e: ReserveItems(order_id=e.order_id),
    compensate=lambda e: CancelReservation(order_id=e.order_id))

# On failure, compensations execute in reverse order
compensation_stack: list[CompensationRecord]  # LIFO via pop()
```

### Memory bounds

Long-lived sagas can accumulate significant state. The `SagaState` provides configurable caps:

| Bound | Field | Default (unlimited) | Purpose |
|-------|-------|---------------------|---------|
| Processed events | `max_processed_events` | `0` | Caps `processed_event_ids` set |
| Step history | `max_step_history` | `0` | Caps `step_history` list |
| Pruning | `prune_history()` | — | Manual cleanup of old entries |
### Command hydration

When a saga produces commands that are later reconstructed from serialised data (e.g., from a message broker or outbox), the `hydrate_command()` function in `cqrs/saga/hydration.py` handles the deserialization:

1. **Resolve the class**: `importlib.import_module(module_name)` + `getattr(mod, command_type)` locates the concrete `Command` subclass.
2. **Strip unknown keys**: Filters `data` to only include keys present in `cls.model_fields`, so `extra="forbid"` on the command class does not reject payloads from newer schema versions.
3. **Validate**: Calls `cls.model_validate(filtered)` to reconstruct the instance.
4. **Graceful failure**: Returns `None` if the module/type cannot be resolved (e.g., command from a different service) or validation fails — no exceptions propagate.

This makes saga-driven command dispatch resilient to schema evolution and cross-service boundaries.
---

## 8.11 Entity Identity and Auto-Generation

All entities carry a typed `id` field. When `id` is omitted at construction, the entity auto-generates one using the configured `IdGenerator[TId]`. A **runtime type guard** verifies that the generated value matches the declared `TId` annotation — if it does not, a `DomainError` is raised.

### Auto-generation flow

```
Entity.__init__(id=...)
    │
    ├── id provided? → use it
    │
    └── id omitted?
            │
            ▼
        Entity._ensure_id (model_validator, mode="before")
            generated = cls._id_generator.generate()
            │
            ├── isinstance(generated, TId)? → data["id"] = generated
            │
            └── type mismatch? → raise DomainError(
                "Uuid7Generator produced UUID, but Order expects int")
```

### Configurable generators

The `IdGenerator[TId]` protocol is generic — implementations declare the ID type they produce:

```python
class Uuid7Generator:
    def generate(self) -> UUID: ...          # IdGenerator[UUID]

class SnowflakeIdGenerator:
    def generate(self) -> int: ...           # IdGenerator[int]
```

Configuration at startup:

```python
# Global default (affects all Entity subclasses)
Entity.configure(id_generator=Uuid7Generator())

# Per-subclass override
class Order(Entity[int]):
    _id_generator: ClassVar[IdGenerator[Any]] = SnowflakeIdGenerator()
```

The default `Uuid7Generator` produces UUIDv7 identifiers (time-ordered, sortable) via `uuid-utils`. All three base types (`Entity`, `DomainEvent`, `Command`) maintain independent `_id_generator` class variables.

---

## 8.12 Read-Side Persistence — ProjectionStore vs. User-Defined Read Stores

The library provides two mechanisms for persisting projection state, depending on the complexity of the read model. The choice follows a clear principle: **use `ProjectionStore` for simple state blobs; define domain-specific read store Protocols for denormalized read models with indexes**.

### Simple case: `ProjectionStore`

`ProjectionStore` is a key-value contract: `save(projection_id, state)` and `load(projection_id) -> Any | None`. It stores a **single opaque state blob** per projection identity. This fits projections that maintain counters, summaries, or aggregated state:

```python
# A summary projection — one state object per projection_id
class OrderSummaryProjection(EventSourcedProjection):
    ...

# Save the derived state
await projection_store.save("order_summary", projection.state)

# Load it back in a query handler
raw = await projection_store.load("order_summary")
result = OrderSummaryState.model_validate(raw)
```

### Complex case: user-defined read store Protocols

When a projection maintains **many rows with typed columns and indexes** (e.g., a denormalized list of orders searchable by customer, date, or status), `ProjectionStore` is insufficient. Instead, users define a domain-specific read store Protocol in the application layer:

```python
# application/read_stores.py — no SQL, no infrastructure imports

class OrderListByCustomerStore(Protocol):
    async def find_by_customer(
        self, customer_id: UUID, *, limit: int = 50
    ) -> list[OrderRow]: ...

    async def find_by_date_range(
        self, start: datetime, end: datetime
    ) -> list[OrderRow]: ...
```

The projection writes directly to its dedicated table via its own database connection (infrastructure layer). The query handler depends on the Protocol, not on SQL:

```python
# application/handlers.py — depends on Protocol, not infrastructure

class GetOrdersByCustomerHandler(QueryHandler[GetOrdersByCustomer, list[OrderRow]]):
    def __init__(self, store: OrderListByCustomerStore) -> None:
        self._store = store

    async def handle(self, query: GetOrdersByCustomer) -> list[OrderRow]:
        return await self._store.find_by_customer(query.customer_id, limit=query.limit)
```

```python
# infrastructure/read_stores.py — SQL lives here

class PostgresOrderListByCustomerStore:
    def __init__(self, db: AsyncConnection) -> None:
        self._db = db

    async def find_by_customer(
        self, customer_id: UUID, *, limit: int = 50
    ) -> list[OrderRow]:
        rows = await self._db.fetch(
            """SELECT order_id, customer_id, total, created_at
               FROM read_order_list_by_customer
               WHERE customer_id = $1
               ORDER BY created_at DESC LIMIT $2""",
            customer_id, limit,
        )
        return [OrderRow(**dict(r)) for r in rows]
    ...
```

### Layer discipline

The same layer separation applies to both sides:

```
WRITE SIDE                              READ SIDE
──────────                              ──────────
CommandHandler → Repository[T, TId]    QueryHandler → User-defined ReadStore Protocol
                    ↓                                       ↓
Infrastructure implements it            Infrastructure implements it
(SqlAlchemyRepository)                  (PostgresOrderListByCustomerStore)
```

### When to choose which

| Factor | `ProjectionStore` | User-defined ReadStore Protocol |
|--------|-------------------|-------------------------------|
| State shape | One blob per `projection_id` | Many rows in a dedicated table |
| Query pattern | Load all or nothing | Filtered, paginated, indexed |
| Denormalization | Single view | Multiple tables for different access patterns |
| Library provides | `ProjectionStore` Protocol + `InMemoryProjectionStore` fake | Pattern only — users define their own Protocols |
