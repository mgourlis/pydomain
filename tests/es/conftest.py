"""Shared test domain objects for the es (event sourcing) test suite.

Domain event subclasses and a concrete EventSourcedAggregateRoot subclass
used across multiple test modules in tests/es/.
"""

from pydomain.ddd import DomainEvent
from pydomain.es.aggregate import EventSourcedAggregateRoot


class OrderPlaced(DomainEvent):
    """A test domain event representing order placement."""

    order_id: str
    customer_name: str


class LineItemAdded(DomainEvent):
    """A test domain event representing adding a line item to an order."""

    order_id: str
    item_name: str
    price: float


class OrderCancelled(DomainEvent):
    """A test domain event representing order cancellation."""

    order_id: str
    reason: str


class TestOrder(EventSourcedAggregateRoot[str]):
    """Concrete event-sourced aggregate for testing."""

    customer_name: str = ""
    items: list[dict] = []
    status: str = "new"

    def _when(self, event: DomainEvent) -> None:
        if isinstance(event, OrderPlaced):
            self.customer_name = event.customer_name
            self.status = "placed"
        elif isinstance(event, LineItemAdded):
            self.items.append({"name": event.item_name, "price": event.price})
        elif isinstance(event, OrderCancelled):
            self.status = "cancelled"
        else:
            raise ValueError(f"Unknown event: {event!r}")
