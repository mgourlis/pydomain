# Recipe: CQRS with DDD

> **Adoption Level:** 2–3 — CQRS
> **Prerequisites:** All Phase 1, Phase 2, phase 3 concepts and how-tos

This recipe shows how to layer CQRS on top of a DDD domain model — commands, queries, handlers, buses, Unit of Work, and pipeline behaviors — to build a complete write/read-separated application.

## What You'll Build

An **Order Management** application with:

- Domain layer (from the [DDD-only recipe](ddd-only-app.md)): `Order` aggregate, `Money` VO, domain events
- Command side: `PlaceOrder`, `CancelOrder` commands with typed results
- Query side: `GetOrder` query with typed result
- Handlers: `PlaceOrderHandler`, `CancelOrderHandler`, `GetOrderHandler`
- Bus: `MessageBus` wrapping command dispatch (with UoW), query dispatch (read-only), and event handler registration
- Pipeline: logging, validation, and idempotency behaviors
- In-memory infrastructure: repository, UoW, read store

## Step 1: Domain Layer (from DDD-only Recipe)

The domain layer is unchanged from Level 1. Start with the [DDD-only recipe](ddd-only-app.md), which gives you:

```
domain/
  value_objects.py   — Money, OrderItem
  events.py          — OrderPlaced, OrderCancelled
  aggregates.py      — Order (AggregateRoot)
```

The `Order` aggregate already records `OrderPlaced` and `OrderCancelled` events via `self._add_event()`. No changes needed.

## Step 2: Commands and Results

```python
# application/commands.py
from uuid import UUID
from datetime import datetime
from pydomain.cqrs.commands import Command, CommandResult, EmptyCommandResult


class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str
    placed_at: datetime


class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: UUID
    items: list[OrderLineItem]


class OrderLineItem(BaseModel):
    product_name: str
    quantity: int
    unit_price: int


class CancelOrderResult(CommandResult):
    order_id: UUID
    cancelled_at: datetime
    reason: str


class CancelOrder(Command[CancelOrderResult]):
    order_id: UUID
    reason: str
```

## Step 3: Queries and Results

```python
# application/queries.py
from uuid import UUID
from datetime import datetime
from pydomain.cqrs.queries import Query, QueryResult


class GetOrderResult(QueryResult):
    order_id: UUID
    customer_id: UUID
    total_amount: int
    item_count: int
    status: str
    placed_at: datetime | None


class GetOrder(Query[GetOrderResult]):
    order_id: UUID
```

## Step 4: Read Store Protocol and Implementation

```python
# application/read_store.py
from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class OrderReadStore(Protocol):
    async def get(self, order_id: UUID) -> dict | None: ...


# infrastructure/read_store.py
class InMemoryOrderReadStore:
    def __init__(self) -> None:
        self._orders: dict[UUID, dict] = {}

    async def get(self, order_id: UUID) -> dict | None:
        return self._orders.get(order_id)

    def insert(self, data: dict) -> None:
        self._orders[data["order_id"]] = data
```

## Step 5: Unit of Work

```python
# infrastructure/unit_of_work.py
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork


class OrderUoW(AbstractUnitOfWork):
    orders: InMemoryOrderRepository

    def __init__(self) -> None:
        super().__init__()
        self.orders = InMemoryOrderRepository()
        self._repos = {"orders": self.orders}

    async def _flush(self) -> None:
        pass  # In-memory — nothing to flush

    async def _commit(self) -> None:
        pass  # In-memory — already persisted
```

## Step 6: Command Handlers

```python
# application/command_handlers.py
from datetime import datetime, UTC
from uuid import uuid4


class PlaceOrderHandler:
    async def __call__(
        self, cmd: PlaceOrder, uow: OrderUoW
    ) -> PlaceOrderResult:
        order = Order(id=uuid4(), customer_id=cmd.customer_id)

        for item in cmd.items:
            order.add_item(item.product_name, item.quantity, item.unit_price)

        order.place()
        await uow.orders.save(order)
        return PlaceOrderResult(
            order_id=order.id,
            status=order.status,
            placed_at=datetime.now(UTC),
        )


class CancelOrderHandler:
    async def __call__(
        self, cmd: CancelOrder, uow: OrderUoW
    ) -> CancelOrderResult:
        order = await uow.orders.get_by_id(cmd.order_id)
        if order is None:
            raise OrderNotFoundError(cmd.order_id)

        order.cancel(cmd.reason)
        await uow.orders.save(order)
        return CancelOrderResult(
            order_id=order.id,
            cancelled_at=datetime.now(UTC),
            reason=cmd.reason,
        )
```

