from __future__ import annotations

from abc import abstractmethod

from pydomain.ddd.aggregate_root import AggregateRoot
from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.snapshot import Snapshot


class EventSourcedAggregateRoot[TId](AggregateRoot[TId]):
    """Aggregate Root whose state is built from an event stream.

    Instead of mutating fields directly and then recording events,
    subclasses call ``_apply(event)`` to both mutate state AND record
    the event.  During reconstitution from the event store,
    ``_replay(event)`` rebuilds state without buffering new events.
    """

    def _apply(self, event: DomainEvent) -> None:
        """Record an event and mutate state.

        Calls ``_when(event)`` to update aggregate fields, then buffers
        the event via ``_add_event()`` (inherited from AggregateRoot),
        and finally increments ``self.version``.
        """
        self._when(event)
        self._add_event(event)
        self.version += 1

    @abstractmethod
    def _when(self, event: DomainEvent) -> None:
        """Mutate state in response to an event.

        Subclasses dispatch by event type using ``isinstance``::

            def _when(self, event: DomainEvent) -> None:
                if isinstance(event, OrderPlaced):
                    self.status = OrderStatus.PLACED
                elif isinstance(event, LineItemAdded):
                    self.line_items.append(event.line_item)
                else:
                    raise ValueError(f"Unknown event: {event!r}")
        """
        ...

    def _replay(self, event: DomainEvent) -> None:
        """Rebuild state from a historical event.

        Calls ``_when(event)`` and increments ``self.version`` but does
        NOT buffer the event -- reconstitution must not produce new
        pending events.
        """
        self._when(event)
        self.version += 1

    def _take_snapshot(self) -> Snapshot:
        """Capture current aggregate state as a Snapshot.

        Uses ``model_dump(mode='python')`` to serialise the full aggregate
        state into a dict, then wraps it in a :class:`Snapshot` for the
        snapshot store.

        This method does **not** persist the snapshot — that is the
        repository's responsibility.  It does **not** mutate aggregate
        state either.
        """
        state = self.model_dump(mode="python")
        state.pop("version", None)
        return Snapshot(
            aggregate_id=str(self.id),
            version=self.version,
            state=state,
        )

    def pull_events(self) -> list[DomainEvent]:
        """Override to document the event-sourced contract.

        Delegates directly to :meth:`AggregateRoot.pull_events` which
        drains ``_pending_events`` and returns them.  The aggregate
        version is already correct at this point -- it was incremented
        by :meth:`_apply` or :meth:`_replay` *before* ``pull_events``
        is called, so no additional version management is needed here.
        """
        return super().pull_events()
