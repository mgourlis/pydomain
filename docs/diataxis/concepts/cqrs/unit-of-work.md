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
    def __init__(self, *, outbox_writer: OutboxWriter | None = None):
        self._repos: dict[str, Any] = {}
        ...
```

Subclasses fill `_repos` with their repositories and override extension hooks to integrate with a concrete database.

## Commit Lifecycle

When `commit()` is called:

```
1. _flush()              — push pending changes to storage
2. _collect_and_stamp()  — pull events from repos, stamp tracing IDs
3. _write_outbox()       — persist integration events to the outbox (optional)
4. _commit()             — commit the database transaction
5. Mark as committed
```

All five steps happen atomically within the storage transaction.

A scope commits **once**. A second `commit()` in the same scope raises `UnitOfWorkAlreadyCommittedError` before any hook runs, so nothing is flushed, written to the outbox or committed twice ([ADR-068](../../../adr/ADR-068-rollback-drains-event-buffers.md)):

- **After a successful commit**, the first commit's changes stay persisted and the scope stays committed, so leaving it does not roll back. This is what a handler that calls `commit()` itself gets inside a dispatch.
- **After a commit that failed part-way**, a retry raises too, because the hooks that already ran would run again. Call `rollback()`, or enter the unit of work again with `async with`, to start a new scope, and run the work again in it: the rollback has drained the staged events.

`rollback()` after a successful commit starts a new scope as well, so a later `commit()` is allowed; it does not undo the committed transaction.

## Extension Hooks

| Hook | Default | Override To |
|------|---------|------------|
| `_flush()` | no-op | Flush ORM session to storage |
| `_write_outbox()` | no-op | Persist integration events via an [`OutboxWriter`](../infrastructure/outbound-event-gateway.md) — built in when one is supplied |
| `_commit()` | no-op | Commit database transaction |

> **⚠️** `_write_outbox()` is a no-op unless the UoW was constructed with an `OutboxWriter` (`super().__init__(outbox_writer=...)`). Without one, nothing is written and anything relying on the outbox finds it empty. Override the hook to customise; see [How to configure the outbound event gateway](../../how-to/infrastructure/configure-outbound-event-gateway.md).

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

    async def rollback(self) -> None:
        await self._session.rollback()
        await super().rollback()  # drains the repositories' event buffers
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

`__aexit__` never commits. It rolls back whenever the scope has not committed, whether the body raised or simply ended without `commit()`:

```python
async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
    if not self._committed:
        await self.rollback()
```

`rollback()` discards everything the scope staged:

- It drains every repository's event buffer through `pull_events()` and throws the events away. A repository that outlives one unit of work (a shared `FakeRepository`, or one `EventSourcedRepository` used by every command) therefore never hands a refused command's events to the next commit.
- It clears the collected events and starts a new scope.
- Calling it again changes nothing. A subclass that overrides it to roll back its session should keep that property and call `await super().rollback()`.

If the body raised and `rollback()` raises as well, `__aexit__` keeps Python's default for a clean-up that fails: the rollback's exception propagates, and the body's exception is its `__context__`. Code that uses a unit of work directly sees the rollback's exception and finds the original failure there. The [Command Bus](command-bus.md) wraps both: its `CommandExecutionError` has the original failure as `__cause__` and the rollback's exception as `__context__`.

`commit()` must always be called explicitly. The [Command Bus](command-bus.md) does this: it calls `uow.commit()` on success and lets `__aexit__` roll back on failure, once. If you use a UoW without the bus (e.g., in a DDD-only app), you must call `commit()` yourself.

## Publish-After-Commit

Events are only collected after the commit succeeds, and a rollback discards the ones a failed scope staged. This guarantees that event handlers never see events from uncommitted transactions ([ADR-005](../../../adr/ADR-005-publish-after-commit.md)). The [Command Bus](command-bus.md) calls `collect_events()` after `commit()` and returns the events alongside the result. `collect_events()` returns a new list on each call, so the list a caller holds does not change when a later scope runs on the same unit of work.

## Next Steps

- **[Configure the Command Bus →](../../how-to/cqrs/configure-command-bus.md)** — wiring UoW factories
- **[Command Bus →](command-bus.md)** — how the bus uses UoW
- **[Fake Unit of Work →](../../how-to/testing/use-fake-uow.md)** — testing with FakeUoW
- **[Integration Events →](integration-events.md)** — cross-boundary events
