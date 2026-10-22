# Testing Philosophy

> **Adoption Level:** All levels
> **Module:** `pydomain.testing`, `pydomain.testing.contracts`

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

pydomain ships an in-memory **fake** for every storage and messaging Protocol, all exported from `pydomain.testing`:

| Fake | Replaces |
|------|----------|
| `FakeRepository` | `Repository[T, TId]` |
| `FakeUnitOfWork` | `UnitOfWork` (a subclass of `AbstractUnitOfWork`) |
| `FakeEventStore` | `EventStore` |
| `FakeSnapshotStore` | `SnapshotStore` |
| `FakeCheckpointStore` | `CheckpointStore` |
| `FakeOutboxStore` | `OutboxStore` |
| `FakeProcessedMessageStore` | `ProcessedMessageStore` |
| `FakeLockProvider` | `LockProvider` |
| `FakeSagaRepository` | `SagaRepository` |
| `InMemoryMessageBroker` | `MessageBroker` |
| `InMemoryMessageSubscriber` | `MessageSubscriber` |
| `InMemoryProjectionStore` | `ProjectionStore` |

**Never mock what you don't own.** A mock returns what the test tells it to; a fake keeps state and enforces the rules the Protocol states, such as the version check on `save()`. The fakes assume one event loop on one thread.

### 3. Fakes Are Held to Their Protocol

A test that passes against a fake proves something only if the fake keeps the same promises as the real adapter. `pydomain.testing.contracts` states those promises as tests: one `*Contract` suite per Protocol, each case taken from the Protocol's docstring or its ADR ([ADR-067](../../../adr/ADR-067-protocol-contract-suites.md)).

pydomain runs every suite against its own fakes. You run the same suites against your own adapters, such as a SQLAlchemy `Repository` or a Postgres `OutboxStore`, so your fakes and your production adapters face the same cases. See [Run a Contract Suite Against Your Adapter](../../how-to/testing/run-contract-suites.md).

A fake that is looser or stricter than its Protocol is a defect, fixed like any other ([ADR-072](../../../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md)). The 0.3.0 audit found `FakeEventStore` refusing the retry ADR-041 prescribes after a `ConcurrencyError`, and reading from the end of a stream for a negative offset. Each such fix adds the missing promise to the Protocol's suite, so your adapters face it too. Helpers that only a fake has, such as `InMemoryMessageSubscriber.simulate_message()`, keep the promises of their own docstrings: it raises `KeyError` for a topic with no handler.

### 4. Domain Tests Have No I/O

Pure domain tests (entity behavior, value object operations, aggregate invariants) should:

- Have **no I/O** — no file reads, no database calls, no network requests
- Run **fast** — thousands per second
- Be **deterministic** — same inputs always produce the same results

### 5. Application Tests Use Fakes

Handler and use-case tests replace infrastructure with fakes. A command handler is an async callable that takes the command and the unit of work:

```python
@pytest.mark.anyio
async def test_place_order_saves_a_placed_order():
    repo = FakeRepository[Order, UUID]()
    uow = FakeUnitOfWork(repository=repo)
    handler = PlaceOrderHandler(repo)

    result = await handler(PlaceOrder(customer_id=uuid4()), uow)

    saved = await repo.get_by_id(result.order_id)
    assert saved is not None
    assert saved.status == "placed"
```

This tests the **full handler flow** without a database.

### 6. Coverage Is Not Proof

A correctness audit of pydomain 0.3.0 confirmed, fully or in part, 108 defects in a suite with 96.65 % branch coverage. Every one sat on covered lines. The defects lived in ordering, failure paths, cancellation, copy semantics, and fakes looser than their Protocols. A line that runs is not a promise that holds.

So pydomain's tests check promises, not lines. They assert what a Protocol, an ADR or a doc page says, through the public API, never through private attributes. Contract suites keep the fakes honest, and runnable docs examples keep the documentation honest. The test suite also has helpers to inject failures and cancellations at an `await`, to check the order of side effects across collaborators, and to run property tests reproducibly. Many tests across the suite use them.

## How pydomain Itself Is Changed: Test-First

Every change to pydomain that alters behaviour is test-first and ships with its documentation in the same pull request ([ADR-066](../../../adr/ADR-066-test-first-fixes-with-documentation.md)). The cycle is red, green, refactor, harden:

| Step | What happens |
|------|--------------|
| **Specify** | The issue lists the acceptance criteria as test names, each naming the contract it checks: an ADR, a docstring or a doc page |
| **Red** | The tests are committed first, each marked `@pytest.mark.xfail(strict=True, raises=<expected failure>, reason="DCE-NN …")`. They are run locally to see each one fail, then pushed together with the green commit |
| **Green** | The smallest change that makes the tests pass; the markers are removed |
| **Refactor** | Clean up with every test green, without changing behaviour |
| **Harden** | Mutation testing (`make mutate`) on the changed modules; each surviving mutant is killed by a new test or justified in the PR |

The red step is what makes the tests trustworthy. A test written after a fix tends to assert what the code does rather than what the contract says. A red commit, run locally and again by the reviewer, shows that each new test fails before the fix. `strict=True` turns an unexpected pass into a failure. `raises=` makes the test fail for the intended reason, not because of an import error or a typo. A plain `xfail` or a skipped test proves neither.

Two more rules follow. An existing test is never weakened to let a change pass; a test that blessed a defect is changed in the same PR, which says why. And a fix includes every test layer that applies: a regression example, a property or stateful test, a contract-suite case, failure and cancellation injection, a content sentinel, a typing test, a docs example.

You can follow the same cycle in your own project. Red and green need only pytest; hardening needs a mutation tester such as mutmut.

## Test Categories

| Category | What to Test | How |
|----------|-------------|-----|
| **Domain unit tests** | Entity behavior, value object operations, aggregate invariants, specifications | Direct instantiation, no I/O |
| **Application tests** | Command handlers, query handlers, event handlers | `FakeRepository`, `FakeUnitOfWork` and the other fakes |
| **Contract tests** | Your own adapters against the Protocol they implement | A `*Contract` suite from `pydomain.testing.contracts` |
| **Integration tests** | Repository implementations, event store, message broker | Real infrastructure (database, broker) |

## What NOT to Do

- **Don't mock domain objects.** Test them directly.
- **Don't mock infrastructure protocols.** Use the provided fakes.
- **Don't assert private attributes.** Observe behaviour through the public API.
- **Don't test Pydantic.** Pydantic's validation is already tested. Test your domain rules.
- **Don't skip invariant tests.** Aggregates must enforce invariants after every mutation — test the error cases too.

## Next Steps

- **[Test Structure →](test-structure.md)** — how pydomain's own tests are organized, and how to organize yours
- **[Run a Contract Suite →](../../how-to/testing/run-contract-suites.md)** — test your adapter against its Protocol
- **[Fake Repository how-to →](../../how-to/testing/use-fake-repository.md)** — using fakes in handler tests
