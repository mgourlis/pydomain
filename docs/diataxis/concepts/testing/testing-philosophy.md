# Testing Philosophy

> **Adoption Level:** All levels
> **Module:** `pydomain.testing`

## Why Testing Matters in DDD

Domain-Driven Design places business logic at the center of the system. This logic must be **correct** — it encodes the rules that the business depends on. pydomain is designed to make domain logic **easy to test in isolation**, without infrastructure dependencies.

## Core Principles

### 1. Test Domain Logic Directly

DDD domain logic lives in entities, value objects, and aggregates. These are plain Python objects with no infrastructure dependencies. Test them directly:

```python
def test_money_add_same_currency():
    a = Money(amount=100, currency="EUR")
    b = Money(amount=50, currency="EUR")
    result = a.add(b)
    assert result == Money(amount=150, currency="EUR")
```

No mocks. No database. No HTTP server. Just domain objects.

### 2. Fakes Over Mocks

pydomain ships with **fake implementations** of every infrastructure protocol:

| Fake | Replaces |
|------|----------|
| `FakeRepository` | `Repository[T, TId]` |
| `FakeUnitOfWork` | `AbstractUnitOfWork` |
| `FakeEventStore` | `EventStore` |
| `FakeSnapshotStore` | `SnapshotStore` |
| `FakeSagaRepository` | Saga repository |
| `FakeCheckpointStore` | `CheckpointStore` |
| `FakeLockProvider` | `LockProvider` |
| `FakeProcessedCommandStore` | `ProcessedCommandStore` |

**Never mock what you don't own.** Instead, use the library-provided fakes. They implement the full protocol contract and behave like the real infrastructure — just in memory.

### 3. Domain Tests Have No I/O

Pure domain tests (entity behavior, value object operations, aggregate invariants) should:

- Have **no I/O** — no file reads, no database calls, no network requests
- Run **fast** — thousands per second
- Be **deterministic** — same inputs always produce the same results

### 4. Application Tests Use Fakes

Handler and use-case tests replace infrastructure with fakes:

```python
async def test_place_order_handler():
    repo = FakeRepository()
    uow = FakeUnitOfWork(repository=repo)

    handler = PlaceOrderHandler()
    result = await handler.handle(PlaceOrder(...), uow)

    assert result.order_id is not None
    saved = await repo.get_by_id(result.order_id)
    assert saved.status == "placed"
```

This tests the **full handler flow** without a database.

## Test Categories

| Category | What to Test | How |
|----------|-------------|-----|
| **Domain unit tests** | Entity behavior, value object operations, aggregate invariants, specifications | Direct instantiation, no I/O |
| **Application tests** | Command handlers, query handlers, event handlers | `FakeRepository`, `FakeUnitOfWork` |
| **Integration tests** | Repository implementations, event store, message broker | Real infrastructure (database, broker) |

## What NOT to Do

- **Don't mock domain objects.** Test them directly.
- **Don't mock infrastructure protocols.** Use the provided fakes.
- **Don't test Pydantic.** Pydantic's validation is already tested. Test your domain rules.
- **Don't skip invariant tests.** Aggregates must enforce invariants after every mutation — test the error cases too.

## Next Steps

- **[Test Structure →](test-structure.md)** — how to organize your test files
- **[Fake Repository how-to →](../../how-to/testing/use-fake-repository.md)** — using fakes in handler tests
