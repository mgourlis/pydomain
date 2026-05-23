# 10. Quality

This section describes how the library's quality goals (defined in §1.3) are verified and enforced in practice. It covers the quality tree, concrete verification mechanisms, and the testing strategy.

---

## 10.1 Quality Tree

The library's quality attributes are organized into three categories. Each leaf node maps to a verification mechanism described in the subsections below.

```
                         pydomain Quality
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                     │
     Architectural          Correctness          Operational
     Quality                Quality               Safety
          │                    │                     │
    ┌─────┴─────┐       ┌─────┴─────┐        ┌─────┴──────┐
    │           │       │           │        │            │
  Module     Type     Test        Code    Optimistic   Publish-
  Boundaries Safety   Coverage    Quality Concurrency  After-Commit
  (§10.2)    (§10.3)  (§10.4)     (§10.5) (§10.6)      (§10.6)
```

---

## 10.2 Module Boundary Enforcement

The strict layered dependency graph (§2.5) is the library's most important architectural invariant. Violations — a `ddd/` file importing from `cqrs/`, or `cqrs/` importing from `es/` — are considered architecture bugs.

### Enforcement mechanism

| Mechanism | How | When |
|-----------|-----|------|
| **Ruff `TID252`** | Bans relative imports — forces absolute `pydomain.*` paths so cross-module dependencies are explicit and auditable | `make lint` (every commit) |
| **Code review** | `architect-reviewer` checks import paths during DDD boundary review (CLAUDE.md workflow step ⑦) | Pre-merge |
| **Manual audit** | `grep` / Pyright workspace scan for forbidden cross-module imports | Periodic; onboarding new modules |

### Permitted imports by module

```
ddd/           → pydantic, uuid, datetime, typing, logging, uuid_utils, stdlib
cqrs/          → ddd/ + same external libs as ddd/
es/            → ddd/ + same external libs as ddd/
infrastructure/→ ddd/, cqrs/, es/
testing/       → ddd/, cqrs/, es/, infrastructure/
```

### What is not allowed

| Violation | Example | Why it's wrong |
|-----------|---------|----------------|
| `ddd` → `cqrs` | `from pydomain.cqrs.command import Command` | Domain layer depends on application layer |
| `ddd` → `es` | `from pydomain.es.event_store import EventStore` | Domain layer depends on event-sourcing mechanism |
| `cqrs` → `es` | `from pydomain.es.aggregate import ...` | CQRS layer must work without event sourcing |
| `es` → `cqrs` | `from pydomain.cqrs.command_bus import CommandBus` | ES layer must work without CQRS buses |
| `infrastructure` → `testing` | `from pydomain.testing.fakes import ...` | Production code depends on test doubles |

---

## 10.3 Type Safety

Python 3.12 generics (PEP 695) bind types at the point of use, eliminating `Any` returns from public APIs. The type checker verifies this at development time.

### Type checker configuration

Two type checkers are used, both in strict mode:

| Checker | Config | Command |
|---------|--------|---------|
| **Pyright** (Pylance) | `pyrightconfig.json`: `typeCheckingMode = "strict"`, `pythonVersion = "3.12"` | VS Code inline; `pyright` CLI |
| **mypy** | `pyproject.toml`: `disallow_untyped_defs`, `disallow_untyped_calls`, `disallow_incomplete_defs`, `pydantic.mypy` plugin | `make type` |

### Generic type bindings

Every generic base class binds its type parameters at the user's declaration site, making `dispatch()` return the exact declared type:

```
Entity[TId]            → id: TId, version: int
AggregateRoot[TId]     → inherits Entity[TId] + event buffer
Command[TResult]       → dispatch() → TResult
Query[TResult]         → dispatch() → TResult
Repository[T, TId]     → save(T), get_by_id(TId) → T
Saga[S]                → state type is S
IdGenerator[TId]       → generate() → TId
```

### Runtime type safety

Where generics are erased at runtime, explicit type guards fill the gap:

| Guard | Location | What it checks |
|-------|----------|----------------|
| `isinstance(generated, TId)` | `Entity._ensure_id()` | Auto-generated ID matches declared type; raises `DomainError` on mismatch |
| `@runtime_checkable` | All `Protocol` classes | Enables `isinstance()` checks for defensive validation |
| `extra = "forbid"` | `Command[TResult]`, `Query[TResult]` | Rejects unknown fields at construction time |

---

## 10.4 Testing Strategy

### Test infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Test runner | `pytest >= 8.0` | Discovery, fixtures, parametrize |
| Async backend | `pytest-anyio` + `anyio >= 4.0` | Framework-agnostic async (`asyncio_mode = "auto"`) |
| Coverage | `pytest-cov` | Branch coverage reporting |
| Test doubles | `pydomain.testing` | Fakes for every infrastructure Protocol |

