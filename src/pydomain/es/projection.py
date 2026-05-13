"""Projection ABC for building event-sourced read models with handler dispatch.

The :class:`Projection` base class provides an event-handler dispatch mechanism
that routes events to ``_when_{EventTypeName}`` methods by the event's class
name.  Unknown events are silently ignored.  Concrete projections declare
``name`` and ``version`` as ``ClassVar`` class attributes for checkpoint store
lookups and schema version tracking.
"""

from __future__ import annotations

from abc import ABC
from typing import ClassVar

from pydomain.ddd.domain_event import DomainEvent


class Projection(ABC):
    """Base class for event-sourced projections with automatic handler dispatch.

    Subclasses set :attr:`name` and :attr:`version` as ``ClassVar`` class
    attributes and define ``_when_{EventTypeName}`` handler methods for the
    event types they care about.  Constructor injection is used for
    dependencies (e.g. a database session or an HTTP client).

    Usage::

        class OrderSummaryProjection(Projection):
            name: ClassVar[str] = "order_summary"
            version: ClassVar[int] = 1

            def __init__(self, db_session: AsyncSession) -> None:
                self._db = db_session

            async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
                ...

            async def _when_OrderShipped(self, event: OrderShipped) -> None:
                ...
    """

    name: ClassVar[str]
    version: ClassVar[int]

    async def handle(self, event: DomainEvent) -> None:
        """Dispatch a domain event to the matching ``_when_*`` handler.

        The method name is constructed from the event's class name as
        ``_when_{EventTypeName}`` (e.g. ``_when_OrderPlaced``).  If the
        method exists on ``self`` it is called with the event; otherwise
        the event is silently ignored.

        Parameters
        ----------
        event:
            The domain event to dispatch.
        """
        handler_name = f"_when_{type(event).__name__}"
        handler = getattr(self, handler_name, None)
        if handler is not None:
            await handler(event)