**Why `save()` is enough — no explicit `commit()` needed.** The handler runs inside `CommandBus.dispatch()`'s `async with uow:` block. `save()` registers the aggregate in the repository's tracking set. After the handler returns, the bus explicitly calls `uow.commit()`, which triggers `_flush()` → `_collect_and_stamp()` → `_write_outbox()` → `_commit()`. The handler never calls `commit()` or `rollback()` — the bus owns the transaction boundary. (And `__aexit__` does NOT auto-commit; it only auto-rollbacks on unhandled exceptions.)

## Step 7: Query Handler

```python
# application/query_handlers.py
from pydomain.ddd.exceptions import DomainError


class OrderNotFoundError(DomainError):
    """Raised when an order is not found."""


class GetOrderHandler:
    def __init__(self, read_store: OrderReadStore) -> None:
        self._store = read_store

    async def __call__(self, query: GetOrder) -> GetOrderResult:
        data = await self._store.get(query.order_id)
        if data is None:
            raise OrderNotFoundError(query.order_id)
        return GetOrderResult(**data)
```

## Step 8: Event Handler (Projection Update)

```python
# application/event_handlers.py
class UpdateOrderProjectionHandler:
    def __init__(self, read_store: OrderReadStore) -> None:
        self._store = read_store

    async def __call__(self, event: OrderPlaced) -> None:
        await self._store.insert({
            "order_id": event.order_id,
            "customer_id": event.customer_id,
            "total_amount": event.total_amount,
            "item_count": event.item_count,
            "status": "placed",
            "placed_at": event.occurred_at,
        })
```

## Step 9: Bootstrap

```python
# bootstrap.py
from pydomain.infrastructure.bootstrap import bootstrap
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.cqrs.behaviors import LoggingBehavior, IdempotencyBehavior


async def build_app() -> Application:
    registry = EventRegistry()
    app = await bootstrap(event_registry=registry)

    # Register event types for serialization
    registry.register(OrderPlaced)
    registry.register(OrderCancelled)

    read_store = InMemoryOrderReadStore()

    def order_uow_factory() -> OrderUoW:
        return OrderUoW()

    idempotency_store = InMemoryProcessedCommandStore()
    behaviors = [
        LoggingBehavior(),
        IdempotencyBehavior(idempotency_store),
    ]

    app.message_bus.register_command(
        command_type=PlaceOrder,
        handler=PlaceOrderHandler(),
        uow_factory=order_uow_factory,
        behaviors=behaviors,
    )
    app.message_bus.register_command(
        command_type=CancelOrder,
        handler=CancelOrderHandler(),
        uow_factory=order_uow_factory,
        behaviors=behaviors,
    )

    app.message_bus.register_query(
        query_type=GetOrder,
        handler=GetOrderHandler(read_store),
        behaviors=[LoggingBehavior()],
    )

    app.message_bus.register_event(
        OrderPlaced, UpdateOrderProjectionHandler(read_store)
    )

    return app
```

## Step 10: Application Entry Point

```python
# main.py
import asyncio
from uuid import uuid4


async def main() -> None:
    app = await build_app()

    # Place an order — Application.dispatch() delegates to MessageBus.
    # Events are dispatched internally to registered handlers.
    customer_id = uuid4()
    result, events = await app.dispatch(PlaceOrder(
        customer_id=customer_id,
        items=[
            OrderLineItem(product_name="Widget", quantity=2, unit_price=500),
            OrderLineItem(product_name="Gadget", quantity=1, unit_price=300),
        ],
    ))

    print(f"Order placed: {result.order_id} — {result.status}")

    # Query the read side (event handler updated the projection synchronously)
    order = await app.dispatch(GetOrder(order_id=result.order_id))
    print(f"Read side query: {order.status}, total={order.total_amount}")

    # Cancel the order
    cancel_result, _ = await app.dispatch(CancelOrder(
        order_id=result.order_id,
        reason="Changed my mind",
    ))
    print(f"Order cancelled: {cancel_result.cancelled_at}")


if __name__ == "__main__":
    asyncio.run(main())
```

