# 12. Glossary

Terms are grouped by module. Each entry notes the source module path and kind
(class, Protocol, attribute, enum, or concept).

---

## DDD Building Blocks (`pydomain.ddd`)

### Aggregate Root
**`pydomain.ddd.aggregate_root`** — class `AggregateRoot[TId]`

The root `Entity` of a consistency boundary in DDD.
Extends `Entity[TId]` with a private `_pending_events` buffer.
Events are recorded via `_add_event()` and drained via `pull_events()` —
typically called by the Unit of Work after commit.

All access to internal objects goes through the root. Only Aggregate Roots
have Repositories.

### Causation ID
**`pydomain.ddd.domain_event`** — attribute of `DomainEvent`

`causation_id: UUID | None` — identifies the message that directly caused
this event. Set by the Unit of Work during `commit()` via `stamp()`.
Together with `correlation_id`, forms the distributed-tracing chain.

### Concurrency Error
**`pydomain.ddd.exceptions`** — class `ConcurrencyError`

Raised when optimistic concurrency check fails — the aggregate's `version`
changed between load and save. Extends `DomainError`.

### Correlation ID
**`pydomain.ddd.domain_event`** — attribute of `DomainEvent`

`correlation_id: UUID | None` — links all events belonging to the same
workflow or operation. Set by the Unit of Work during `commit()` via
`stamp()`. Constant across the entire causal chain.

### Domain Error
**`pydomain.ddd.exceptions`** — class `DomainError`

Base exception for all domain-layer errors.

### Domain Event
**`pydomain.ddd.domain_event`** — class `DomainEvent`

An immutable record of a fact that happened in the domain.
Named in **past tense**. Frozen Pydantic model (`frozen=True`).
Auto-generates `event_id` (UUIDv7) and `occurred_at` (UTC).

| Field | Type | Description |
|---|---|---|
| `event_id` | `UUID` | Auto-generated UUIDv7 |
| `occurred_at` | `datetime` | UTC timestamp |
| `event_version` | `int` | Schema version (default `1`) |
| `correlation_id` | `UUID \| None` | Links events in the same workflow |
| `causation_id` | `UUID \| None` | Identifies the causing message |

Method `stamp(correlation_id, causation_id)` returns a new frozen copy
with tracing IDs set — the original is never mutated.

### Domain Service
**`pydomain.ddd.domain_service`** — class `DomainService`

Stateless marker base class for domain logic that does not belong to any
single Entity or Value Object. Carries no state or behaviour; exists as an
architectural signal that the component lives in the domain layer.

### Entity
**`pydomain.ddd.entity`** — class `Entity[TId]`

An object defined by its **identity** (`id` field) rather than its
attributes. Two entities are equal iff they share the same type and `id`.
Mutable (`frozen=False`). Auto-generates `id` via the configured
`IdGenerator` when omitted at construction.

| Field | Type | Description |
|---|---|---|
| `id` | `TId` | Identity — immutable, auto-generated if omitted |
| `version` | `int` | Optimistic concurrency counter (default `0`) |

### Event ID
**`pydomain.ddd.domain_event`** — attribute of `DomainEvent`

`event_id: UUID` — unique identifier for the event, auto-generated as
UUIDv7 via `Uuid7Generator`.

### Event Version
**`pydomain.ddd.domain_event`** — attribute of `DomainEvent`

`event_version: int` — schema version of the event payload. Defaults to `1`.
Used by the `UpcasterRegistry` to migrate events across schema versions.

### Factory
**`pydomain.ddd.factory`** — Protocol `Factory[T]`

Structural protocol for encapsulating complex creation logic. Any class
with a `create()` method returning `T` automatically conforms — no
inheritance required.

### ID Generator
**`pydomain.ddd.id_generator`** — Protocol `IdGenerator[TId]`

Protocol for generating identifiers. Defines `generate() -> TId`.
Implemented by `Uuid7Generator` (default).

### Occurred At
**`pydomain.ddd.domain_event`** — attribute of `DomainEvent`

`occurred_at: datetime` — UTC timestamp of when the event was created.

### Reconstitution Factory
**`pydomain.ddd.factory`** — Protocol `ReconstitutionFactory[T]`

