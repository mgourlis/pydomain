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

## Installation

```bash
pip install pydomain
```

Requires Python 3.12+. Pulls in `pydantic>=2.7` and `uuid-utils>=0.9` automatically.

For development tooling (pytest, ruff, mypy, pre-commit):

```bash
pip install "pydomain[dev]"
```

## Quick Start — Your First Aggregate in 5 Minutes

```python
from uuid import UUID
from pydomain.ddd.value_object import ValueObject
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.testing.fake_repository import FakeRepository


# 1. Define a Value Object (immutable, defined by its attributes)
class Money(ValueObject):
    amount: int
    currency: str

    def add(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError("Cannot add different currencies")
        return self.model_copy(update={"amount": self.amount + other.amount})


# 2. Define a Domain Event (past-tense fact)
class OrderPlaced(DomainEvent):
    order_id: UUID
    total_amount: int
    currency: str


# 3. Define an Aggregate Root (consistency boundary, owns events)
class Order(AggregateRoot[UUID]):
    customer_id: UUID
    total: Money
    status: str = "pending"

    def place(self) -> None:
        if self.status != "pending":
            raise ValueError("Order is not pending")
        self.status = "placed"
        self._add_event(OrderPlaced(
            order_id=self.id,
            total_amount=self.total.amount,
            currency=self.total.currency,
        ))


# 4. Create and use it — id is auto-generated (UUIDv7)
order = Order(
    customer_id=UUID("550e8400-e29b-41d4-a716-446655440000"),
    total=Money(amount=1000, currency="EUR"),
)

order.place()                                 # records OrderPlaced event
events = order.pull_events()                  # collect recorded events
print(f"Status: {order.status}")              # "placed"


# 5. Test with fakes — no database needed
async def main():
    repo: FakeRepository[Order, UUID] = FakeRepository()
    await repo.save(order)
    found = await repo.get_by_id(order.id)
    assert found is not None and found.status == "placed"
```

## Documentation

| Section | Mode | Content |
|---------|------|---------|
| [Getting Started](docs/diataxis/getting-started/) | Tutorial | Installation and quickstart walkthrough |
| [Concepts](docs/diataxis/concepts/) | Explanation | The *why* behind DDD, CQRS, ES, Sagas, and infrastructure patterns |
| [How-To Guides](docs/diataxis/how-to/) | Task-oriented | Step-by-step recipes for defining aggregates, configuring buses, implementing projections, and more |
| [Recipes](docs/diataxis/how-to/recipes/) | Integration | End-to-end patterns combining multiple modules (DDD-only, CQRS+ES, Saga orchestration) |
| [API Reference](docs/diataxis/api-reference/) | Reference | Auto-generated module and class index |

## Development

```bash
git clone https://github.com/mgourlis/pydomain.git
cd pydomain
uv sync --extra dev
```

| Command | Purpose |
|---------|---------|
| `make test` | Run tests with coverage |
| `make lint` | Lint with ruff |
| `make format` | Format with ruff |
| `make type` | Static type checking with mypy |
| `make check` | Run all checks (lint + type + test) |

## References

- [Domain-Driven Design — Evans (2003)](https://www.domainlanguage.com/ddd/)
- [Patterns of Enterprise Application Architecture — Fowler (2002)](https://martinfowler.com/books/eaa.html)
- [Event Sourcing — Fowler](https://martinfowler.com/eaaDev/EventSourcing.html)
- [CQRS — Fowler](https://martinfowler.com/bliki/CQRS.html)
- [Diátaxis Documentation Framework](https://diataxis.fr/)

## License

MIT License — see [LICENSE](LICENSE) file for details.
