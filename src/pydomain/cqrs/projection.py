"""Projection protocol for building read models from domain events.

A projection transforms domain events into a query-optimized read model
using the left-fold pattern: ``current_state + event -> new_state``.
Read models are disposable -- they can always be rebuilt from the event log.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydomain.ddd.domain_event import DomainEvent

StateT = TypeVar("StateT", covariant=True)

__all__ = [
    "Projection",
    "ProjectionStore",
]


@runtime_checkable
class Projection(Protocol[StateT]):
    """Projection protocol for building read models from domain events.

    A projection transforms domain events into a query-optimized read model.
    It follows the left-fold pattern: ``current_state + event -> new_state``.
    Read models are disposable -- they can always be rebuilt from the event log.

    Usage::

        class OrderSummaryProjection(Projection[OrderSummary]):
            ...

    Type Parameters
    ---------------
    StateT:
        The type of the read model state maintained by this projection.
    """

    @property
    def checkpoint(self) -> int:
        """The event version processed up to.

        Used for idempotency: only events with a higher version should be
        applied. Returns ``0`` for a projection that has processed no events.
        """
        ...

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
    """Protocol for persisting projection state with checkpoint tracking.

    Stores the opaque read model state and the last processed event
    checkpoint, keyed by projection identity.  Implementations are
    responsible for serialization of the state value.

    Usage::

        class PostgresProjectionStore(ProjectionStore):
            ...
    """

    async def load(self, projection_id: str) -> tuple[Any, int] | None:
        """Load ``(state, checkpoint)`` for a projection.

        Parameters
        ----------
        projection_id:
            The unique identity of the projection.

        Returns
        -------
        tuple[Any, int] | None
            A ``(state, checkpoint)`` tuple if found, or ``None`` if no
            state exists for this projection.
        """
        ...

    async def save(self, projection_id: str, state: Any, checkpoint: int) -> None:
        """Persist projection state and checkpoint.

        Parameters
        ----------
        projection_id:
            The unique identity of the projection.
        state:
            The opaque read model state to persist.
        checkpoint:
            The last processed event version.
        """
        ...
