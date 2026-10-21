from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.event_stream import EventStream


@runtime_checkable
class EventStore(Protocol):  # pragma: no mutate block
    """Event store protocol for event-sourced aggregates."""

    async def append_to_stream(
        self,
        aggregate_id: str,
        events: Sequence[DomainEvent],
        expected_version: int,
        command_id: UUID | None = None,
    ) -> None:
        """Append events to the stream if the expected version matches.

        Parameters
        ----------
        aggregate_id:
            The aggregate / stream identity.
        events:
            The domain events to persist. An empty sequence runs the
            duplicate and version checks like any append but, when they
            pass, stores nothing: no stream is created and ``command_id``
            is not recorded.
        expected_version:
            The number of events currently in the stream. Must be
            ``0`` for a new stream.
        command_id:
            Optional UUID that uniquely identifies the command. When provided,
            the event store SHOULD reject duplicate command submissions by
            raising :class:`DuplicateCommandError`. The id counts as
            processed only once its append stores events: an append rejected
            with :class:`ConcurrencyError` leaves it unused, so the command can
            be retried at the current version (ADR-041).

        Raises
        ------
        ConcurrencyError
            If the current stream length does not match ``expected_version``.
            This includes the case where ``expected_version`` is ``0`` and
            the stream already exists.
        DuplicateCommandError
            If ``command_id`` was already processed for this aggregate.
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
            Zero-based offset to start reading from. Must not be negative.

        Returns
        -------
        EventStream
            The stream slice and current version.

        Raises
        ------
        ValueError
            If ``from_version`` is negative, whether or not the stream exists.
        StreamNotFoundError
            If the stream does not exist.
        """
        ...

    async def read_all(self, from_version: int = 0) -> EventStream:
        """Read all events from the global event log starting at ``from_version``.

        Events are ordered by append time across all streams. The version
        field in the returned :class:`EventStream` reflects the total global
        event count.

        Parameters
        ----------
        from_version:
            Zero-based global offset to start reading from. Must not be
            negative.

        Returns
        -------
        EventStream
            All global events from ``from_version`` onward, with the total
            global event count as ``version``.

        Raises
        ------
        ValueError
            If ``from_version`` is negative.
        """
        ...
