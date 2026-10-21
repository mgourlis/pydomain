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

from pydomain.cqrs.exceptions import UnitOfWorkAlreadyCommittedError
from pydomain.cqrs.outbox import OutboxWriter
from pydomain.ddd.domain_event import DomainEvent


@runtime_checkable
class UnitOfWork(Protocol):
    """Protocol for Unit of Work implementations.

    The UoW manages transactional scope and domain event collection.
    After a successful ``commit()``, collected domain events are stamped
    with ``correlation_id`` and ``causation_id`` tracing values. Events
    are only published after the commit completes (publish-after-commit
    semantics). Extension hooks are provided for outbox writes. If the
    context manager exits without an explicit ``commit()``, with or
    without an exception, the UoW rolls back.

    A scope (from ``__aenter__`` or ``rollback()`` to the exit) commits at
    most once. A rollback discards every event the scope staged, including
    events buffered in repositories that outlive the unit of work, so they
    never reach a later commit. A committed scope also discards, on exit,
    the events staged after its commit (ADR-068).

    A unit of work can be entered again after it exits, one scope at a
    time: each ``async with`` starts a new scope. It is not reentrant, and
    two scopes must not run on one instance at once.
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
            UnitOfWorkAlreadyCommittedError: if ``commit()`` was already
                called in this scope, whether it succeeded or failed.
                Nothing is persisted or written a second time; a first
                commit that succeeded stays persisted.
            CQRSError: if the underlying storage flush or hook chain fails.
        """
        ...

    async def rollback(self) -> None:
        """Undo all changes made during this unit of work.

        Discards the events the scope staged, so no later ``commit()``
        collects them, and starts a new scope. Calling it again changes
        nothing.

        Raises:
            CQRSError: if the underlying storage rollback fails.
        """
        ...

    def collect_events(self) -> list[DomainEvent]:
        """Return the stamped events of the last commit, as a new list."""
        ...


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

    Pass an ``OutboxWriter`` to wire the transactional outbox without
    overriding any hook — see :meth:`_write_outbox`.
    """

    def __init__(self, *, outbox_writer: OutboxWriter | None = None) -> None:
        self._repos: dict[str, Any] = {}
        self._events: list[DomainEvent] = []
        self._correlation_id: UUID | None = None
        self._causation_id: UUID | None = None
        self._outbox_writer = outbox_writer
        self._start_scope()

    def _start_scope(self) -> None:
        """Forget the previous scope's commit and collected events."""
        self._committed: bool = False
        self._commit_started: bool = False
        self._events.clear()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AbstractUnitOfWork:
        self._start_scope()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        """Roll back unless the scope committed, with or without an exception.

        A scope that did not commit is rolled back through ``rollback()``. A
        committed scope is not rolled back, and keeps its collected events,
        but the repositories' buffers are still drained: events staged after
        the commit never reach a later scope.

        The exception, if any, propagates, including a cancellation. If the
        rollback or the drain raises too, its exception propagates instead,
        with the body's as its ``__context__`` (Python's default for a
        clean-up that fails).
        """
        if self._committed:
            self._discard_staged_events()
        else:
            await self.rollback()

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def commit(self) -> None:
        """Execute the commit hook chain.

        1. ``_flush`` — persist pending changes (overridable no-op).
        2. ``_collect_and_stamp`` — pull events from the seen aggregate,
           stamp with correlation/causation IDs, store in ``_events``.
        3. ``_write_outbox`` — persist integration events to the outbox
           (no-op unless an ``OutboxWriter`` was supplied or the hook is
           overridden).
        4. ``_commit`` — commit the database transaction (overridable no-op).
        5. Mark as committed.

        A scope commits once: a second call raises before any hook runs.
        After a successful first commit its changes stay persisted and the
        scope stays committed, so leaving it does not roll back. After a
        first commit that failed part-way, the retry raises too: call
        ``rollback()`` (or enter the unit of work again) to start a new
        scope, and run the work again.

        Raises:
            UnitOfWorkAlreadyCommittedError: if ``commit()`` was already
                called in this scope. The message says whether the first
                commit succeeded.
            CQRSError: if ``_flush``, ``_write_outbox``, ``_commit``,
                or any hook in the chain raises a storage or persistence error.
        """
        if self._committed:
            raise UnitOfWorkAlreadyCommittedError(
                "commit() was already called in this unit-of-work scope; changes "
                "from the first commit are persisted. Handlers must not call "
                "commit() (ADR-020)."
            )
        if self._commit_started:
            raise UnitOfWorkAlreadyCommittedError(
                "commit() was already called in this unit-of-work scope and failed; "
                "nothing from it is guaranteed to be persisted. Call rollback(), or "
                "enter the unit of work again, to start a new scope."
            )
        self._commit_started = True
        await self._flush()
        self._collect_and_stamp()
        await self._write_outbox()
        await self._commit()
        self._committed = True

    async def rollback(self) -> None:
        """Roll back the current unit of work and start a new scope.

        1. ``_rollback`` — roll back the storage transaction (overridable
           no-op).
        2. Drain every repository's event buffer through ``pull_events()``
           and discard the events, so a later ``commit()`` never collects
           them, even from a repository that outlives this unit of work.
        3. Clear the collected events and the commit record.

        Steps 2 and 3 run even when ``_rollback`` or a repository's
        ``pull_events()`` raises; the error is then re-raised (the first
        ``pull_events()`` error, or the drain's over ``_rollback``'s, with
        ``_rollback``'s as its ``__context__``). Calling ``rollback()``
        again drains nothing new; ``_rollback`` runs again, so it should be
        idempotent.

        Override :meth:`_rollback`, not this method, to roll back storage.

        Raises:
            CQRSError: if the underlying storage rollback fails.
        """
        try:
            await self._rollback()
        finally:
            try:
                self._discard_staged_events()
            finally:
                self._start_scope()

    def _discard_staged_events(self) -> None:
        """Drain every repository's buffer and discard the events.

        Every repository is drained even if one ``pull_events()`` raises;
        the first error is re-raised afterwards.
        """
        first_error: Exception | None = None
        for repo in self._repos.values():
            try:
                repo.pull_events()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    # ------------------------------------------------------------------
    # Event access
    # ------------------------------------------------------------------

    def collect_events(self) -> list[DomainEvent]:
        """Return the stamped domain events of the last commit, as a new list.

        The list is a copy: changing it, or a later scope on this unit of
        work, does not change the other.
        """
        return list(self._events)

    # ------------------------------------------------------------------
    # Extension hooks (overridable no-ops)
    # ------------------------------------------------------------------

    async def _flush(self) -> None:
        """Override to flush changes to storage.

        Raises:
            CQRSError: if the underlying persistence operation fails.
        """
        return None

    async def _rollback(self) -> None:
        """Override to roll back the storage transaction.

        Called first by :meth:`rollback`, which drains the repositories'
        event buffers and starts a new scope afterwards, even if this hook
        raises. It also runs when a scope exits without committing, and
        again on exit after an explicit ``rollback()``, so keep it
        idempotent.

        Raises:
            CQRSError: if the underlying storage rollback fails.
        """
        return None

    async def _write_outbox(self) -> None:
        """Persist integration events to the outbox.

        Runs inside the transaction, after ``_collect_and_stamp`` and
        before ``_commit``, so anything written here commits atomically
        with the aggregate state change.

        When an ``OutboxWriter`` was supplied to the constructor, the
        stamped domain events from ``collect_events()`` are translated
        and appended here — no override needed::

            uow = OrderUoW(session_factory, outbox_writer=writer)

        Without a writer this is a no-op, and anything relying on the
        outbox will silently find it empty.

        Override to customise — for example to write through a different
        mechanism, or to publish only under certain conditions. An
        override can still delegate here with
        ``await super()._write_outbox()``.

        This hook persists *integration* events, not domain events —
        domain events are dispatched in-process by ``EventBus`` after
        commit.

        Raises:
            CQRSError: if persisting outbox events to storage fails.
        """
        if self._outbox_writer is None:
            return None
        await self._outbox_writer.write(self.collect_events())

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
