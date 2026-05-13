"""Event store protocol for event sourcing.

The ``EventStore`` protocol defines the persistence contract for
appending and reading domain events by aggregate stream.  Optimistic
concurrency is managed through an expected-version check on every
append.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydomain.ddd.domain_event import DomainEvent

__all__ = [
    "EventStore",
]


@runtime_checkable
class EventStore(Protocol):
    """Event store protocol for event-sourced aggregates.

    Defines the persistence contract for appending and reading domain
    events by stream (aggregate) identity.  The ``expected_version``
    parameter provides optimistic concurrency control.

    Usage::

        class PostgreSQLEventStore(EventStore):
            ...

        class MySQLEventStore(EventStore):
            ...
    """

    async def append_events(
        self,
        stream_id: UUID,
        events: Sequence[DomainEvent],
        expected_version: int,
    ) -> None:
        """Append events to the stream if the expected version matches.

        Parameters
        ----------
        stream_id:
            The aggregate / stream identity.
        events:
            The domain events to persist.
        expected_version:
            The number of events currently in the stream.  Must be
            ``0`` for a new stream.

        Raises
        ------
        ConcurrencyError
            If the current stream length does not match
            ``expected_version``.
        """
        ...

    async def read_events(self, stream_id: UUID) -> list[DomainEvent]:
        """Return all events for a stream ordered by version.

        Parameters
        ----------
        stream_id:
            The aggregate / stream identity.

        Returns
        -------
        list[DomainEvent]
            The events in ascending version order, or an empty list if
            the stream is unknown.
        """
        ...
