from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.models import EventStream


@runtime_checkable
class EventStore(Protocol):
    """Event store protocol for event-sourced aggregates."""

    async def append_to_stream(
        self,
        aggregate_id: str,
        events: Sequence[DomainEvent],
        expected_version: int,
    ) -> None:
        """Append events to the stream if the expected version matches.

        Parameters
        ----------
        aggregate_id:
            The aggregate / stream identity.
        events:
            The domain events to persist.
        expected_version:
            The number of events currently in the stream. Must be
            ``0`` for a new stream.

        Raises
        ------
        StreamAlreadyExistsError
            If ``expected_version`` is ``0`` and the stream already exists.
        ConcurrencyError
            If the current stream length does not match ``expected_version``.
        """
        ...

    async def read_stream(
        self,
        aggregate_id: str,
        from_version: int = 0,
    ) -> EventStream:
        """Read events from a stream starting at the given version.

        Parameters
        ----------
        aggregate_id:
            The aggregate / stream identity.
        from_version:
            Zero-based offset to start reading from.

        Returns
        -------
        EventStream
            The stream slice and current version.

        Raises
        ------
        StreamNotFoundError
            If the stream does not exist.
        """
        ...
