# Recipe: All Modules Integration

> **Adoption Level:** 5 ( Full Stack) · Prerequisites: All Phase 1–7 concepts and how-tos

This recipe wires every pydomain module into a single application — DDD domain model, CQRS command/query buses, Event Sourcing persistence, and Saga orchestration — all composed via `bootstrap()`.

## What You'll Build

A complete **Order Processing** application:

- **DDD:** `Order` aggregate, `Money` VO, `OrderPlaced`/`OrderCancelled` events
- **CQRS:** `PlaceOrder`, `CancelOrder` commands, `GetOrder` query, handlers, `MessageBus`
- **ES:** Event-sourced aggregate, `EventStore`, `EventSourcedRepository`, `OrderSummaryProjection`
- **Sagas:** `OrderFulfillmentSaga` — reserves inventory after order placement
- **Infrastructure:** `EventRegistry`, `InMemoryMessageBroker`, `SagaManager`

## Step 1: Domain Model (DDD)

```python
# domain/model.py
from uuid import UUID
from pydantic import field_validator
from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.value_object import ValueObject
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import DomainError


# ── Value Objects ────────────────────────────────────────────────────

class Money(ValueObject):
    amount: int
    currency: str = "EUR"

    @field_validator("amount")
    @classmethod
    def amount_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Amount cannot be negative")
        return v


class OrderLineItem(ValueObject):
    product_name: str
    quantity: int
    unit_price: int


# ── Domain Events ─────────────────────────────────────────────────────

class OrderPlaced(DomainEvent):
    order_id: UUID
    customer_id: UUID
    total_amount: int
    currency: str


class OrderCancelled(DomainEvent):
    order_id: UUID
    reason: str


# ── Domain Errors ─────────────────────────────────────────────────────

class OrderNotModifiable(DomainError): ...
class OrderNotPlaceable(DomainError): ...
class OrderNotCancellable(DomainError): ...


# ── Aggregate Root ────────────────────────────────────────────────────

class Order(AggregateRoot[UUID]):
    customer_id: UUID
    items: list[OrderLineItem] = []
    status: str = "draft"
    currency: str = "EUR"

    @property
    def total_amount(self) -> int:
        return sum(item.quantity * item.unit_price for item in self.items)

    def add_item(self, product_name: str, quantity: int, unit_price: int) -> None:
        if self.status != "draft":
            raise OrderNotModifiable("Cannot modify a non-draft order")
        self.items.append(OrderLineItem(
            product_name=product_name, quantity=quantity, unit_price=unit_price,
        ))

    def place(self) -> None:
        if self.status != "draft":
            raise OrderNotPlaceable(f"Cannot place order in '{self.status}' status")
        if not self.items:
            raise OrderNotPlaceable("Cannot place an empty order")
        self.status = "placed"
        self._add_event(OrderPlaced(
            order_id=self.id,
            customer_id=self.customer_id,
            total_amount=self.total_amount,
            currency=self.currency,
        ))

    def cancel(self, reason: str) -> None:
        if self.status not in ("draft", "placed"):
            raise OrderNotCancellable(f"Cannot cancel order in '{self.status}' status")
        self.status = "cancelled"
        self._add_event(OrderCancelled(order_id=self.id, reason=reason))
```

## Step 2: Application Layer (CQRS)

```python
# application/commands.py
from uuid import UUID
from pydomain.cqrs.commands import Command, CommandResult


class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str


class PlaceOrder(Command[PlaceOrderResult]):
    customer_id: UUID
    items: list[dict]  # [{"product_name": "X", "quantity": 2, "unit_price": 500}]


class CancelOrderResult(CommandResult):
    order_id: UUID
    cancelled_at: datetime


class CancelOrder(Command[CancelOrderResult]):
    order_id: UUID
    reason: str
```

```python
# application/queries.py
from uuid import UUID
from pydomain.cqrs.queries import Query, QueryResult


class GetOrderResult(QueryResult):
    order_id: UUID
    customer_id: UUID
    total_amount: int
    status: str


class GetOrder(Query[GetOrderResult]):
    order_id: UUID
```

