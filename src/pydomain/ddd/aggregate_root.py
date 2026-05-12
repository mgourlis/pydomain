from __future__ import annotations

from pydantic import PrivateAttr

from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.entity import Entity


class AggregateRoot[TId](Entity[TId]):
    """Generic Aggregate Root base class.

    Aggregate Roots are the consistency boundaries in DDD. They extend
    ``Entity[TId]`` and additionally manage a collection of **pending
    domain events** that are recorded during command handling and
    subsequently dispatched by the UnitOfWork.

    Usage::

        class Order(AggregateRoot[UUID]):
            ...

            def submit(self) -> None:
                # ... business logic ...
                self._add_event(OrderSubmitted(order_id=self.id))
    """

    _pending_events: list[DomainEvent] = PrivateAttr(default_factory=list)

    def _add_event(self, event: DomainEvent) -> None:
        """Record a domain event.

        The event is buffered in ``_pending_events`` and will be
        returned (and cleared) by the next call to ``pull_events()``.
        """
        self._pending_events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        """Return all pending domain events and clear the internal buffer.

        This method is typically called by the UnitOfWork after a
        successful ``commit()`` to dispatch the events to the message bus.
        """
        events, self._pending_events = self._pending_events, []
        return events
