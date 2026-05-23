# How to Implement a Command Handler

> **Prerequisites:** [Handlers concept](../../concepts/cqrs/handlers.md), [Unit of Work concept](../../concepts/cqrs/unit-of-work.md)

## Problem

You need to implement the business logic that executes when a command is dispatched — loading aggregates, enforcing invariants, and returning a result.

## Solution

Create a class with an `async __call__(self, command, uow) -> TResult` signature. Inject dependencies via `__init__`. Register it with the Command Bus.

## Steps

### 1. Create the handler class

```python
from pydomain.cqrs.handlers import CommandHandler
from pydomain.cqrs.unit_of_work import UnitOfWork


class PlaceOrderHandler:
    def __init__(self, pricing_service: PricingService) -> None:
        self._pricing = pricing_service

    async def __call__(
        self, cmd: PlaceOrder, uow: OrderUoW
    ) -> PlaceOrderResult:
        # 1. Load the aggregate
        customer = await uow.customers.get_by_id(cmd.customer_id)

        # 2. Perform domain logic
        order = customer.place_order(
            order_id=cmd.order_id,
            items=cmd.items,
            pricing=self._pricing,
        )

        # 3. Persist
        await uow.orders.add(order)

        # 4. Return result
        return PlaceOrderResult(
            order_id=order.id,
            status=order.status,
            placed_at=datetime.now(UTC),
        )
```

### 2. Inject dependencies

Dependencies come through `__init__` — never through a service locator:

```python
class CancelOrderHandler:
    def __init__(
        self,
        refund_service: RefundService,
        notification_service: NotificationService,
    ) -> None:
        self._refund = refund_service
        self._notification = notification_service

    async def __call__(
        self, cmd: CancelOrder, uow: OrderUoW
    ) -> CancelOrderResult:
        order = await uow.orders.get_by_id(cmd.order_id)
        refund_amount = order.cancel()
        await self._refund.issue(order.customer_id, refund_amount)
        return CancelOrderResult(
            order_id=order.id,
            cancelled_at=datetime.now(UTC),
            refund_amount=refund_amount,
        )
```

### 3. Register with the Command Bus

```python
from pydomain.cqrs.command_bus import CommandBus

bus = CommandBus()

bus.register(
    command_type=PlaceOrder,
    handler=PlaceOrderHandler(pricing_service),
    uow_factory=lambda: OrderUoW(session_factory()),
)
```

## Using the Unit of Work

The handler receives the UoW as its second parameter. It uses the UoW's typed repository attributes to load and persist aggregates:

```python
async def __call__(self, cmd: AddItem, uow: OrderUoW) -> AddItemResult:
    # uow.orders is typed — OrderRepository
    order = await uow.orders.get_by_id(cmd.order_id)
    order.add_item(cmd.product_id, cmd.quantity)
    # No save() needed — get_by_id() already registered the aggregate
    # in the repo's tracking set; _flush() will persist the changes.
    return AddItemResult(order_id=order.id, item_count=len(order.items))
```

**Never** call `uow.commit()` or `uow.rollback()` — the Command Bus manages the lifecycle. The handler runs inside the bus's `async with uow:` block: `save()` and `get_by_id()` register aggregates in the repo's tracking set; after the handler returns, the bus explicitly calls `uow.commit()`, which triggers `_flush()` → `_collect_and_stamp()` → `_write_outbox()` → `_commit()`. (`__aexit__` does NOT auto-commit — it only auto-rollbacks on unhandled exceptions.)

## Deleting an Aggregate

Deletion follows the same load-mutate-delete pattern. The handler loads the aggregate (which tracks it in `_seen`), calls the domain delete method (which records the event), then calls `delete()` on the repo (which removes it from storage but leaves it in `_seen` for event collection):

```python
class DeleteOrderHandler:
    async def __call__(
        self, cmd: DeleteOrder, uow: OrderUoW
    ) -> DeleteOrderResult:
        # 1. Load — tracks aggregate in repo._seen
        order = await uow.orders.get_by_id(cmd.order_id)
        if order is None:
            raise OrderNotFoundError(cmd.order_id)

        # 2. Domain logic — records OrderDeleted via _add_event()
        order.delete(reason=cmd.reason)

        # 3. Remove from store — aggregate stays in _seen for event pull
        await uow.orders.delete(order.id)

        return DeleteOrderResult(
            order_id=order.id,
            deleted_at=datetime.now(UTC),
            reason=cmd.reason,
        )
```

The order is removed from storage but still referenced in the repo's tracking set. During `uow.commit()` → `_collect_and_stamp()` → `pull_events()`, the repo iterates its tracking set, calls `order.pull_events()`, and collects the `OrderDeleted` event. After draining, the tracking set is cleared and the order can be garbage collected.

**No `save()` before `delete()` needed** — `get_by_id()` already did the tracking. But your repo's `delete()` should add the aggregate to the tracking set as a safety measure, so events are collected even if `get_by_id()` wasn't called first:

```python
async def delete(self, id_: UUID) -> None:
    aggregate = self._store.pop(id_, None)
    if aggregate is not None:
        self._seen.append(aggregate)  # Track for event collection
```

## Domain Events

The handler doesn't publish events directly. It calls aggregate methods, which record events via `self._add_event()`. The UoW collects and publishes them after commit:

```python
async def __call__(self, cmd: PlaceOrder, uow: OrderUoW) -> PlaceOrderResult:
    customer = await uow.customers.get_by_id(cmd.customer_id)
    order = customer.place_order(...)  # Internally calls self._add_event(OrderPlaced(...))
    await uow.orders.add(order)

    # After bus.commit(): OrderPlaced event is stamped and collected
    return PlaceOrderResult(...)
```

## Error Handling

Let domain errors propagate. The Command Bus wraps them in `CommandExecutionError`:

```python
async def __call__(self, cmd: CancelOrder, uow: OrderUoW) -> CancelOrderResult:
    order = await uow.orders.get_by_id(cmd.order_id)
    order.cancel()  # May raise OrderNotCancellable(DomainError)
    return CancelOrderResult(...)
```

Don't catch domain errors in the handler — let them propagate to the bus, which rolls back the UoW and wraps the exception.

## See Also

- [Handlers concept](../../concepts/cqrs/handlers.md)
- [Unit of Work concept](../../concepts/cqrs/unit-of-work.md)
- [Configure the Command Bus](configure-command-bus.md)
- [Implement a Query Handler](implement-query-handler.md)