```python
# application/command_handlers.py
from datetime import datetime, UTC
from uuid import uuid4
from pydomain.ddd.exceptions import DomainError

from domain.model import Order, OrderLineItem


class OrderNotFoundError(DomainError):
    """Raised when an order is not found."""


class PlaceOrderHandler:
    def __init__(self, repository):
        self._repo = repository

    async def __call__(self, cmd: PlaceOrder, uow) -> PlaceOrderResult:
        order = Order(id=uuid4(), customer_id=cmd.customer_id)
        for item in cmd.items:
            order.add_item(
                product_name=item["product_name"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
            )
        order.place()
        await self._repo.save(order)
        return PlaceOrderResult(order_id=order.id, status=order.status)


class CancelOrderHandler:
    def __init__(self, repository):
        self._repo = repository

    async def __call__(self, cmd: CancelOrder, uow) -> CancelOrderResult:
        order = await self._repo.get_by_id(cmd.order_id)
        if order is None:
            raise OrderNotFoundError(cmd.order_id)
        order.cancel(cmd.reason)
        await self._repo.save(order)
        return CancelOrderResult(
            order_id=order.id,
            cancelled_at=datetime.now(UTC),
        )
```

```python
# application/query_handlers.py
class GetOrderHandler:
    def __init__(self, read_store):
        self._store = read_store

    async def __call__(self, query: GetOrder) -> GetOrderResult:
        data = await self._store.get(query.order_id)
        if data is None:
            raise OrderNotFoundError(query.order_id)
        return GetOrderResult(**data)
```

```python
# application/event_handlers.py
class UpdateOrderProjectionHandler:
    def __init__(self, read_store):
        self._store = read_store

    async def __call__(self, event: OrderPlaced) -> None:
        await self._store.insert({
            "order_id": event.order_id,
            "customer_id": event.customer_id,
            "total_amount": event.total_amount,
            "status": "placed",
        })
```

## Step 3: Infrastructure Layer

```python
# infrastructure/read_store.py
from uuid import UUID


class InMemoryOrderReadStore:
    def __init__(self):
        self._orders: dict[UUID, dict] = {}

    async def get(self, order_id: UUID) -> dict | None:
        return self._orders.get(order_id)

    async def insert(self, data: dict) -> None:
        self._orders[data["order_id"]] = data
```

```python
# infrastructure/repository.py
from uuid import UUID
from pydomain.ddd.repository import Repository
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import ConcurrencyError

from domain.model import Order


class InMemoryOrderRepository(Repository[Order, UUID]):
    def __init__(self):
        self._store: dict[UUID, Order] = {}
        self._seen: list[Order] = []

    async def save(self, aggregate: Order, command_id: UUID | None = None) -> None:
        existing = self._store.get(aggregate.id)
        if existing is not None and existing.version != aggregate.version:
            raise ConcurrencyError("Version mismatch")
        if existing is None:
            self._store[aggregate.id] = aggregate
        else:
            aggregate.version += 1
            self._store[aggregate.id] = aggregate
        self._seen.append(aggregate)

    async def get_by_id(self, id_: UUID) -> Order | None:
        found = self._store.get(id_)
        if found is not None:
            self._seen.append(found)
        return found

    async def delete(self, id_: UUID) -> None:
        agg = self._store.pop(id_, None)
        if agg is not None:
            self._seen.append(agg)

    def pull_events(self) -> list[DomainEvent]:
        events: list[DomainEvent] = []
        for agg in self._seen:
            events.extend(agg.pull_events())
        self._seen.clear()
        return events
```

```python
# infrastructure/unit_of_work.py
from pydomain.cqrs.unit_of_work import AbstractUnitOfWork


class OrderUnitOfWork(AbstractUnitOfWork):
    orders: InMemoryOrderRepository

    def __init__(self):
        super().__init__()
        self.orders = InMemoryOrderRepository()
        self._repos = {"orders": self.orders}

    async def _flush(self) -> None:
        pass

    async def _commit(self) -> None:
        pass
```

## Step 4: Saga

