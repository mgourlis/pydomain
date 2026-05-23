# 1. Introduction and Goals

## 1.1 Overview

**pydomain** is a Python 3.12+ library that provides reusable building blocks for implementing Domain-Driven Design (DDD), Command-Query Responsibility Segregation (CQRS), and Event Sourcing (ES) patterns — individually or composed. It is distributed as a single installable package on PyPI and is designed to be integrated into other Python projects, not run as a standalone application.

The library is **opinionated about the patterns but unopinionated about infrastructure**. It provides tactical and architectural building blocks (entities, value objects, aggregates, commands, queries, events, buses, projections, sagas); library users provide the domain model and storage adapters. No database driver, message broker, or DI container ships with the library — infrastructure contracts are expressed as Python `Protocol` interfaces that users implement against their chosen technology stack.

The type system and serialization backbone is **Pydantic v2**, giving every concept built-in validation, round-trip serialization (`model_dump()` / `model_validate()`), JSON Schema generation, and a type-safe developer experience with full IDE support.

## 1.2 Problem Statement

Teams building domain-rich applications in Python face recurring challenges:

| Challenge | Consequence |
|-----------|-------------|
| **No standard DDD primitives** | Every project re-implements entity identity, value-object equality, aggregate event collection, and optimistic concurrency from scratch. |
| **Tangled read/write paths** | CRUD services mix queries with mutations, making both hard to test, scale, and reason about independently. |
| **Lost domain intent** | State-based persistence overwrites what happened with only the current state, losing audit trail, causality, and the ability to reconstruct past decisions. |
| **Infrastructure coupling** | Domain logic directly imports ORM models, message broker clients, or cache libraries, making it untestable without infrastructure and brittle to technology changes. |
| **Boilerplate wiring** | Command/event routing, unit-of-work lifecycle, correlation/causation tracking, and idempotency are cross-cutting concerns that are solved differently (and often incorrectly) in every project. |

`pydomain` addresses these by providing **tested, composable building blocks** that encode proven patterns so teams can focus on domain logic rather than infrastructure plumbing.

## 1.3 Design Goals

### Primary Quality Goals

| # | Quality Goal | Motivation |
|---|-------------|------------|
| **G1** | **Non-intrusiveness** | Library users' domain models inherit from library base classes (`Entity`, `ValueObject`, `AggregateRoot`), but the library never reaches into user code. No decorators, metaclasses, or magic beyond Pydantic v2. Domain code reads like plain Python. |
| **G2** | **Modularity** | Five modules (`ddd`, `cqrs`, `es`, `infrastructure`, `testing`) with strict dependency rules. Users adopt only what they need — a simple CRUD app uses `ddd` alone; a fully event-sourced system uses all five. Each level adds capability without requiring rewrites. |
| **G3** | **Type safety** | Python 3.12 generics bind return types at the point of use: `Command[TResult]`, `Query[TResult]`, `Repository[TAggregate, TId]`, `Saga[S]`. `dispatch()` returns the declared result type — no casting, no `Any`. |
| **G4** | **Extensibility** | Cross-cutting concerns are expressed as `Protocol` interfaces (repositories, event stores, snapshot stores, message brokers, pipeline behaviors). Library users implement these against any technology — SQLAlchemy, MongoDB, RabbitMQ, Kafka — without modifying library code. |
| **G5** | **Testability** | Every concept is testable in isolation without infrastructure. The `pydomain.testing` module ships complete fakes (`FakeRepository`, `FakeUnitOfWork`, `FakeEventStore`, `FakeSnapshotStore`, etc.). Tests run in milliseconds, not seconds. |

### Secondary Goals

| # | Goal | Approach |
|---|------|----------|
| **G6** | Async-first | All public APIs that perform I/O use `async`/`await`. Tested with `pytest-anyio` + `anyio` for framework-agnostic async. |
| **G7** | Developer ergonomics | Pydantic v2 gives IDE autocompletion, inline validation errors, and JSON Schema out of the box. Typed results eliminate guesswork. |
| **G8** | Operational safety | Optimistic concurrency on aggregates, publish-after-commit event semantics, idempotent command handling via `ProcessedCommandStore`, compensating actions in sagas. |
| **G9** | Minimal dependencies | Only two runtime dependencies: `pydantic >= 2.7` and `uuid-utils >= 0.9`. No transitive dependency trees. |

## 1.4 Adoption Levels

The library supports five levels of commitment. Each level builds on the previous without breaking existing code:

```
Level 1 ─── Tactical DDD         Entity, ValueObject, AggregateRoot, Repository, DomainEvent
   │
Level 2 ─── + CQRS               Command, Query, CommandBus, QueryBus
   │
Level 3 ─── + Message Bus        MessageBus, UnitOfWork, EventHandler, PipelineBehavior
   │
Level 4 ─── + Event Sourcing     EventSourcedAggregateRoot, EventStore, Projection
   │
Level 5 ─── + Advanced ES        SnapshotStore, Upcaster, Subscription, Saga
```

A `ValueObject` at Level 1 is the same class at Level 5. An `AggregateRoot` becomes an `EventSourcedAggregateRoot` by changing the base class — command methods and invariants stay unchanged.

## 1.5 Stakeholders

| Stakeholder | Interest |
|-------------|----------|
| **Library users** | Python developers building domain-driven applications. They want clean, composable primitives that don't fight their domain model. |
| **System architects** | Teams evaluating whether to adopt DDD/CQRS/ES. They need confidence that the library correctly implements the patterns and won't become a maintenance burden. |
| **QA / Test engineers** | Developers writing integration and unit tests. They need fakes that behave like the real thing without infrastructure setup. |
| **Library maintainer** | The author(s) maintaining `pydomain`. They need clear module boundaries, consistent conventions, and a testable codebase. |

## 1.6 Scope

### In Scope

- Tactical DDD primitives: `Entity`, `ValueObject`, `AggregateRoot`, `DomainEvent`, `Specification`, `Factory`, `DomainService`
- CQRS abstractions: `Command`, `Query`, `CommandBus`, `QueryBus`, typed results
- Application services: `MessageBus`, `UnitOfWork`, `PipelineBehavior`, handler protocols
- Event sourcing: `EventSourcedAggregateRoot`, `EventStore`, `Snapshot`, `Projection`, `Upcaster`, `Checkpoint`
- Saga orchestration: `Saga`, `SagaManager`, `SagaState`, `SagaRegistry`, compensating actions
- Infrastructure wiring: `bootstrap()` composition root, `EventRegistry`, `SubscriptionRunner`
- Test doubles: `FakeRepository`, `FakeUnitOfWork`, `FakeEventStore`, and all other fakes in `pydomain.testing`
- Infrastructure contracts: `Protocol` interfaces for `Repository`, `EventStore`, `SnapshotStore`, `MessageBroker`, `LockProvider`, `ProcessedCommandStore`

### Out of Scope

- Concrete persistence adapters (SQLAlchemy, MongoDB, PostgreSQL event stores)
- Concrete message broker adapters (RabbitMQ, Kafka, Redis)
- DI container integration (users wire their own or use `bootstrap()`)
- Web framework integration (FastAPI, Flask, Starlette adapters)
- Deployment tooling, CI/CD pipelines, container images
- Any specific business domain (the library is domain-agnostic)

## 1.7 Success Metrics

| Metric | Target |
|--------|--------|
| Test coverage | ≥ 90% branch coverage across all modules |
| Import boundaries | `ddd/` imports only `pydantic`, `uuid`, `datetime`, and stdlib. `cqrs/` never imports `es/`. `es/` never imports `cqrs/`. |
| Zero runtime dependencies beyond Pydantic | Only `pydantic` and `uuid-utils` at runtime |
| Test doubles completeness | Every `Protocol` interface has a corresponding `Fake*` in `pydomain.testing` |
| API stability | Public base classes and protocols do not change between minor versions |

## 1.8 References

| Reference | Relevance |
|-----------|-----------|
| [Domain-Driven Design — Evans (2003)](https://www.domainlanguage.com/ddd/) | Foundational DDD concepts (entities, value objects, aggregates, repositories) |
| [Patterns of Enterprise Application Architecture — Fowler (2002)](https://martinfowler.com/books/eaa.html) | Unit of Work, Repository, Service Layer patterns |
| [Event Sourcing — Fowler](https://martinfowler.com/eaaDev/EventSourcing.html) | Event sourcing motivation and left-fold projection model |
| [CQRS — Fowler](https://martinfowler.com/bliki/CQRS.html) | CQRS motivation and read/write separation |
| [Pydantic v2 Documentation](https://docs.pydantic.dev/) | Type system, validation, serialization backbone |
| [PEP 544 — Protocols](https://peps.python.org/pep-0544/) | Structural subtyping used for all infrastructure contracts |
| [PEP 561 — Typed Packages](https://peps.python.org/pep-0561/) | `py.typed` marker for PEP 561 type-checking support |