Protocol for rebuilding domain objects from persisted state. Any class
with a `reconstitute()` method returning `T` conforms.
Must **never** generate a new tracking identity — identity comes from
persisted data.

### Repository
**`pydomain.ddd.repository`** — Protocol `Repository[T, TId]`

Persistence contract for aggregate roots. Only Aggregate Roots get
repositories. Interface belongs to the domain layer; implementation
belongs to infrastructure. Tracks `seen` aggregates for the Unit of Work.

| Method | Description |
|---|---|
| `save(aggregate, command_id)` | Persist aggregate, drain events into internal buffer |
| `get_by_id(id_)` | Retrieve by identity, or `None` |
| `delete(id_)` | Remove (idempotent) |
| `pull_events()` | Drain and return collected `DomainEvent`s |

### Specification
**`pydomain.ddd.specification`** — class `Specification`

A frozen Value Object encapsulating a business rule as a predicate
(`is_satisfied_by`). Composable via `and_()`, `or_()`, `not_()`.
Supports subsumption checking (`subsumes()`).

Three uses: validation, selection (querying repositories), and generation
(building to order).

Related composites: `AndSpecification`, `OrSpecification`,
`NotSpecification`.

### Specification Error
**`pydomain.ddd.exceptions`** — class `SpecificationError`

Raised when a specification-based validation rule fails.
Extends `DomainError`.

### UUIDv7 Generator
**`pydomain.ddd.id_generator`** — class `Uuid7Generator`

Default ID generator. Produces time-ordered UUIDv7 identifiers via
`uuid_utils.uuid7`. Structurally conforms to `IdGenerator[UUID]`.

### Value Object
**`pydomain.ddd.value_object`** — class `ValueObject`

An immutable object defined by its attributes — not by identity.
Frozen (`frozen=True`). Structural equality (Pydantic default).
Operations return new instances via `model_copy(update=...)`.
No `id` field.

### Version (Entity)
**`pydomain.ddd.entity`** — attribute of `Entity`

`version: int` — optimistic concurrency counter. Incremented by
`EventSourcedAggregateRoot._apply()` and `_replay()`. Used by repositories
for concurrency checks during `save()`.

---

## CQRS (`pydomain.cqrs`)

### Command
**`pydomain.cqrs.commands`** — class `Command[TResult]`

An immutable message expressing **intent** — "do this."
Named in **imperative mood**. Frozen (`frozen=True`, `extra="forbid"`).
One handler per command type. Binds a typed result via
`TResult : CommandResult`.

| Field | Type | Description |
|---|---|---|
| `command_id` | `UUID` | Auto-generated UUIDv7 |
| `correlation_id` | `UUID \| None` | Distributed tracing (set by saga manager) |
| `causation_id` | `UUID \| None` | Distributed tracing (set by saga manager) |

### Command Bus
**`pydomain.cqrs.command_bus`** — class `CommandBus`

Routes commands to their single registered handler. Creates a UoW per
dispatch via the registered factory. Runs the `PipelineBehavior` chain.
On success: commits, stamps events with tracing IDs. On failure:
rollbacks and re-raises. Returns `(CommandResult, list[DomainEvent])`.

### Command Execution Error
**`pydomain.cqrs.exceptions`** — class `CommandExecutionError`

Wraps handler exceptions. Carries the failed `command` for diagnostics.

### Command Handler
**`pydomain.cqrs.handlers`** — Protocol `CommandHandler[TCommand, TResult]`

Receives a command and the transaction-scoped `UnitOfWork`.
Returns a `CommandResult`. Must **not** call `uow.commit()` or
`uow.rollback()` — the bus manages lifecycle.

### Command ID
**`pydomain.cqrs.commands`** — attribute of `Command`

`command_id: UUID` — unique identifier for the command invocation.
Auto-generated as UUIDv7.

### Command Result
**`pydomain.cqrs.commands`** — class `CommandResult`

Abstract frozen base for command execution results. Each concrete
`Command` declares the exact result type its handler produces.

### CQRS Error
**`pydomain.cqrs.exceptions`** — class `CQRSError`

Base exception for all CQRS-layer errors. Extends `DomainError`.

