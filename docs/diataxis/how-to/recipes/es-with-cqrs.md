# Recipe: Event Sourcing with CQRS

> **Adoption Level:** 4 · Prerequisites: [ES Aggregates concept](../../concepts/es/event-sourced-aggregates.md), [Command Bus concept](../../concepts/cqrs/command-bus.md), [ES Repositories concept](../../concepts/es/event-sourced-repositories.md), [CQRS with DDD recipe](cqrs-with-ddd.md)

This recipe shows how to combine Event Sourcing with CQRS — commands produce events, aggregates are event-sourced, and projections build read models from the event stream.

## Ingredients

- **Event-sourced aggregate** — `Order` extending `EventSourcedAggregateRoot`
- **Commands and events** — `PlaceOrder` command, `OrderPlaced` event
- **Event store** — `FakeEventStore` (production: PostgreSQL-backed)
- **ES repository** — `EventSourcedRepository` with optional snapshots
- **Command bus** — with `TransactionalUnitOfWork`
- **Projection** — `OrderSummaryProjection` for the read side

## Step 1: Define the aggregate and events

```python
from uuid import UUID
from datetime import datetime, UTC
from typing import ClassVar
from pydomain.es.aggregate import EventSourcedAggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.exceptions import DomainError


class OrderNotPlacable(DomainError): ...


class OrderPlaced(DomainEvent):
    order_id: UUID
    customer_id: UUID
    total_amount: int
    currency: str
    placed_at: datetime


class Order(EventSourcedAggregateRoot[UUID]):
    customer_id: UUID
    status: str = "draft"
    total_amount: int = 0
    currency: str = "EUR"

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self.status = "placed"
            self.customer_id = event.customer_id
            self.total_amount = event.total_amount
            self.currency = event.currency
        else:
            raise ValueError(f"Unknown event: {event!r}")

    def place(self, customer_id: UUID, total_amount: int, currency: str) -> None:
        if self.status != "draft":
            raise OrderNotPlacable("Order is not in draft status")
        self._apply(OrderPlaced(
            order_id=self.id,
            customer_id=customer_id,
            total_amount=total_amount,
            currency=currency,
            placed_at=datetime.now(UTC),
        ))
```

## Step 2: Define the command and handler

```python
from pydomain.cqrs.commands import Command, CommandResult


class PlaceOrderResult(CommandResult):
    order_id: UUID
    status: str


class PlaceOrder(Command[PlaceOrderResult]):
    order_id: UUID
    customer_id: UUID
    total_amount: int
    currency: str


class PlaceOrderHandler:
    def __init__(self, repository: EventSourcedRepository[Order, UUID]) -> None:
        self._repository = repository

    async def handle(self, cmd: PlaceOrder) -> PlaceOrderResult:
        order = await self._repository.get_by_id(cmd.order_id)
        if order is None:
            order = Order(id=cmd.order_id)

        order.place(
            customer_id=cmd.customer_id,
            total_amount=cmd.total_amount,
            currency=cmd.currency,
        )
        await self._repository.save(order, command_id=cmd.command_id)

        return PlaceOrderResult(order_id=order.id, status=order.status)
```

## Step 3: Wire the infrastructure

```python
from pydomain.testing.fake_event_store import FakeEventStore
from pydomain.testing.fake_uow import FakeUnitOfWork
from pydomain.es.event_sourced_repository import EventSourcedRepository
from pydomain.cqrs.command_bus import CommandBus
from pydomain.cqrs.unit_of_work import TransactionalUnitOfWork


event_store = FakeEventStore()
repository = EventSourcedRepository[Order, UUID](
    event_store=event_store,
    aggregate_cls=Order,
)

uow = FakeUnitOfWork(event_store=event_store, repository=repository)

bus = CommandBus()
bus.register_handler(PlaceOrder, PlaceOrderHandler(repository))
bus.configure_middleware([TransactionalUnitOfWork(uow)])
```

## Step 4: Build the projection

```python
from typing import ClassVar
from pydomain.es.projection import EventSourcedProjection


class OrderSummaryProjection(EventSourcedProjection):
    name: ClassVar[str] = "order_summary"
    version: ClassVar[int] = 1

    def __init__(self) -> None:
        super().__init__()
        self.total_orders: int = 0
        self.total_revenue: int = 0

    async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
        self.total_orders += 1
        self.total_revenue += event.total_amount
```

## Step 5: Dispatch and verify

```python
cmd = PlaceOrder(
    order_id=UUID(int=1),
    customer_id=UUID(int=2),
    total_amount=1000,
    currency="EUR",
)
result = await bus.dispatch(cmd)
assert result.status == "placed"

# The event stream captured it
stream = await event_store.read_stream("00000000-0000-0000-0000-000000000001")
assert len(stream.events) == 1
assert isinstance(stream.events[0], OrderPlaced)

# The projection can consume it
projection = OrderSummaryProjection()
await projection.apply(stream.events[0])
assert projection.total_orders == 1
assert projection.total_revenue == 1000
```

## What we built

A full event-sourced CQRS flow: command → handler → aggregate (event-sourced) → event store → projection (read model). The event store is the source of truth. The aggregate state is reconstructed by replaying events. The projection builds a read model from events.

## Next steps

- [Build Projections recipe](build-projections.md) — full projection pipeline with checkpointing
- [Build Denormalized Read Models recipe](build-denormalized-read-models.md) — denormalized views
- [Configure Snapshots how-to](../event-sourcing/connect-event-store.md) — add snapshot support

## Cross-references

- **ADR-024**: Two separate projection types
- **ADR-025**: Projection split across layers