## Step 11: Tests

```python
# tests/test_cqrs.py
import pytest
from uuid import uuid4


class TestPlaceOrderCommand:
    async def test_place_order_returns_result(self):
        app = await build_app()

        result, events = await app.dispatch(PlaceOrder(
            customer_id=uuid4(),
            items=[OrderLineItem(product_name="Widget", quantity=1, unit_price=500)],
        ))

        assert result.status == "placed"
        # Events are dispatched internally by MessageBus — the
        # UpdateOrderProjectionHandler has already updated the read store.

    async def test_duplicate_command_is_idempotent(self):
        app = await build_app()
        cmd = PlaceOrder(
            customer_id=uuid4(),
            items=[OrderLineItem(product_name="Widget", quantity=1, unit_price=500)],
        )

        result1, _ = await app.dispatch(cmd)
        result2, _ = await app.dispatch(cmd)

        assert result1.order_id == result2.order_id
        # The handler is only called once — idempotency behavior returns
        # the cached result for the duplicate.

    async def test_cancel_order(self):
        app = await build_app()

        place_result, _ = await app.dispatch(PlaceOrder(
            customer_id=uuid4(),
            items=[OrderLineItem(product_name="Widget", quantity=1, unit_price=500)],
        ))

        cancel_result, _ = await app.dispatch(CancelOrder(
            order_id=place_result.order_id,
            reason="Changed my mind",
        ))

        assert cancel_result.reason == "Changed my mind"

    async def test_cancel_nonexistent_order(self):
        app = await build_app()

        with pytest.raises(CommandExecutionError):
            await app.dispatch(CancelOrder(
                order_id=uuid4(),
                reason="Doesn't exist",
            ))


class TestGetOrderQuery:
    async def test_query_after_place(self):
        app = await build_app()

        result, _ = await app.dispatch(PlaceOrder(
            customer_id=uuid4(),
            items=[OrderLineItem(product_name="Widget", quantity=1, unit_price=500)],
        ))

        # Events are dispatched synchronously by MessageBus — the
        # projection is already updated, no need for asyncio.sleep(0).
        order = await app.dispatch(GetOrder(order_id=result.order_id))
        assert order.status == "placed"
        assert order.total_amount == 500
        assert order.item_count == 1
```

## Architecture Recap

```
┌──────────────────────────────────────────────────┐
│                                                │
│  Application (bootstrap composition root)        │
│    │                                             │
│    ├── EventRegistry (serialization)             │
│    └── MessageBus                                │
│          │                                       │
│          ├── Command dispatch                    │
│          │     ├── Logging (behavior)            │
│          │     ├── Idempotency (behavior)        │
│          │     └── Handler ── UoW ── Repo        │
│          │                                       │
│          ├── Query dispatch                      │
│          │     ├── Logging (behavior)            │
│          │     └── Handler ── ReadStore          │
│          │                                       │
│          └── Event dispatch                      │
│                └── EventHandler (projections)    │
│                                                │
└──────┼─────────────────────────────────────────┘
       │
┌──────┼─────────────────────────────────────────┐
│      │              Domain                      │
│      └── AggregateRoot ── Events               │
│                                                │
└────────────────────────────────────────────────┘
```

## What's Next?

- **Event-driven side effects** → [Handle Domain Events](../cqrs/handle-domain-events.md)
- **Full audit trail** → [Level 4: Event Sourcing](../../concepts/es/event-sourcing.md)
- **Saga orchestration** → [Level 5: Sagas](../../concepts/sagas/saga.md)
- **Infrastructure wiring** → [Application Bootstrap concept](../../concepts/infrastructure/bootstrap.md) and [Bootstrap how-to](../infrastructure/bootstrap-application.md)

Each level builds on what you already have — no rewriting required.