### Dict Lock Key Resolver
**`pydomain.cqrs.locking`** — class `DictLockKeyResolver`

Registry-based `LockKeyResolver`. Maps message types to key-extraction
functions. Collects returned keys from all registered functions.

### Empty Command Result
**`pydomain.cqrs.commands`** — class `EmptyCommandResult`

Void-style result (`CommandResult` subclass) for commands that produce no
meaningful output.

### Event Handler
**`pydomain.cqrs.handlers`** — Protocol `EventHandler[TEvent]`

Receives a domain event, performs side effects. Returns `None`
(fire-and-forget). Multiple handlers per event type. Handlers **fail
independently** — one handler's failure must not affect others.

### Handler Already Registered Error
**`pydomain.cqrs.exceptions`** — class `HandlerAlreadyRegisteredError`

Raised when registering a handler for a message type that already has one.

### Idempotent Command Ignored
**`pydomain.cqrs.exceptions`** — class `IdempotentCommandIgnored`

Raised when a duplicate command is detected and silently ignored.
Carries `command_id`.

### Integration Event
**`pydomain.cqrs.integration_events`** — class `IntegrationEvent`

Cross-boundary counterpart to `DomainEvent`. Carries **primitives only**
(str, int, float, bool, dict, list, None) for broker serialization.
Frozen. Auto-generates `event_id` and `occurred_at` as **strings**
(not UUID/datetime).

### Lock Key Resolver
**`pydomain.cqrs.locking`** — Protocol `LockKeyResolver`

Resolves lock keys from a message. Return empty list for "no locking
needed." Defines `resolve(message) -> list[str]`.

### Lock Provider
**`pydomain.cqrs.locking`** — Protocol `LockProvider`

Acquires and releases named locks for concurrency-safe message handling.
Defines `acquire(key)` and `release(key)`. Not a replacement for
optimistic concurrency checks in the domain layer.

### Logging Behavior
**`pydomain.cqrs.behaviors`** — class `LoggingBehavior`

Built-in `PipelineBehavior` that logs entry, success, and failure with
wall-clock duration measurement. Uses `logging.getLogger("pydomain.pipeline")`.

### Message Context
**`pydomain.cqrs.behaviors`** — dataclass `MessageContext`

Mutable carrier flowing through the pipeline. Every behavior and the
terminal handler receive the same instance.

| Field | Description |
|---|---|
| `message` | The command, query, or event being dispatched |
| `handler` | Resolved handler callable |
| `kind` | `MessageKind` enum: `COMMAND`, `EVENT`, `QUERY` |
| `uow` | Transaction-scoped `UnitOfWork` (commands only) |
| `correlation_id` | Distributed tracing correlation |
| `causation_id` | Distributed tracing causation |
| `metadata` | Arbitrary key-value pairs for downstream behaviors |
| `new_events` | Domain events produced during command handling |

### Message Kind
**`pydomain.cqrs.behaviors`** — enum `MessageKind`

Distinguishes message categories in pipeline behaviors:
`COMMAND`, `EVENT`, `QUERY`.

### Message Pipeline
**`pydomain.cqrs.behaviors`** — class `MessagePipeline`

Composable pipeline wrapping a handler with `PipelineBehavior` instances.
Built at registration time, reused across dispatches. Executes behaviors
in onion order (first in list = outermost).

### MISSING
**`pydomain.cqrs.idempotency`** — sentinel `MISSING`

Sentinel object distinguishing "never processed" from a cached `None`
result in `ProcessedCommandStore.get()`.

### No Handler Registered Error
**`pydomain.cqrs.exceptions`** — class `NoHandlerRegisteredError`

Raised when dispatching a message with no registered handler.

### Pipeline Behavior
**`pydomain.cqrs.behaviors`** — Protocol `PipelineBehavior`

Middleware protocol wrapping message handlers in onion (decorator)
pattern. Each behavior runs before and after calling `next()`.
Defines `handle(ctx, next)`.

### Processed Command Store
**`pydomain.cqrs.idempotency`** — Protocol `ProcessedCommandStore`

Tracks which command IDs have been processed for idempotency.
Stores `CommandResult` values keyed by command UUID.

