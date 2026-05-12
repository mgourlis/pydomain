"""Unit of Work protocol and abstract base class for managing transactional scope.

The ``UnitOfWork`` protocol defines the contract for managing
transactional boundaries and collecting domain events produced
during a unit of work.

``AbstractUnitOfWork`` provides a reusable ABC implementation with
a commit/rollback lifecycle, correlation/causation ID stamping of
domain events, and extension hooks for subclasses.
"""

from __future__ import annotations

from abc import ABC
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydomain.ddd.domain_event import DomainEvent


@runtime_checkable
class UnitOfWork(Protocol):
    """Protocol for Unit of Work implementations.

    The UoW manages transactional scope and domain event collection.
    After a successful ``commit()``, collected domain events are stamped
    with ``correlation_id`` and ``causation_id`` tracing values. Events
    are only published after the commit completes (publish-after-commit
    semantics). Extension hooks are provided for outbox writes and
    idempotency checks. If the context manager exits without an explicit
    ``commit()``, the UoW rolls back by default.
    """

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None: ...

    async def commit(self) -> None:
        """Persist all changes and finalise the unit of work.

        Raises:
            IdempotentCommandIgnored: if a duplicate command identifier is
                detected by the idempotency check.
            CQRSError: if the underlying storage flush or hook chain fails.
        """
        ...

    async def rollback(self) -> None:
        """Undo all changes made during this unit of work.

        Raises:
            CQRSError: if the underlying storage rollback fails.
        """
        ...

    def collect_events(self) -> list[DomainEvent]: ...


class AbstractUnitOfWork(ABC):
    """Abstract Unit of Work for DDD transactional boundaries.

    Provides the full commit/rollback lifecycle, domain event
    collection and stamping, and extension hooks that subclasses
    override to integrate with a concrete database or message bus.

    Subclasses **must** populate ``_seen`` with aggregates observed
    by their repositories so that ``_collect_and_stamp`` can pull
    and stamp pending domain events during ``commit``.
    """

    def __init__(self) -> None:
        self._committed = False
        self._seen: set[Any] = set()
        self._events: list[DomainEvent] = []
        self._correlation_id: UUID | None = None
        self._causation_id: UUID | None = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AbstractUnitOfWork:
        self._committed = False
        self._events.clear()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        if exc_type is not None and not self._committed:
            await self.rollback()

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def commit(self) -> None:
        """Execute the commit hook chain.

        1. ``on_before_commit`` — pre-flush extension hook.
        2. ``_check_idempotency`` — enforce idempotency (overridable no-op).
        3. ``_flush`` — persist pending changes (overridable no-op).
        4. ``_collect_and_stamp`` — pull events from seen aggregates,
           stamp with correlation/causation IDs, store in ``_events``.
        5. ``_write_outbox`` — persist events to outbox (overridable no-op).
        6. ``on_after_commit`` — post-commit extension hook.
        7. Mark as committed.

        Raises:
            IdempotentCommandIgnored: if ``_check_idempotency`` detects a
                duplicate command identifier.
            CQRSError: if ``_flush``, ``_write_outbox``, or any hook in the
                chain raises a storage or persistence error.
        """
        await self.on_before_commit()
        await self._check_idempotency(self._correlation_id or UUID(int=0))
        await self._flush()
        self._collect_and_stamp()
        await self._write_outbox(self._events)
        await self.on_after_commit()
        self._committed = True

    async def rollback(self) -> None:
        """Roll back the current unit of work.

        Resets the committed flag, clears collected events, and
        provides an extension point for subclasses to roll back
        their storage transaction.

        Raises:
            CQRSError: if the underlying storage rollback fails.
        """
        self._committed = False
        self._events.clear()

    # ------------------------------------------------------------------
    # Event access
    # ------------------------------------------------------------------

    def collect_events(self) -> list[DomainEvent]:
        """Return all stamped domain events collected during commit."""
        return self._events

    # ------------------------------------------------------------------
    # Extension hooks (overridable no-ops)
    # ------------------------------------------------------------------

    async def on_before_commit(self) -> None:
        """Extension point. Called before flushing changes.

        Raises:
            CQRSError: override to abort the commit by raising an error.
        """
        return None

    async def _flush(self) -> None:
        """Override to flush changes to storage.

        Raises:
            CQRSError: if the underlying persistence operation fails.
        """
        return None

    async def on_after_commit(self) -> None:
        """Extension point. Called after flushing and stamping.

        Raises:
            CQRSError: override to signal a post-commit failure.
        """
        return None

    async def _check_idempotency(self, command_id: UUID) -> None:
        """Extension point for idempotency enforcement. Default no-op.

        Raises:
            IdempotentCommandIgnored: if a duplicate command identifier is
                detected.
        """
        return None

    async def _write_outbox(self, events: list[DomainEvent]) -> None:
        """Extension point for outbox writes in state-based CQRS. Default no-op.

        Raises:
            CQRSError: if persisting outbox events to storage fails.
        """
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_and_stamp(self) -> None:
        """Iterate seen aggregates, pull events, stamp with tracing IDs.

        Each event is replaced by a frozen copy with ``correlation_id``
        and ``causation_id`` set via ``DomainEvent.stamp()``. The caller
        is responsible for setting ``_correlation_id`` and
        ``_causation_id`` before calling ``commit()``.
        """
        for aggregate in self._seen:
            for event in aggregate.pull_events():
                stamped = event.stamp(
                    correlation_id=self._correlation_id,
                    causation_id=self._causation_id,
                )
                self._events.append(stamped)
