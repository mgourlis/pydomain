# `ddd_cqrs_es` — Python DDD/CQRS/ES Library

A Python 3.12+ installable library implementing Domain-Driven Design (DDD), Command-Query Responsibility Segregation (CQRS), and Event Sourcing (ES) building blocks. The canonical definitions for every concept come from the wiki at `/home/mgourlis/knowledge/wiki-cqrs-ddd/wiki/`.

---

## Behavioral guidelines (Karpathy)

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so.
- If something is unclear, stop and ask.

### 2. Simplicity First
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" that wasn't requested.
- If you write 200 lines and it could be 50, rewrite it.

### 3. Surgical Changes
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style.
- Remove only imports/variables/functions that **your** changes made unused.

### 4. Goal-Driven Execution
- Transform tasks into verifiable goals with a brief plan before starting.
- Weak criteria ("make it work") require clarification before proceeding.

---

## Project facts

| Item | Value |
|---|---|
| Python | ≥ 3.12 |
| Package name | `ddd_cqrs_es` |
| Install target | other Python projects (library, not an application) |
| Validation | Pydantic v2 only (`pydantic>=2.7`) |
| Build backend | `hatchling` |
| Async | `async`/`await` throughout; use `anyio` for async tests |
| Linter | `ruff` (target `py312`) |

---

## Pydantic v2 rules

Always use Pydantic v2 APIs. Never use v1 compatibility shims.

| Need | v2 API |
|---|---|
| Immutable model | `model_config = ConfigDict(frozen=True)` |
| Mutable model | `model_config = ConfigDict(frozen=False)` |
| Private field (not serialised) | `PrivateAttr(default_factory=...)` |
| Copy with changes | `model.model_copy(update={...})` |
| Serialise to dict | `model.model_dump()` |
| Deserialise from dict | `MyModel.model_validate(data)` |
| Custom validators | `@field_validator` / `@model_validator` |
| Computed field | `@computed_field` |

`BaseModel` subclasses **must not** mix `frozen=True` fields with `PrivateAttr` mutation unless the private attribute itself is mutable (list, dict). This is valid and how `AggregateRoot._pending_events` works.

---

## Concept definitions (authoritative wiki pages)

Read these before implementing or modifying a concept. Wiki root: `/home/mgourlis/knowledge/wiki-cqrs-ddd/wiki/`.

### DDD Tactical Patterns

**Entity** (`concepts/entity.md`)
- Defined by identity, not attributes.
- Identity is an immutable `UUID` field.
- Pydantic `BaseModel` with `frozen=False` (state changes are expected).
- Two entities are equal iff their `id` fields are equal.

**Value Object** (`concepts/value-object.md`)
- Defined by its attributes, not by identity.
- Always immutable: `ConfigDict(frozen=True)`.
- Structural equality (Pydantic default when frozen).
- Operations return new instances via `model_copy(update=...)`.
- No `id` field.

**Domain Event** (`concepts/domain-event.md`)
- An immutable record of a fact that happened in the domain.
- Named in **past tense** in the Ubiquitous Language (`OrderPlaced`, not `PlaceOrder`).
- Is a `ValueObject` (frozen Pydantic model).
- Carries `event_id: UUID`, `occurred_at: datetime`, `correlation_id`, `causation_id`.
- Carries business intent, not entire entity state.
- Events **fail independently** — one handler's failure must not affect others.

**Aggregate / Aggregate Root** (`concepts/aggregate.md`)
- A cluster of objects with a single consistency boundary.
- All access to internal objects goes through the root `Entity`.
- Only Aggregate Roots have Repositories.
- Carries `version: int` for optimistic concurrency.
- Collects domain events in `_pending_events: PrivateAttr`; drain via `pull_events()`.
- Invariants must hold after every mutation; enforce them inside the aggregate method.

**Repository** (`concepts/repository.md`)
- Abstract interface expressed in domain language, not persistence language.
- Only Aggregate Roots get repositories.
- Interface belongs in the domain layer; implementation belongs in infrastructure.
- Must track `seen` aggregates so the Unit of Work can publish their events.
- Exactly one Repository per Aggregate Root type.

**Domain Service** (`concepts/domain-service.md`)
- Stateless operation that doesn't belong to any single Entity or Value Object.
- Named with a verb in the Ubiquitous Language.
- Lives in the domain layer; no infrastructure imports.

**Specification** (`concepts/specification.md`)
- A `ValueObject` that encapsulates a business rule as a predicate (`is_satisfied_by`).
- Three uses: validation, selection (querying repositories), and generation (building to order).
- Composable with `AND` / `OR` / `NOT`.

**Factory** (`concepts/factory.md`)
- Encapsulates creation and reconstitution of complex Aggregates.
- Hides the construction details from clients.

### CQRS

**Command** (`concepts/command.md`)
- Captures *intent* — a request to do something.
- Named in the **imperative** mood (`Allocate`, `CreateBatch`).
- Frozen Pydantic model (immutable once created).
- One handler per command type; failure bubbles up (fail-loud).

**Query**
- A request for data that has no side effects.
- One handler per query type; returns a read model (plain dict, dataclass, or Pydantic model).
- Must not use the domain write model; raw SQL or a separate read store is preferred.