| Method | Description |
|---|---|
| `get(command_id)` | Return cached result, or `MISSING` |
| `set(command_id, result)` | Persist result |
| `contains(command_id)` | Check if already processed |

### Projection (CQRS)
**`pydomain.cqrs.projection`** — Protocol `Projection[StateT]`

Pure CQRS projection protocol. Transforms domain events into a
query-optimized read model using the left-fold pattern:
`current_state + event → new_state`.

| Method | Description |
|---|---|
| `apply(event)` | Apply a single event |
| `rebuild(events)` | Reset and replay full stream |

### Projection Store
**`pydomain.cqrs.projection`** — Protocol `ProjectionStore`

Persists opaque projection read model state, keyed by `projection_id`.
No checkpoint concept — only derived state.

| Method | Description |
|---|---|
| `load(projection_id)` | Load state, or `None` |
| `save(projection_id, state)` | Persist state |

### Query
**`pydomain.cqrs.queries`** — class `Query[TResult]`

An immutable message asking for data — "give me this."
Named in descriptive mood. Read-only: no side effects, no aggregate
mutation. Binds a typed result via `TResult : QueryResult`.

| Field | Type | Description |
|---|---|---|
| `query_id` | `UUID` | Auto-generated UUIDv7 |

### Query Bus
**`pydomain.cqrs.query_bus`** — class `QueryBus`

Routes queries to their single registered handler. No `UnitOfWork`.
No side effects. No events collected.

### Query Handler
**`pydomain.cqrs.handlers`** — Protocol `QueryHandler[TQuery, TResult]`

Receives a query, returns a typed `QueryResult`. Read-only — no
`UnitOfWork`.

### Query ID
**`pydomain.cqrs.queries`** — attribute of `Query`

`query_id: UUID` — unique identifier for the query invocation.
Auto-generated as UUIDv7.

### Query Result
**`pydomain.cqrs.queries`** — class `QueryResult`

Abstract frozen base for query execution results.

### Unit of Work
**`pydomain.cqrs.unit_of_work`** — Protocol `UnitOfWork`

Manages transactional scope and domain event collection.
Publish-after-commit semantics. Context manager (`async with`).

| Method | Description |
|---|---|
| `commit()` | Persist changes, stamp events with tracing IDs |
| `rollback()` | Undo all changes |
| `collect_events()` | Return stamped `DomainEvent`s after commit |

If the context manager exits without explicit `commit()`, the UoW
rolls back by default.

### Abstract Unit of Work
**`pydomain.cqrs.unit_of_work`** — class `AbstractUnitOfWork`

Reusable ABC implementing the full commit/rollback lifecycle.
Extension hooks (overridable no-ops): `_flush()`, `_write_outbox()`,
`_commit()`. Subclasses populate `_repos` with repository instances
and expose typed repository attributes for handlers.

---

## Saga Subsystem (`pydomain.cqrs.saga`)

### Compensation Record
**`pydomain.cqrs.saga.state`** — class `CompensationRecord`

Serialized compensating command for LIFO execution on failure.
Frozen Pydantic model.

| Field | Type | Description |
|---|---|---|
| `command_type` | `str` | Class name of the compensating command |
| `data` | `dict[str, Any]` | Serialized command payload |
| `description` | `str` | Human-readable description |
| `module_name` | `str` | Python module path for hydration |

### hydrate_command
**`pydomain.cqrs.saga.hydration`** — function `hydrate_command(module_name, command_type, data)`

Reconstructs a `Command` instance from serialized data using `importlib`
and `model_validate()`. Returns `None` if the module or type cannot be
resolved. Strips unknown keys for schema-evolution resilience.

### Saga
**`pydomain.cqrs.saga.saga`** — class `Saga[S: SagaState]`

Base class for sagas / process managers — explicit state machines for
long-running processes. Provides two event-handling styles:

- **Command mapper** — `on(EventType, send=lambda e: Command(...))`
- **Custom handler** — `on(EventType, handler=self.handle_event)`

| Class Attribute | Description |
|---|---|
| `state_class` | `ClassVar[type[SagaState]]` — state type |
| `listens_to` | `ClassVar[list[type[DomainEvent]]]` — handled event types |