```python
# domain/order_fulfillment_saga.py
from datetime import timedelta
from pydomain.cqrs.saga import Saga
from pydomain.cqrs.saga.state import SagaState

from domain.model import OrderPlaced


class ReserveInventory(Command[UUID]):
    order_id: UUID


class CancelInventoryReservation(Command[UUID]):
    order_id: UUID


class OrderFulfillmentSaga(Saga[SagaState]):
    listens_to = [OrderPlaced]
    default_timeout = timedelta(hours=24)

    def __init__(self, state: SagaState) -> None:
        super().__init__(state)

        self.on(OrderPlaced,
                send=lambda e: ReserveInventory(order_id=e.order_id),
                step="reserving_inventory",
                compensate=lambda e: CancelInventoryReservation(
                    order_id=e.order_id,
                ),
                compensate_description="Cancel inventory reservation",
                complete=True)
```

## Step 5: Bootstrap Composition Root

```python
# bootstrap.py
import asyncio
from uuid import uuid4

from pydomain.infrastructure.bootstrap import bootstrap, Application
from pydomain.infrastructure.event_registry import EventRegistry
from pydomain.infrastructure.message_bus import MessageBus
from pydomain.cqrs.behaviors import LoggingBehavior, IdempotencyBehavior
from pydomain.cqrs.saga import SagaRegistry, SagaManager

from pydomain.testing import (
    FakeRepository,
    FakeUnitOfWork,
    FakeSagaRepository,
    InMemoryMessageBroker,
)

from domain.model import Order, OrderPlaced, OrderCancelled
from domain.order_fulfillment_saga import OrderFulfillmentSaga
from application.commands import PlaceOrder, CancelOrder
from application.queries import GetOrder
from application.command_handlers import PlaceOrderHandler, CancelOrderHandler
from application.query_handlers import GetOrderHandler
from application.event_handlers import UpdateOrderProjectionHandler
from infrastructure.read_store import InMemoryOrderReadStore
from infrastructure.repository import InMemoryOrderRepository
from infrastructure.unit_of_work import OrderUnitOfWork


async def build_app(
    use_fakes: bool = True,
) -> Application:
    """Composition root — swap fakes for real adapters in production."""

    registry = EventRegistry()
    registry.register(OrderPlaced)
    registry.register(OrderCancelled)

    message_broker = InMemoryMessageBroker() if use_fakes else None

    app = await bootstrap(
        event_registry=registry,
        message_broker=message_broker,
    )

    # ── Repositories ──────────────────────────────────────────────────

    order_repo: InMemoryOrderRepository = InMemoryOrderRepository()
    saga_repo: FakeSagaRepository = FakeSagaRepository()
    read_store = InMemoryOrderReadStore()

    # ── Saga Wiring ────────────────────────────────────────────────────

    saga_registry = SagaRegistry()
    saga_registry.register_saga(OrderFulfillmentSaga)

    saga_manager = SagaManager(
        repository=saga_repo,
        registry=saga_registry,
        command_bus=app._message_bus,
    )
    saga_manager.bind_to(app._message_bus)

    # ── Idempotency ────────────────────────────────────────────────────

    from pydomain.testing import FakeProcessedCommandStore
    idempotency_store = FakeProcessedCommandStore()
    behaviors = [
        LoggingBehavior(),
        IdempotencyBehavior(idempotency_store),
    ]

    # ── Command Registration ───────────────────────────────────────────

    def order_uow_factory() -> OrderUnitOfWork:
        uow = OrderUnitOfWork()
        uow.orders = order_repo  # Share repo across UoW instances
        uow._repos["orders"] = order_repo
        return uow

    app._message_bus.register_command(
        command_type=PlaceOrder,
        handler=PlaceOrderHandler(order_repo),
        uow_factory=order_uow_factory,
        behaviors=behaviors,
    )

    app._message_bus.register_command(
        command_type=CancelOrder,
        handler=CancelOrderHandler(order_repo),
        uow_factory=order_uow_factory,
        behaviors=behaviors,
    )

    # ── Query Registration ─────────────────────────────────────────────

    app._message_bus.register_query(
        query_type=GetOrder,
        handler=GetOrderHandler(read_store),
        behaviors=[LoggingBehavior()],
    )

    # ── Event Handler Registration ─────────────────────────────────────

    app._message_bus.register_event(
        OrderPlaced, UpdateOrderProjectionHandler(read_store)
    )

    return app
```

## Step 6: Application Entry Point