### Coverage target

≥ 90% branch coverage across all modules (defined in §1.7).

### Test file organization

Tests are organized by concern, not by module:

```
tests/
├── ddd/
│   ├── test_entity.py           # Entity[TId], auto-ID, equality, type guard
│   ├── test_value_object.py     # ValueObject immutability, structural equality
│   ├── test_aggregate_root.py   # Event collection, pull_events(), version
│   ├── test_domain_event.py     # DomainEvent fields, stamp(), immutability
│   ├── test_specification.py    # Specification composition (AND/OR/NOT)
│   ├── test_factory.py          # Factory[T], ReconstitutionFactory[T] protocols
│   ├── test_id_generator.py     # IdGenerator[TId] protocol, Uuid7Generator
│   └── test_exceptions.py       # DomainError, ConcurrencyError hierarchy
├── cqrs/
│   ├── test_commands.py         # Command[TResult] construction, frozen, forbid
│   ├── test_queries.py          # Query[TResult] construction
│   ├── test_command_bus.py      # Registration, dispatch, duplicate handler
│   ├── test_query_bus.py        # Registration, dispatch
│   ├── test_behaviors.py        # PipelineBehavior chain, MessageContext
│   ├── test_unit_of_work.py     # Commit/rollback lifecycle, event stamping
│   ├── test_integration_events.py # Primitive-only enforcement
│   └── test_projection.py       # Projection[StateT] protocol conformance
├── es/
│   ├── test_event_sourced_aggregate.py  # _apply(), _replay(), _when()
│   ├── test_event_store.py               # Append, read_stream, read_all
│   ├── test_event_sourced_repository.py  # Snapshot + replay, optimistic concurrency
│   ├── test_snapshot.py                  # SnapshotPolicy, SnapshotThresholdPolicy
│   ├── test_upcasting.py                 # UpcasterRegistry, EventUpcaster chain
│   └── test_es_projection.py             # EventSourcedProjection, _when_* dispatch
├── saga/
│   ├── test_saga.py              # Saga[S] on() DSL, handle(), compensation
│   ├── test_saga_manager.py      # SagaManager orchestration, event → commands
│   ├── test_saga_registry.py     # SagaRegistry lookup
│   ├── test_saga_state.py        # SagaState aggregate, idempotency, pruning
│   └── test_hydration.py         # hydrate_command(), unknown-key stripping
├── infrastructure/
│   ├── test_message_bus.py       # MessageBus facade, event dispatch
│   ├── test_bootstrap.py         # bootstrap() composition root
│   ├── test_event_registry.py    # EventRegistry serialization
│   └── test_subscription.py      # SubscriptionRunner catch-up
├── testing/
│   └── test_fakes.py             # Verify all fakes satisfy their Protocols
└── conftest.py                   # Shared fixtures (FakeRepository, FakeUoW, etc.)
```

### Fakes over mocks

The library follows the principle: **never mock what you don't own**. Test doubles are complete in-memory implementations of `Protocol` interfaces, not `unittest.mock.MagicMock` patches.

| Fake | Protocol it satisfies | Key behaviour |
|------|-----------------------|---------------|
| `FakeRepository[T, TId]` | `Repository[T, TId]` | In-memory dict storage, optimistic concurrency check, `seen` tracking |
| `FakeUnitOfWork` | `UnitOfWork` | Commit/rollback lifecycle, event collection + stamping |
| `FakeEventStore` | `EventStore` | In-memory append-only stream storage |
| `FakeSnapshotStore` | `SnapshotStore` | In-memory dict of snapshots |
| `FakeCheckpointStore` | `CheckpointStore` | In-memory checkpoint counter |
| `FakeSagaRepository` | `SagaRepository` | In-memory saga state storage |
| `FakeProcessedCommandStore` | `ProcessedCommandStore` | In-memory dedup set |
| `FakeLockProvider` | `LockProvider` | In-memory semaphore-based locks |
| `InMemoryMessageBroker` | `MessageBroker` | Captures published messages |
| `InMemoryProjectionStore` | `ProjectionStore` | In-memory read model storage |

### Test categories

| Category | Scope | I/O | Speed |
|----------|-------|-----|-------|
| **Domain unit tests** | Single class (Entity, VO, Aggregate, Specification) | None | < 10ms |
| **Bus tests** | CommandBus, QueryBus, MessageBus dispatch | None (fakes) | < 50ms |
| **Handler tests** | Command/Query handler + FakeUoW + FakeRepository | None (fakes) | < 100ms |
| **Saga tests** | Saga orchestration, hydration, compensation | None (fakes) | < 100ms |
| **ES integration** | EventSourcedRepository + FakeEventStore + FakeSnapshotStore | None (fakes) | < 200ms |