Key methods: `handle(event)` — idempotent entry point.
`on()` — DSL for event-to-command mapping with optional compensation.

### Saga Configuration Error
**`pydomain.cqrs.saga.exceptions`** — class `SagaConfigurationError`

Invalid saga setup — e.g. conflicting `handler`/`send` registration in
`on()`. Extends `SagaError`.

### Saga Error
**`pydomain.cqrs.saga.exceptions`** — class `SagaError`

Base exception for all saga-related errors. Extends `CQRSError`.

### Saga Handler Not Found Error
**`pydomain.cqrs.saga.exceptions`** — class `SagaHandlerNotFoundError`

No handler registered for an event type. Extends `SagaError`.

### Saga Manager
**`pydomain.cqrs.saga.manager`** — class `SagaManager`

Orchestrates saga lifecycle: **load → handle → save → dispatch**.

For each incoming event:

1. Finds all saga classes registered for the event type via `SagaRegistry`.
2. Loads or creates `SagaState` via `SagaRepository`.
3. Instantiates the `Saga` and calls `handle(event)`.
4. Saves state back to the repository.
5. Dispatches pending commands via the `CommandBus`.

### Saga Pruning Policy
**`pydomain.cqrs.saga.pruning`** — Protocol `SagaPruningPolicy`

Decides whether a saga's history should be pruned to bound memory growth.

| Member | Description |
|---|---|
| `keep_last_n_steps` | Steps to retain after pruning |
| `keep_last_n_events` | Event IDs to retain (or `None` for all) |
| `should_prune(saga_type, state)` | Returns `True` if pruning recommended |

### Saga Registry
**`pydomain.cqrs.saga.registry`** — class `SagaRegistry`

Injectable registry mapping `event_type → list[type[Saga]]`.
Multiple sagas can react to the same event.
Also maps by type name for recovery/timeout lookups.

### Saga Repository
**`pydomain.cqrs.saga.repository`** — Protocol `SagaRepository`

Persistence contract for `SagaState`. Extends basic repository with
saga-specific queries.

| Method | Description |
|---|---|
| `save(state)` | Persist `SagaState` (insert or update) |
| `get_by_id(id_)` | Retrieve by identity, or `None` |
| `find_by_correlation_id(correlation_id, saga_type)` | Locate saga in a correlation chain |
| `find_stalled_sagas(limit)` | Sagas with undispatched pending commands |
| `find_suspended_sagas(limit)` | Sagas in `SUSPENDED` status |
| `find_expired_suspended_sagas(limit)` | Suspended sagas past `timeout_at` |
| `pull_events()` | Drain collected `DomainEvent`s |

### Saga State
**`pydomain.cqrs.saga.state`** — class `SagaState(AggregateRoot[UUID])`

Mutable aggregate root tracking the full lifecycle of a saga instance.
Covers identity, step history, idempotency, pending commands,
compensation stack, suspension, timeouts, retries, and audit timestamps.

| Field | Type | Description |
|---|---|---|
| `saga_type` | `str` | Saga class name |
| `status` | `SagaStatus` | Lifecycle state |
| `current_step` | `str` | Current step name |
| `step_history` | `list[StepRecord]` | Ordered step transitions |
| `processed_event_ids` | `set[UUID]` | Idempotency guard |
| `pending_commands` | `list[dict]` | Serialized commands awaiting dispatch |
| `compensation_stack` | `list[CompensationRecord]` | LIFO compensating commands |
| `failed_compensations` | `list[dict]` | Failed compensation records |
| `suspended_at` | `datetime \| None` | Suspension timestamp |
| `suspension_reason` | `str \| None` | Reason for suspension |
| `timeout_at` | `datetime \| None` | Auto-expiry for suspension |
| `retry_count` | `int` | Current retry count |
| `max_retries` | `int` | Maximum retries (default `3`) |
| `error` | `str \| None` | Last error message |
| `correlation_id` | `UUID \| None` | Distributed tracing |
| `causation_id` | `UUID \| None` | Distributed tracing |
| `created_at` | `datetime` | Creation timestamp |
| `updated_at` | `datetime` | Last update timestamp |
| `metadata` | `dict[str, Any]` | Arbitrary context |

