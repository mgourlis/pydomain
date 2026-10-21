# Test Structure

> **Adoption Level:** All levels
> **Prerequisite:** [Testing Philosophy](testing-philosophy.md)

## Test Organization

pydomain tests are organized by concern, not by module. Each test file corresponds to a specific area of the system:

```
tests/
├── conftest.py              # Shared fixtures, fakes
├── test_domain.py           # Pure domain unit tests (no I/O)
├── test_buses.py            # Command/Query/Message bus tests
├── test_application.py      # Handler tests with FakeRepository/UoW
└── fakes.py                 # Custom fakes (if needed)
```

## Test File Guide

### `test_domain.py` — Pure Domain Tests

Test domain objects directly. No infrastructure, no I/O.

```python
import pytest


class TestMoney:
    def test_add_same_currency(self):
        a = Money(amount=100, currency="EUR")
        b = Money(amount=50, currency="EUR")
        assert a.add(b) == Money(amount=150, currency="EUR")

    def test_add_different_currency_raises(self):
        a = Money(amount=100, currency="EUR")
        b = Money(amount=50, currency="USD")
        with pytest.raises(ValueError):
            a.add(b)


class TestOrder:
    def test_place_records_event(self):
        order = Order(customer_id=uuid4(), total=Money(amount=100, currency="EUR"))
        order.place()
        events = order.pull_events()
        assert len(events) == 1
        assert isinstance(events[0], OrderPlaced)

    def test_place_twice_raises(self):
        order = Order(customer_id=uuid4(), total=Money(amount=100, currency="EUR"))
        order.place()
        with pytest.raises(ValueError):
            order.place()
```

### `test_buses.py` — Bus Registration and Dispatch

Test command, query, and message bus wiring:

```python
import pytest


class TestCommandBus:
    async def test_dispatch_routes_to_handler(self):
        bus = CommandBus()
        bus.register(PlaceOrder, place_order_handler)
        result = await bus.dispatch(PlaceOrder(...))
        assert result is not None

    async def test_dispatch_raises_on_missing_handler(self):
        bus = CommandBus()
        with pytest.raises(CommandExecutionError):
            await bus.dispatch(PlaceOrder(...))
```

### `test_application.py` — Handler Tests with Fakes

Test command/query handlers using fakes:

```python
import pytest


async def test_place_order_handler():
    repo = FakeRepository()
    uow = FakeUnitOfWork(repository=repo)

    handler = PlaceOrderHandler()
    result = await handler.handle(PlaceOrder(customer_id=..., items=...), uow)

    assert result.order_id is not None
    saved = await repo.get_by_id(result.order_id)
    assert saved is not None
    assert saved.status == "placed"
```

## Async Testing

pydomain is async throughout. Use `pytest-anyio` with `anyio` for async tests:

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

```python
# Async tests just use async def
async def test_async_handler():
    result = await handler.handle(command, uow)
    assert result is not None
```

## Fixtures and Shared State

Shared fixtures live in `conftest.py`:

```python
# tests/conftest.py
import pytest
from pydomain.testing.fake_repository import FakeRepository


@pytest.fixture
def order_repo() -> FakeRepository:
    return FakeRepository()
```

Keep fixtures focused — each test should be understandable in isolation. Avoid fixtures that set up complex shared state.

## Fakes

The library ships with ready-to-use fakes. Use them instead of mocks:

```python
from pydomain.testing.fake_repository import FakeRepository
from pydomain.testing.fake_unit_of_work import FakeUnitOfWork
```

If you need a custom fake, put it in `tests/fakes.py` — not in `conftest.py`.

## What Goes Where

| Test type | File | I/O? | Uses fakes? |
|-----------|------|------|-------------|
| Entity / VO behavior | `test_domain.py` | No | No |
| Aggregate invariants | `test_domain.py` | No | No |
| Specification logic | `test_domain.py` | No | No |
| Bus registration | `test_buses.py` | No | No |
| Command handlers | `test_application.py` | No | Yes |
| Query handlers | `test_application.py` | No | Yes |
| Event handlers | `test_application.py` | No | Yes |
| Repository implementations | Integration test | Yes | No |

## Runnable Docs Examples

This section is for contributors to pydomain's own documentation. The runner lives in this repository's
`tests/docs/` and is not part of the `pydomain` package.

The Python examples of a Diataxis page can run as tests. `tests/docs/test_diataxis_examples.py` finds every page under `docs/diataxis/` marked runnable and runs its fenced `python` blocks from top to bottom, so `make test` fails when a page stops matching the library.

A page opts in with a marker near the top. Markers are HTML comments, so they do not show when the page is rendered:

````markdown
# How to Create a Value Object

<!-- docs-test: runnable -->

<!-- docs-test: setup
from uuid import uuid4
-->

```python
from pydomain.ddd.value_object import ValueObject
```

<!-- docs-test: skip -->
```python
app = bootstrap(...)  # a fragment that cannot run on its own
```
````

| Marker | Effect |
|--------|--------|
| `<!-- docs-test: runnable -->` | The page's `python` blocks run as one test |
| `<!-- docs-test: skip -->` | The next fenced block does not run |
| `<!-- docs-test: setup` … `-->` | Hidden code that runs before the first block, e.g. imports the page leaves out |

How the blocks run:

- **One namespace per page.** A later block sees the names an earlier one defined; pages never share names.
- **Top-level `await`.** A block may use `await` outside a function. All blocks of a page share one event loop.
- **Only `python` fences run.** `bash`, `toml` and other fences are ignored, as are markers and blocks shown inside another fence.
- **Failures name the line.** A failing block reports the page path and the line of its opening fence, and the traceback points at the Markdown line.

Mark a page runnable only when every block runs as written. A page that shows fragments can mark them `skip` or supply the missing context in a setup comment. Examples that need third-party packages, such as a broker client, stay unmarked.

## Next Steps

- **[Testing Philosophy →](testing-philosophy.md)** — why we test this way
- **[Fake Repository how-to →](../../how-to/testing/use-fake-repository.md)** — using fakes in practice