```python
# main.py
import asyncio
from uuid import uuid4
from bootstrap import build_app


async def main() -> None:
    app = await build_app(use_fakes=True)

    try:
        customer_id = uuid4()

        # Place an order
        result, events = await app.dispatch(PlaceOrder(
            customer_id=customer_id,
            items=[
                {"product_name": "Widget", "quantity": 2, "unit_price": 500},
                {"product_name": "Gadget", "quantity": 1, "unit_price": 300},
            ],
        ))
        print(f"Order placed: {result.order_id} — {result.status}")

        # Query the read side (event handler updated projection synchronously)
        order = await app.dispatch(GetOrder(order_id=result.order_id))
        print(f"Read side: status={order.status}, total={order.total_amount}")

        # Cancel the order
        cancel_result, _ = await app.dispatch(CancelOrder(
            order_id=result.order_id,
            reason="Changed my mind",
        ))
        print(f"Order cancelled at {cancel_result.cancelled_at}")

        # Saga ran to completion
        import logging
        logging.basicConfig(level=logging.DEBUG)

    finally:
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

## Step 7: Full Integration Test

```python
# tests/test_integration.py
import pytest
from uuid import uuid4

from pydomain.testing import (
    FakeRepository,
    FakeUnitOfWork,
    FakeSagaRepository,
    InMemoryMessageBroker,
)


@pytest.mark.anyio
class TestOrderProcessingIntegration:
    async def test_place_order_to_completion(self):
        app = await build_app(use_fakes=True)

        customer_id = uuid4()
        result, events = await app.dispatch(PlaceOrder(
            customer_id=customer_id,
            items=[
                {"product_name": "Widget", "quantity": 2, "unit_price": 500},
                {"product_name": "Gadget", "quantity": 1, "unit_price": 300},
            ],
        ))

        # Command succeeded
        assert result.status == "placed"

        # Domain events emitted
        placed = [e for e in events if isinstance(e, OrderPlaced)]
        assert len(placed) == 1
        assert placed[0].customer_id == customer_id
        assert placed[0].total_amount == 1300

        # Read side updated synchronously
        order = await app.dispatch(GetOrder(order_id=result.order_id))
        assert order.status == "placed"
        assert order.total_amount == 1300

        await app.shutdown()

    async def test_cancel_order(self):
        app = await build_app(use_fakes=True)

        result, _ = await app.dispatch(PlaceOrder(
            customer_id=uuid4(),
            items=[{"product_name": "Widget", "quantity": 1, "unit_price": 100}],
        ))

        cancel, _ = await app.dispatch(CancelOrder(
            order_id=result.order_id,
            reason="Testing cancellation",
        ))

        assert cancel.order_id == result.order_id

        await app.shutdown()
```

## Module Dependency Graph

```
Domain (DDD)              Application (CQRS)         Infrastructure
───────────────────      ──────────────────────     ───────────────────────
                          Commands ──────────┐
                          │                   │
                          │    CommandHandler │
                          │         │         │
AggregateRoot ────────────┤         │         ├── MessageBus
  └── DomainEvents ───────┤         │         │     ├── CommandBus
                           │    QueryHandler  │     ├── QueryBus
ValueObjects ─────────────┤         │         │     └── Event dispatch
                           │         │         │
                          Queries ────────────┘           ├── Bootstrap
                                                          ├── EventRegistry
Saga ──────────────────────────────────────────────────────── SagaManager
  └── SagaState                                                    │
  └── SagaRegistry                                                 │
                                                                   ├── SagaRepository
Event Store (ES) ──────────────────────────────────────────────────┤
  └── EventSourcedAggregateRoot                                    │
  └── Projections                                                  │
                                                                   ├── MessageBroker
Fakes (testing) ─────── FakeRepository, FakeUoW,                   │
                        FakeEventStore, FakeSagaRepository,         ├── InboundEventGateway
                        InMemoryMessageBroker, etc.                 │
                                                                   └── shutdown()
```

## What's Next?

- [Test Your Application recipe](test-your-application.md) — comprehensive testing guide
- [Saga Orchestration recipe](saga-orchestration.md) — deep dive on saga patterns
- [Publish Integration Events recipe](publish-integration-events.md) — cross-service communication
- [API Reference](../../api-reference/) — auto-generated API docs
