"""Projection protocol and store for building read models from events.

A projection transforms domain events into a query-optimized read model
using the left-fold pattern: ``current_state + event -> new_state``.
Read models are disposable -- they can always be rebuilt from the event log.

This module contains only the pure CQRS abstractions.  Event-sourcing-specific
concerns (checkpoint tracking, ``_when_*`` handler dispatch) live in
:mod:`pydomain.es.projection`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pydomain.ddd.domain_event import DomainEvent

__all__ = [
    "Projection",
    "ProjectionStore",
]


@runtime_checkable
class Projection[StateT](Protocol):
    """Projection protocol for building read models from domain events.

    A projection transforms domain events into a query-optimized read model.
    It follows the left-fold pattern: ``current_state + event -> new_state``.
    Read models are disposable -- they can always be rebuilt from the event log.

    This protocol captures the **CQRS** essence of a projection — applying
    events and rebuilding — without coupling to any particular event-delivery
    mechanism (event store, message bus, same-transaction sync).

    Usage::

        class OrderSummaryProjection(Projection[OrderSummary]):
            ...

    Type Parameters
    ---------------
    StateT:
        The type of the read model state maintained by this projection.
    """

    async def apply(self, event: DomainEvent) -> None:
        """Apply a domain event to this projection.

        Transforms the current read model state according to the event's
        meaning.  The projection is responsible for determining whether
        the event is relevant (e.g. via ``isinstance`` checks) and updating
        its internal state accordingly.  Not an error if the event is
        irrelevant -- simply skip it.

        Parameters
        ----------
        event:
            The domain event to apply.
        """
        ...

    async def rebuild(self, events: Sequence[DomainEvent]) -> None:
        """Rebuild the projection from scratch by replaying a full event stream.

        Resets internal state then applies each event in order.  Used when
        a projection is first created or needs to be rebuilt from the
        event log.

        Parameters
        ----------
        events:
            The complete event stream in ascending version order.
        """
        ...


@runtime_checkable
class ProjectionStore(Protocol):
    """Protocol for persisting projection read model state.

    Stores the opaque read model state, keyed by projection identity.
    Implementations are responsible for serialization of the state value.

    This store has no checkpoint concept — it persists only the derived
    read model state.  Event-stream position tracking is handled by
    :class:`~pydomain.es.checkpoint_store.CheckpointStore` in the ES layer.

    Usage::

        class PostgresProjectionStore(ProjectionStore):
            ...
    """

    async def load(self, projection_id: str) -> Any | None:
        """Load the persisted state for a projection.

        Parameters
        ----------
        projection_id:
            The unique identity of the projection.

        Returns
        -------
        Any | None
            The persisted state if found, or ``None`` if no state exists.
        """
        ...

    async def save(self, projection_id: str, state: Any) -> None:
        """Persist projection state.

        Parameters
        ----------
        projection_id:
            The unique identity of the projection.
        state:
            The opaque read model state to persist.
        """
        ...