Memory bounds: `max_processed_events` and `max_step_history` ClassVar
caps (default `0` = unlimited).

### Saga State Error
**`pydomain.cqrs.saga.exceptions`** — class `SagaStateError`

Invalid saga state transition or lifecycle violation.
Extends `SagaError`.

### Saga Status
**`pydomain.cqrs.saga.state`** — enum `SagaStatus`

Lifecycle states for a saga instance:

| State | Description |
|---|---|
| `PENDING` | Created, no event processed yet |
| `RUNNING` | Actively processing events |
| `SUSPENDED` | Paused (human-in-the-loop pattern) |
| `COMPLETED` | Successfully finished |
| `FAILED` | Terminally failed |
| `COMPENSATING` | Executing compensating commands |
| `COMPENSATED` | Compensation complete |

### Step Record
**`pydomain.cqrs.saga.state`** — class `StepRecord`

Immutable record of a single saga step transition. Frozen.

| Field | Type | Description |
|---|---|---|
| `step_name` | `str` | Logical step identifier |
| `event_type` | `str` | Event class name that triggered the step |
| `causation_id` | `UUID \| None` | Event that caused this step |
| `occurred_at` | `datetime` | UTC timestamp |
| `metadata` | `dict[str, Any]` | Arbitrary context |

### Step Threshold Pruning Policy
**`pydomain.cqrs.saga.pruning`** — class `StepThresholdPruningPolicy`

Concrete pruning strategy: prune when `step_history` exceeds a threshold.
Never prunes during `COMPENSATING`, `SUSPENDED`, or terminal states.

---

## Event Sourcing (`pydomain.es`)

### Checkpoint
**`pydomain.es.checkpoint_store`** — concept

An integer position in the global event log, tracking how far a
subscription has processed. Persisted by `CheckpointStore`. Used by
`SubscriptionRunner` for catch-up subscriptions.

### Checkpoint Store
**`pydomain.es.checkpoint_store`** — Protocol `CheckpointStore`

Persists subscription checkpoints (last processed global event version).

| Method | Description |
|---|---|
| `load(subscription_id)` | Return last processed version, or `0` |
| `save(subscription_id, checkpoint)` | Persist checkpoint |

### Duplicate Command Error
**`pydomain.es.exceptions`** — class `DuplicateCommandError`

Raised when a `command_id` was already processed for an aggregate.
Carries `aggregate_id` and `command_id`.

### Event Store
**`pydomain.es.event_store`** — Protocol `EventStore`

Persistence contract for event streams. The source of truth in
event-sourced systems.

| Method | Description |
|---|---|
| `append_to_stream(aggregate_id, events, expected_version, command_id)` | Append with optimistic concurrency |
| `read_stream(aggregate_id, from_version)` | Read single aggregate's events → `EventStream` |
| `read_all(from_version)` | Read global event log → `EventStream` |

### Event Stream
**`pydomain.es.event_stream`** — class `EventStream`

Frozen read-only representation of an event stream.

| Field | Type | Description |
|---|---|---|
| `events` | `Sequence[DomainEvent]` | Ordered event list |
| `version` | `int` | Stream length (per-stream or global) |

### Event-Sourced Aggregate Root
**`pydomain.es.aggregate`** — class `EventSourcedAggregateRoot[TId]`

Aggregate whose state is rebuilt from an event stream. Subclasses call
`_apply(event)` to both mutate state and record the event.
During reconstitution, `_replay(event)` rebuilds state without buffering.

| Method | Description |
|---|---|
| `_apply(event)` | Call `_when()`, buffer event, increment `version` |
| `_when(event)` | **Abstract** — subclasses dispatch by `isinstance` |
| `_replay(event)` | Call `_when()`, increment `version` (no buffer) |
| `_take_snapshot()` | Serialize state → `Snapshot` for snapshot store |
| `pull_events()` | Drain `_pending_events` buffer |

Class attribute: `_snapshot_schema_version: ClassVar[int]` — default `1`.

### Event-Sourced Projection
**`pydomain.es.projection`** — class `EventSourcedProjection(ABC)`

