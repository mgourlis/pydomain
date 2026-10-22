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
        await uow.orders.save(order)

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
    # save() persists the change and drains the aggregate's pending events
    # into the repository, where the unit of work collects them at commit.
    await uow.orders.save(order)
    return AddItemResult(order_id=order.id, item_count=len(order.items))
```

**Never** call `uow.commit()` or `uow.rollback()` — the Command Bus manages the lifecycle. The handler runs inside the bus's `async with uow:` block: `save()` persists the aggregate and drains its pending events into the repository's buffer; after the handler returns, the bus explicitly calls `uow.commit()`, which triggers `_flush()` → `_collect_and_stamp()` → `_write_outbox()` → `_commit()`. (`__aexit__` never commits: it rolls back whenever the scope did not commit, and a second `commit()` raises `UnitOfWorkAlreadyCommittedError`.)

## Deleting an Aggregate

Deletion follows the same load-mutate-save-delete pattern. The handler loads the aggregate, calls the domain delete method (which records the event), calls `save()` so the repository drains that event into its buffer, and only then calls `delete()` to remove the aggregate from storage. `delete()` takes only an ID, so it never sees the aggregate's events; without the `save()`, the `OrderDeleted` event is lost:

```python
class DeleteOrderHandler:
    async def __call__(
        self, cmd: DeleteOrder, uow: OrderUoW
    ) -> DeleteOrderResult:
        # 1. Load
        order = await uow.orders.get_by_id(cmd.order_id)
        if order is None:
            raise OrderNotFoundError(cmd.order_id)

        # 2. Domain logic — records OrderDeleted via _add_event()
        order.delete(reason=cmd.reason)

        # 3. Drain the event into the repository's buffer
        await uow.orders.save(order)

        # 4. Remove from storage
        await uow.orders.delete(order.id)

        return DeleteOrderResult(
            order_id=order.id,
            deleted_at=datetime.now(UTC),
            reason=cmd.reason,
        )
```

The order is removed from storage, but its `OrderDeleted` event is already in the repository's buffer, because `save()` drained it. During `uow.commit()` → `_collect_and_stamp()`, the unit of work calls the repository's `pull_events()` and stamps the event.

**Call `save()` before `delete()`.** The `Repository` protocol's `delete()` receives an ID, not the aggregate, so it cannot collect events. Calling `save()` first puts the event in the repository's buffer, whatever tracking the implementation uses.

## Domain Events

The handler doesn't publish events directly. It calls aggregate methods, which record events via `self._add_event()`. The unit of work collects and stamps them at commit, and the `MessageBus` publishes them after it:

```python
async def __call__(self, cmd: PlaceOrder, uow: OrderUoW) -> PlaceOrderResult:
    customer = await uow.customers.get_by_id(cmd.customer_id)
    order = customer.place_order(...)  # Internally calls self._add_event(OrderPlaced(...))
    await uow.orders.save(order)

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
