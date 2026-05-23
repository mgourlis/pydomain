# Unit of Work

> **Adoption Level:** 2 — CQRS
> **Module:** `pydomain.cqrs.unit_of_work`

## What is the Unit of Work?

The **Unit of Work (UoW)** is a transactional boundary that groups multiple aggregate changes into a single atomic operation. It tracks which aggregates were modified and collects their domain events for publishing after a successful commit.

## Why It Exists

Without a UoW, each aggregate change would be persisted independently. If one succeeded and another failed, you'd have partial updates — broken invariants.

The UoW solves this by deferring persistence until `commit()` is called. All changes succeed or none do.

## The `UnitOfWork` Protocol

```python
from pydomain.cqrs.unit_of_work import UnitOfWork


class UnitOfWork(Protocol):
    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(self, ...) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    def collect_events(self) -> list[DomainEvent]: ...
```

The protocol defines the contract. `AbstractUnitOfWork` provides the implementation.

## `AbstractUnitOfWork`

The ABC provides the full lifecycle:

```python
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork


class AbstractUnitOfWork(ABC, UnitOfWork):
    def __init__(self):
        self._committed = False
        self._repos: dict[str, Any] = {}
        self._events: list[DomainEvent] = []
```

Subclasses override extension hooks to integrate with a concrete database.

## Commit Lifecycle

When `commit()` is called:

```
1. _flush()              — push pending changes to storage
2. _collect_and_stamp()  — pull events from repos, stamp tracing IDs
3. _write_outbox()       — persist events to outbox (optional)
4. _commit()             — commit the database transaction
5. Mark as committed
```

All five steps happen atomically within the storage transaction.

## Extension Hooks

| Hook | Default | Override To |
|------|---------|------------|
| `_flush()` | no-op | Flush ORM session to storage |
| `_write_outbox()` | no-op | Write stamped events to outbox table |
| `_commit()` | no-op | Commit database transaction |

## Concrete UoW Example

```python
class OrderUoW(AbstractUnitOfWork):
    orders: OrderRepository
    customers: CustomerRepository

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        super().__init__()
        self._session_factory = session_factory
        self._session: Session | None = None

    async def __aenter__(self) -> OrderUoW:
        self._session = self._session_factory()
        self.orders = OrderRepository(self._session)
        self.customers = CustomerRepository(self._session)
        self._repos = {"orders": self.orders, "customers": self.customers}
        return await super().__aenter__()

    async def _flush(self) -> None:
        await self._session.flush()

    async def _commit(self) -> None:
        await self._session.commit()
```

Handlers access repositories through typed UoW attributes:

```python
async def __call__(self, cmd: PlaceOrder, uow: OrderUoW) -> PlaceOrderResult:
    customer = await uow.customers.get_by_id(cmd.customer_id)
    order = Order.create(customer, cmd.items)
    await uow.orders.add(order)
    return PlaceOrderResult(order_id=order.id, status="placed")
```

## Event Collection and Stamping

During `_collect_and_stamp()`, the UoW pulls pending events from every registered repository and stamps them with tracing IDs:

```python
for repo in self._repos.values():
    for event in repo.pull_events():
        stamped = event.stamp(
            correlation_id=self._correlation_id,
            causation_id=self._causation_id,
        )
        self._events.append(stamped)
```

The `correlation_id` and `causation_id` are set by the [Command Bus](command-bus.md) before `commit()`.

## Rollback

`__aexit__` does **not** auto-commit. It only auto-rollbacks on unhandled exceptions:

```python
async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
    if exc_type is not None and not self._committed:
        await self.rollback()
```

`commit()` must always be called explicitly. The [Command Bus](command-bus.md) does this — it calls `uow.commit()` on success and `uow.rollback()` on failure. If you use a UoW without the bus (e.g., in a DDD-only app), you must call `commit()` yourself.

## Publish-After-Commit

Events are only collected after the commit succeeds. This guarantees that event handlers never see events from uncommitted transactions. The [Command Bus](command-bus.md) calls `collect_events()` after `commit()` and returns the events alongside the result.

## Next Steps

- **[Configure the Command Bus →](../../how-to/cqrs/configure-command-bus.md)** — wiring UoW factories
- **[Command Bus →](command-bus.md)** — how the bus uses UoW
- **[Fake Unit of Work →](../../how-to/testing/use-fake-uow.md)** — testing with FakeUoW
- **[Integration Events →](integration-events.md)** — cross-boundary events