Projection backed by a versioned event stream. Adds checkpoint tracking
and convention-based `_when_{EventTypeName}` handler dispatch.

| Class Attribute | Description |
|---|---|
| `name` | `ClassVar[str]` — projection identity for checkpoint lookups |
| `version` | `ClassVar[int]` — schema version |

| Method | Description |
|---|---|
| `handle(event)` | Dispatch to `_when_{EventTypeName}` by convention |
| `apply(event)` | Handle + increment `_checkpoint` |
| `rebuild(events)` | Reset checkpoint, replay all events |

Property `checkpoint: int` — the event version processed up to.

### Event-Sourced Repository
**`pydomain.es.event_sourced_repository`** — class `EventSourcedRepository[T, TId]`

Concrete base class for event-sourced persistence.

- `save()` — drain pending events, append to event store with optimistic
  concurrency, optionally take snapshot via `SnapshotPolicy`.
- `get_by_id()` — read event stream, optionally use snapshot for fast
  hydration, replay remaining events.
- `pull_events()` — drain internal event buffer for Unit of Work.

### Event Upcaster
**`pydomain.es.upcasting`** — class `EventUpcaster`

Base class for event schema migration. Subclasses declare class variables
and implement `_transform()`:

| Class Variable | Description |
|---|---|
| `source_type` | Event type name to upcast FROM |
| `source_version` | Schema version to upcast FROM |
| `target_version` | Schema version to upcast TO |

Method `upcast(event_dict)` applies the transformation, raising
`UpcastError` on failure.

### Snapshot
**`pydomain.es.snapshot`** — class `Snapshot`

Captures full aggregate state at a specific version for fast rebuild.
Never replaces the event log — used for performance only.

| Field | Type | Description |
|---|---|---|
| `aggregate_id` | `str` | Aggregate identity |
| `version` | `int` | Aggregate version at snapshot time |
| `state` | `dict[str, Any]` | Full serialized aggregate state |
| `schema_version` | `int` | Aggregate schema version (default `1`) |
| `created_at` | `datetime` | UTC timestamp |

### Snapshot Policy
**`pydomain.es.snapshot`** — Protocol `SnapshotPolicy`

Decides when to take a snapshot. Defines `should_snapshot(aggregate_type,
aggregate_id, current_version, pending_event_count) -> bool`.

### Snapshot Schema Policy
**`pydomain.es.snapshot`** — Protocol `SnapshotSchemaPolicy`

Decides whether a snapshot is compatible with the current aggregate
schema. Defines `should_use_snapshot(snapshot, expected_schema_version)
-> bool`.

### Snapshot Store
**`pydomain.es.snapshot`** — Protocol `SnapshotStore`

Persists snapshots keyed by aggregate type and ID.

| Method | Description |
|---|---|
| `save(aggregate_type, snapshot)` | Persist snapshot |
| `get(aggregate_type, aggregate_id)` | Load snapshot, or `None` |

### Snapshot Threshold Policy
**`pydomain.es.snapshot`** — class `SnapshotThresholdPolicy`

Takes a snapshot every N events (`current_version % threshold == 0`).
When `threshold` is `0`, snapshots on every flush.

### Stale Snapshot Error
**`pydomain.es.exceptions`** — class `StaleSnapshotError`

Raised when a snapshot's `schema_version` doesn't match the aggregate's
expected `_snapshot_schema_version`. Carries diagnostic information.

### Stream Not Found Error
**`pydomain.es.exceptions`** — class `StreamNotFoundError`

Raised when an event stream does not exist for the given `aggregate_id`.

### Upcaster Registry
**`pydomain.es.upcasting`** — class `UpcasterRegistry`

Stores and resolves upcasters by `(source_type, source_version)`.
Chains upcasters to migrate events across multiple schema versions.
Detects cycles.

### Upcast Error
**`pydomain.es.exceptions`** — class `UpcastError`

Raised when an upcaster fails to transform an event payload.

### Reject Stale Snapshot Policy
**`pydomain.es.snapshot`** — class `RejectStaleSnapshotPolicy`

Rejects snapshots whose `schema_version` doesn't match the expected
version. Causes the repository to fall back to full event replay.

