"""Shared synthetic domain for ES probes."""
from __future__ import annotations
from pydantic import BaseModel, Field
from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot


class OrderPlaced(DomainEvent):
    order_id: str
    customer_name: str


class LineItemAdded(DomainEvent):
    order_id: str
    item_name: str
    price: float


class OrderShipped(DomainEvent):
    order_id: str


class Address(BaseModel):
    street: str = ""
    city: str = ""


class Order(EventSourcedAggregateRoot[str]):
    customer_name: str = ""
    items: list[dict] = Field(default_factory=list)
    status: str = "new"
    address: Address = Field(default_factory=Address)

    def place(self, name: str) -> None:
        self._apply(OrderPlaced(order_id=self.id, customer_name=name))

    def add(self, item: str, price: float) -> None:
        self._apply(LineItemAdded(order_id=self.id, item_name=item, price=price))

    def ship(self) -> None:
        self._apply(OrderShipped(order_id=self.id))

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self.customer_name = event.customer_name
            self.status = "placed"
            self.address = Address(street="1 Synthetic St", city="Testville")
        elif isinstance(event, LineItemAdded):
            self.items.append({"name": event.item_name, "price": event.price})
        elif isinstance(event, OrderShipped):
            self.status = "shipped"
        else:
            raise ValueError(f"Unknown event: {event!r}")
