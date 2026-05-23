# pydomain — DDD, CQRS & Event Sourcing Building Blocks for Python

**pydomain** is a Python 3.12+ library that provides the tactical and architectural building blocks for building systems using Domain-Driven Design (DDD), Command-Query Responsibility Segregation (CQRS), and Event Sourcing (ES) — individually or composed together.

## Design Philosophy

**Opinionated about patterns, unopinionated about infrastructure.**

pydomain gives you the abstractions, base classes, and wiring for DDD/CQRS/ES patterns. You provide the domain model and the storage adapters. The library never locks you into a specific database, message broker, or web framework.

This means:

- **Domain layer has zero infrastructure imports.** Your entities, value objects, and aggregates depend only on `pydantic`, `uuid`, `datetime`, and the standard library.
- **Infrastructure is pluggable.** The `Repository` and `EventStore` contracts are defined as `typing.Protocol` — any class with the right methods conforms. No inheritance required.
- **You adopt incrementally.** Use Level 1 (DDD only) for a rich domain model on top of a traditional ORM. Add CQRS, Event Sourcing, and Sagas as your system grows.

## The Five Adoption Levels

You don't need all five. Start with what you need and add levels as your system grows.

| Level | What You Use | What You Get |
|-------|-------------|--------------|
| **1. Tactical DDD** | `ValueObject`, `Entity`, `AggregateRoot`, `Repository`, `DomainEvent` | Persistence-ignorant domain model with explicit consistency boundaries |
| **2. + CQRS** | Level 1 + `Command[TResult]`, `Query[TResult]`, `CommandBus`, `QueryBus` | Separated read and write paths with typed result abstractions |
| **3. + Message Bus** | Level 2 + `MessageBus`, `UnitOfWork` | Event-driven side effects; inter-aggregate eventual consistency |
| **4. + Event Sourcing** | Level 3 + `EventSourcedAggregateRoot`, `EventStore`, `Projection` | Full audit trail; rebuildable state; multiple read models |
| **5. + Advanced ES** | Level 4 + `SnapshotStore`, `Upcaster`, `SubscriptionRunner` | Production-grade event sourcing with operational maturity |

Moving up a level adds capabilities without rewriting what already works. A `ValueObject` at Level 1 is the same class at Level 5. An `AggregateRoot` becomes an `EventSourcedAggregateRoot` by changing the base class — the command methods and invariants stay the same.

## Built on Pydantic v2

Every domain concept (entities, value objects, events, commands, queries) is a Pydantic `BaseModel`. This gives you:

- **Built-in validation** — field constraints and custom validators express domain invariants directly in the type system.
- **Serialization for free** — `model_dump()` and `model_validate()` give round-trip serialization for event stores, message brokers, and API boundaries.
- **Type safety** — generic base classes like `Entity[TId]`, `Command[TResult]`, and `Repository[T, TId]` give you typed results with no casting.
- **JSON Schema generation** — self-documenting event and command catalogs for cross-team integration contracts.

## Minimal Dependencies

pydomain has only two runtime dependencies:

| Package | Version | Purpose |
|---------|---------|---------|
| `pydantic` | ≥ 2.7 | Type system, validation, serialization |
| `uuid-utils` | ≥ 0.9 | Fast UUIDv7 generation via C extension |

Everything else — your database driver, web framework, message broker — is your choice.

## What's Next?

- **[Install pydomain](getting-started/installation.md)** and verify your setup
- **[Quickstart tutorial](getting-started/quickstart.md)** — build your first aggregate + command in 5 minutes
- **[DDD Concepts](concepts/ddd/)** — understand the tactical building blocks