---

## Infrastructure (`pydomain.infrastructure`)

### Application
**`pydomain.infrastructure.bootstrap`** — class `Application`

Configured entry point wrapping a `MessageBus`. Provides `dispatch()`
for unified command and query dispatch. Also holds optional
`EventRegistry` and `SnapshotStore` references.

### bootstrap()
**`pydomain.infrastructure.bootstrap`** — function `bootstrap()`

Composition root. Wires event store, message bus, message broker,
event registry, and snapshot store into a configured `Application`.
Tests call it with fakes; production calls it with real adapters.

### Event Registry
**`pydomain.infrastructure.event_registry`** — class `EventRegistry`

Maps event type names to Pydantic model classes for dynamic
serialization/deserialization. Falls back to `GenericDomainEvent` for
unrecognized types. Optionally integrates with `UpcasterRegistry`.

### Generic Domain Event
**`pydomain.infrastructure.event_registry`** — class `GenericDomainEvent`

Weak-schema fallback for unrecognized event types. Carries `type: str`,
`data: dict[str, Any]`, and `version: int`.

### InMemoryMessageBroker
**`pydomain.infrastructure.message_broker`** — class `InMemoryMessageBroker`

Test double implementing `MessageBroker`. Captures published events
for test assertions.

### Message Broker
**`pydomain.infrastructure.message_broker`** — Protocol `MessageBroker`

Publishes `IntegrationEvent` instances to external brokers (RabbitMQ,
Kafka, etc.).

| Method | Description |
|---|---|
| `publish(topic, event)` | Publish an integration event |
| `start()` | Initialize connection (startup) |
| `stop()` | Graceful shutdown |

### MessageBus (Infrastructure)
**`pydomain.infrastructure.message_bus`** — class `MessageBus`

Level-3 facade wrapping `CommandBus`, `QueryBus`, and event dispatcher.
Unified `dispatch()` inspects message type and routes accordingly.
After command execution, collected domain events are dispatched to
registered `EventHandler`s.

### Subscription
**`pydomain.infrastructure.subscription`** — dataclass `Subscription`

Binds a projection to the event types it handles.

| Field | Type | Description |
|---|---|---|
| `subscription_id` | `str` | Unique identity |
| `projection` | `EventSourcedProjection` | Target projection |
| `event_types` | `tuple[type[DomainEvent], ...]` | Event filter |

### Subscription Runner
**`pydomain.infrastructure.subscription`** — ABC `SubscriptionRunner`

Coordinates catch-up subscriptions. Polls `EventStore.read_all()` from
checkpoint, filters by event type, delegates to `process_batch()`.
At-least-once guarantee — checkpoint updates only after successful batch.

| Method | Description |
|---|---|
| `add_subscription(subscription)` | Register a subscription |
| `run()` | Polling loop until `stop()` |
| `run_once()` | Single catch-up pass (for tests) |
| `stop()` | Request graceful exit |
| `process_batch(events, subscription)` | **Abstract** — process matching events |

---

## Testing (`pydomain.testing`)

Test doubles implementing their respective Protocols. Designed for use
in unit tests without infrastructure dependencies. Fakes over mocks.

| Test Double | Satisfies Protocol | Purpose |
|---|---|---|
| `FakeRepository[T, TId]` | `Repository[T, TId]` | In-memory aggregate storage |
| `FakeUnitOfWork` | `AbstractUnitOfWork` | No-op transaction scope with event collection |
| `FakeEventStore` | `EventStore` | In-memory event stream storage |
| `FakeSnapshotStore` | `SnapshotStore` | In-memory snapshot storage |
| `FakeCheckpointStore` | `CheckpointStore` | In-memory checkpoint tracking |
| `FakeSagaRepository` | `SagaRepository` | In-memory saga state storage |
| `FakeProcessedCommandStore` | `ProcessedCommandStore` | In-memory idempotency tracking |
| `FakeLockProvider` | `LockProvider` | No-op lock (always succeeds) |
| `InMemoryMessageBroker` | `MessageBroker` | Captures published integration events |
| `InMemoryProjectionStore` | `ProjectionStore` | In-memory projection state storage |