**Command Bus** (`concepts/message-bus.md`)
- Routes a command to exactly one registered handler.
- `register(CommandType, handler)` raises on duplicate.
- `dispatch(command)` raises if no handler registered.
- Handler exceptions propagate.

**Query Bus**
- Same structure as Command Bus but for queries.

**Message Bus** (`concepts/message-bus.md`)
- Publish-subscribe system for domain events.
- Maps event types to lists of handlers.
- Event handlers fail independently — catch and log per handler, do not abort the queue.
- Processes an event queue (events may produce more events; collect and process until empty).

**Projection** (`concepts/projection.md`)
- Transforms event data into a query-optimised read model.
- Left-fold pattern: `current_state + event → new_state`.
- Read models are disposable — they can always be rebuilt from the event log.
- Sync projections (same transaction as the write) are valid and preferred when possible.

**Unit of Work** (`concepts/unit-of-work.md`)
- Context manager that provides repository access and guarantees atomicity.
- On `commit()`: persist all changes, then publish collected domain events to the message bus.
- On exit without commit: rollback.
- The Unit of Work (not route handlers) calls `commit()`.

### Event Sourcing

**Event Sourcing** (`concepts/event-sourcing.md`)
- The event log is the source of truth; current state is derived by replaying events.
- Events are immutable and append-only.
- Current state is a projection (left-fold over the event stream).
- Does **not** require async; does **not** require CQRS (though they compose well).

**Snapshot** (`concepts/snapshot.md`)
- Saved derived state at a point in time to avoid replaying the entire event stream.
- Never replaces the event log — used for performance only.
- Carry a schema version to detect stale snapshots.

**Event Versioning** (`concepts/event-versioning.md`)
- Events are immutable; schemas evolve via upcasting (transforming old events to new format on read).
- Additive-only changes (new optional fields) are the safest evolution strategy.
- Weak schema (JSON) gives version insensitivity at the cost of type safety.

**Compensating Action** (`concepts/compensating-action.md`)
- Mistakes are corrected by appending new corrective events, not by editing history.

---

## File & naming conventions

```
src/
  ddd_cqrs_es/
    __init__.py
    domain/
      entity.py           # Entity base class
      value_object.py     # ValueObject base class
      aggregate_root.py   # AggregateRoot base class
      domain_event.py     # DomainEvent base class
      domain_service.py   # DomainService base class (optional)
      specification.py    # Specification base class
      exceptions.py       # ConcurrencyError, DomainError, etc.
      repository.py       # Abstract Repository base
    application/
      command.py          # Command base class
      query.py            # Query base class
      command_bus.py      # CommandBus
      query_bus.py        # QueryBus
      message_bus.py      # MessageBus (event dispatch)
      unit_of_work.py     # UnitOfWork abstract base
    infrastructure/
      repository.py       # SQLAlchemy / in-memory base implementations
      event_store.py      # EventStore abstract base + implementations
      snapshot_store.py   # SnapshotStore abstract base + implementations
    es/
      aggregate.py        # EventSourcedAggregate base class
      projection.py       # Projection / EventSourcedProjection base
```

**Naming rules:**
- No `utils.py`, `helpers.py`, `common.py`.
- No `schemas.py` — Pydantic models go in their role-specific file.
- Domain concepts use Ubiquitous Language terms (past-tense events, imperative commands).
- Abstract base classes are prefixed with nothing; concrete implementations are suffixed with their technology (`SqlAlchemyRepository`, `InMemoryRepository`).

---

## What NOT to do

- **Don't leak infrastructure into the domain.** `domain/` imports only `pydantic`, `uuid`, `datetime`, and the standard library.
- **Don't use `session.commit()` directly in handlers.** The Unit of Work manages commits.
- **Don't raise exceptions for domain events.** If a domain concept is expressed as an event, don't also raise an exception for it.
- **Don't conflate** Aggregate (DDD tactical pattern) with aggregate (generic data aggregation).
- **Don't conflate** Domain Event (fact that happened in the domain) with technical/system events.
- **Don't assume CQRS implies Event Sourcing.** They are independent patterns that compose well.
- **Don't mutate aggregates from outside.** Only the aggregate's own methods enforce invariants.
- **Don't use Pydantic v1 APIs** (`__fields__`, `schema()`, `parse_obj()`, `validator`, `root_validator`).

---

## Testing conventions

- `tests/test_domain.py` — pure unit tests, no I/O, test domain logic directly.
- `tests/test_buses.py` — test command/query/message bus registration and dispatch.
- `tests/test_application.py` — handler tests using `FakeRepository` and `FakeUnitOfWork`.
- Use `pytest-anyio` + `anyio` for async tests (`asyncio_mode = "auto"`).
- Fakes live in `tests/conftest.py` or a `tests/fakes.py` module.
- Never mock what you don't own. Use `FakeRepository` instead of mocking SQLAlchemy.

---

## References

- Wiki (DDD/CQRS/ES theory): `/home/mgourlis/knowledge/wiki-cqrs-ddd/wiki/`
- FastAPI template (implementation patterns): `/home/mgourlis/Development/Microservice/Fast-api-rest-cqrs-ddd-event-sourcing-template/ddd-core/`
- Pydantic v2 docs (Context7 ID): `/pydantic/pydantic`