All tests run without infrastructure. No Docker, no database, no message broker.

---

## 10.5 Code Quality Toolchain

The library enforces code quality through automated tooling integrated into the development workflow.

### Linting and formatting

| Tool | Configuration | Enforcement |
|------|--------------|-------------|
| **Ruff** (linter) | `target-version = "py312"`, `line-length = 88` | Rules: `E` (errors), `F` (pyflakes), `I` (isort), `UP` (pyupgrade). `make lint`. |
| **Ruff** (formatter) | Same config as linter | `make format`. Replaces Black. |

### Static analysis

| Tool | Configuration | Purpose |
|------|--------------|---------|
| **Pyright** | `typeCheckingMode = "strict"` | Primary type checker (VS Code Pylance) |
| **mypy** | `strict = true`, `pydantic.mypy` plugin enabled | Secondary type checker (`make type`) |

### Make targets

```bash
make lint       # Ruff lint check
make format     # Ruff auto-format
make type       # mypy type checking
make test       # pytest with coverage
make check      # lint + type + test (full gate)
```

`make check` is the complete quality gate. All targets must pass before merge.

---

## 10.6 Operational Safety Mechanisms

These mechanisms protect production correctness at runtime. They are design-level guarantees, verified by tests.

### Optimistic concurrency

Every `AggregateRoot` carries a `version: int` field. The repository checks this on `save()` — if the persisted version differs from the in-memory version, a `ConcurrencyError` is raised. This prevents lost updates when two handlers mutate the same aggregate concurrently.

```
Handler A loads Order (version=3)
Handler B loads Order (version=3)
Handler B saves Order (version=3→4)  ← succeeds
Handler A saves Order (version=3→4)  ← ConcurrencyError (expected 3, found 4)
```

### Publish-after-commit

Domain events are only dispatched to handlers *after* the Unit of Work commits successfully (§8.3). This guarantees event handlers never see uncommitted state.

### Idempotent command handling

The `IdempotencyBehavior` pipeline behavior tracks `command_id` in a `ProcessedCommandStore`. Duplicate dispatches are detected and return the cached result without re-executing the handler.

### Saga compensation

The saga subsystem records a compensation stack (LIFO) for each forward step. On failure, compensating actions execute in reverse order. Saga state tracks `processed_event_ids` to skip duplicate events.

### Schema evolution resilience

- **Upcasting** (`EventUpcaster` chain) transforms old event payloads to current schema at read time — the event log is never modified.
- **Unknown-key stripping** (`hydrate_command()`) removes extra fields before `model_validate()`, so consumers with older schemas don't break when the producer evolves.
- **Weak-schema fallback** (`GenericDomainEvent`) handles unknown event types gracefully instead of raising.

---

## 10.7 Quality Scenarios

Key quality scenarios that tests must cover. These are not exhaustive — they represent the most critical behaviours.

| ID | Quality Attribute | Scenario | Verification |
|----|-------------------|----------|--------------|
| QS-1 | Modularity | Adding a new `Entity` subclass requires importing only `pydomain.ddd` | Import test |
| QS-2 | Modularity | `cqrs` module works when `es` is not imported | No-import test |
| QS-3 | Type safety | `dispatch(Command[TResult])` returns `TResult`, not `Any` | Pyright/mypy verify return type |
| QS-4 | Type safety | Auto-generated ID type mismatch raises `DomainError` | Unit test |
| QS-5 | Correctness | Two entities with same `id` are equal regardless of other fields | Unit test |
| QS-6 | Correctness | `ValueObject` mutations return new instances; original unchanged | Unit test |
| QS-7 | Correctness | `pull_events()` drains the buffer; second call returns empty list | Unit test |
| QS-8 | Correctness | `stamp()` returns a new frozen copy; original event unchanged | Unit test |
| QS-9 | Operational safety | Concurrent aggregate saves raise `ConcurrencyError` | Unit test with fake repo |
| QS-10 | Operational safety | Event handlers see only committed events | Handler test with UoW |
| QS-11 | Operational safety | Duplicate command dispatch returns cached result | Idempotency behavior test |
| QS-12 | Operational safety | Saga compensation stack executes in reverse order | Saga test |
| QS-13 | Testability | All tests run without Docker, database, or message broker | CI runs `make check` with no external services |
| QS-14 | Schema evolution | `hydrate_command()` strips unknown keys before validation | Hydration test |
| QS-15 | Schema evolution | `UpcasterRegistry` transforms old event to current schema | Upcasting test |
