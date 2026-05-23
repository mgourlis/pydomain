"""Event-sourced projection base class with checkpoint tracking and handler dispatch.

An :class:`EventSourcedProjection` extends the CQRS projection concept with
event-store-specific concerns: integer checkpoint tracking (position in the
event stream) and convention-based ``_when_*`` handler dispatch.

This class belongs in the ``es`` module because it assumes a versioned
event stream — a concern of Event Sourcing, not CQRS generally.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from typing import ClassVar

from pydomain.ddd.domain_event import DomainEvent

__all__ = [
    "EventSourcedProjection",
]


class EventSourcedProjection(ABC):
    """Base class for projections backed by a versioned event stream.

    Provides a convenient ABC that adds:

    * **Checkpoint tracking** — an integer position in the event stream,
      incremented on every :meth:`apply` call.
    * **Handler dispatch** — :meth:`handle` routes each event to a
      ``_when_{EventTypeName}`` method by convention.
    * **Rebuild** — resets state and replays a full event stream.

    Subclasses define ``_when_{EventTypeName}`` methods for the event types
    they care about and declare ``name`` and ``version`` as ``ClassVar``
    attributes for checkpoint store lookups and schema version tracking.

    Usage::

        class OrderSummaryProjection(EventSourcedProjection):
            name: ClassVar[str] = "order_summary"
            version: ClassVar[int] = 1

            async def _when_OrderPlaced(self, event: OrderPlaced) -> None:
                ...

            async def _when_OrderShipped(self, event: OrderShipped) -> None:
                ...
    """

    name: ClassVar[str]
    version: ClassVar[int]

    def __init__(self) -> None:
        self._checkpoint: int = 0

    @property
    def checkpoint(self) -> int:
        """The event version processed up to."""
        return self._checkpoint

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

    async def apply(self, event: DomainEvent) -> None:
        """Apply a domain event via handler dispatch.

        Delegates to :meth:`handle` which routes the event to the
        matching ``_when_*`` method.  Increments the checkpoint.

        Parameters
        ----------
        event:
            The domain event to apply.
        """
        await self.handle(event)
        self._checkpoint += 1

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        """Rebuild the projection from scratch by replaying events.

        Resets the checkpoint to ``0`` then applies each event in order.
        Subclasses should override this method to also reset any custom
        state fields **before** calling ``await super().rebuild(events)``.

        Parameters
        ----------
        events:
            The complete event stream in ascending version order.
        """
        self._checkpoint = 0
        for event in events:
            await self.apply(event)
