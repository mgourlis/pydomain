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
    semantics). Extension hooks are provided for outbox writes. If the
    context manager exits without an explicit ``commit()``, the UoW
    rolls back by default.
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


class AbstractUnitOfWork(ABC, UnitOfWork):
    """Abstract Unit of Work for DDD transactional boundaries.

    Provides the full commit/rollback lifecycle, domain event
    collection and stamping, and extension hooks that subclasses
    override to integrate with a concrete database or message bus.

    Subclasses **must** populate ``_repos`` with repository instances
    so that ``_collect_and_stamp`` can pull and stamp pending domain
    events during ``commit``.

    **Concrete UoWs should also expose repository attributes** (e.g.
    ``self.orders``, ``self.customers``) that provide typed, convenient
    access for command handlers.  Handlers receive the UoW via their
    second parameter and use these attributes to load and persist
    aggregates::

        class OrderUoW(AbstractUnitOfWork):
            orders: OrderRepository

            def __init__(self, session_factory) -> None:
                super().__init__()
                self.orders = OrderRepository(session_factory())
                self._repos = {"orders": self.orders}

    The handler then accesses repos through the UoW::

        async def handle(cmd: PlaceOrder, uow: OrderUoW) -> PlaceOrderResult:
            order = await uow.orders.get_by_id(cmd.order_id)
            ...
    """

    def __init__(self) -> None:
        self._committed = False
        self._repos: dict[str, Any] = {}
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

        1. ``_flush`` — persist pending changes (overridable no-op).
        2. ``_collect_and_stamp`` — pull events from the seen aggregate,
           stamp with correlation/causation IDs, store in ``_events``.
        3. ``_write_outbox`` — persist events to outbox (overridable no-op).
        4. ``_commit`` — commit the database transaction (overridable no-op).
        5. Mark as committed.

        Raises:
            CQRSError: if ``_flush``, ``_write_outbox``, ``_commit``,
                or any hook in the chain raises a storage or persistence error.
        """
        await self._flush()
        self._collect_and_stamp()
        await self._write_outbox()
        await self._commit()
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

    async def _flush(self) -> None:
        """Override to flush changes to storage.

        Raises:
            CQRSError: if the underlying persistence operation fails.
        """
        return None

    async def _write_outbox(self) -> None:
        """Extension point for outbox writes in state-based CQRS. Default no-op.

        Subclasses can access ``self._events`` to write stamped domain
        events to an outbox table within the same transaction.

        Raises:
            CQRSError: if persisting outbox events to storage fails.
        """
        return None

    async def _commit(self) -> None:
        """Override to commit the database transaction.

        Called after ``_write_outbox`` so that both aggregate state and
        outbox entries are committed atomically.

        Raises:
            CQRSError: if the underlying transaction commit fails.
        """
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_and_stamp(self) -> None:
        """Pull events from all registered repos and stamp with tracing IDs.

        Each repo's ``pull_events()`` drains its internal event buffer.
        Each event is replaced by a frozen copy with ``correlation_id``
        and ``causation_id`` set via ``DomainEvent.stamp()``. The caller
        is responsible for setting ``_correlation_id`` and
        ``_causation_id`` before calling ``commit()``.
        """
        for repo in self._repos.values():
            for event in repo.pull_events():
                stamped = event.stamp(
                    correlation_id=self._correlation_id,
                    causation_id=self._causation_id,
                )
                self._events.append(stamped)
