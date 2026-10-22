"""Unit of Work protocol and abstract base class for managing transactional scope.

The ``UnitOfWork`` protocol defines the contract for managing
transactional boundaries and collecting domain events produced
during a unit of work.

``AbstractUnitOfWork`` provides a reusable ABC implementation with
a commit/rollback lifecycle, correlation/causation ID stamping of
domain events, and extension hooks for subclasses.
"""

from __future__ import annotations

import asyncio
from abc import ABC
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, Protocol, runtime_checkable
from uuid import UUID

from pydomain.cqrs.exceptions import CQRSError, UnitOfWorkAlreadyCommittedError
from pydomain.cqrs.outbox import OutboxWriter
from pydomain.ddd.domain_event import DomainEvent


class _ShieldedCallCancelledError(CQRSError):
    """A shielded call raised ``CancelledError`` while nothing cancelled its caller.

    That ``CancelledError`` is the call's own failure, not a cancellation of
    the caller (B-126, B-130); it is this error's ``__cause__``.
    """


async def _shielded[T](call: Callable[[], Awaitable[T]]) -> T:
    """Run *call* to completion, even when the calling task is cancelled.

    The call runs inside ``anyio.CancelScope(shield=True)``, which absorbs the
    cancellation of an anyio cancel scope (and, under trio, any cancellation)
    inline, without spinning. Under asyncio a native ``task.cancel()`` is not
    stopped by that scope, so the call also runs in its own task, built with
    the caller's context and never started eagerly; the caller waits on a
    private future that the task's done callback sets, absorbing its own
    cancellations until the call ends (ADR-079 point 6, B-122).

    Once the call ended:

    - when the caller was cancelled meanwhile, natively, the first
      ``CancelledError`` is re-raised; if the call raised too, the
      cancellation carries a note naming its error's type only (ADR-078).
      A cancellation already pending at entry is kept the same way (B-132).
      An anyio scope's cancellation is not re-raised: anyio delivers it again
      at the next checkpoint;
    - otherwise the call's result is returned, or its error raised, except
      that its own ``CancelledError`` becomes a
      :class:`_ShieldedCallCancelledError` raised from it (B-130).

    With no async library running (a coroutine driven by hand), the call runs
    inline, without the scope (B-131).
    """
    # Imported here, not at module level, so that ``import pydomain`` keeps
    # loading only pydantic and uuid-utils while the package loads every layer
    # eagerly (DDD-17). anyio is still a required dependency (B-122).
    import anyio

    try:
        scope = anyio.CancelScope(shield=True)
    except RuntimeError:  # no async library detected (B-131)
        return await call()
    try:
        caller = asyncio.current_task()
    except RuntimeError:  # pragma: no cover - trio is not a dev dependency (B-131)
        # Another anyio backend: the scope absorbs every cancellation inline.
        with scope:
            result = await call()
        return result
    if caller is None:  # driven by hand from a callback of a running loop
        return await call()
    with scope:
        inner, cancellation = await _wait_for_own_task(call, caller)
    return _outcome(inner, cancellation)


async def _wait_for_own_task[T](
    call: Callable[[], Awaitable[T]], caller: asyncio.Task[Any]
) -> tuple[asyncio.Task[T], asyncio.CancelledError | None]:
    """Run *call* in a task of its own; wait until it ends, whatever cancels us.

    The task is built with the constructor and the caller's context, so
    context variables stay visible both ways (B-119), and it is never started
    eagerly: under ``asyncio.eager_task_factory`` an eager start would enter
    the caller's context while the caller runs in it. Return the ended task
    and the first cancellation of the caller, if any.
    """

    async def run() -> T:
        return await call()

    loop = asyncio.get_running_loop()
    inner = asyncio.Task(run(), loop=loop, context=caller.get_context())
    cancellation: asyncio.CancelledError | None = None
    while not inner.done():
        # A private future, not asyncio.shield: on Python 3.14 the latter logs
        # the call's error, message and traceback included, when the caller
        # was cancelled (ADR-071).
        waiter: asyncio.Future[None] = loop.create_future()

        def wake(_: asyncio.Task[T], waiter: asyncio.Future[None] = waiter) -> None:
            if not waiter.done():  # a waiter the caller's cancellation cancelled
                waiter.set_result(None)

        inner.add_done_callback(wake)
        try:
            await waiter
        except asyncio.CancelledError as cancelled:
            # Only a cancellation of the caller cancels the private waiter.
            if cancellation is None:
                cancellation = cancelled
    return inner, cancellation


def _outcome[T](
    inner: asyncio.Task[T], cancellation: asyncio.CancelledError | None
) -> T:
    """Return the ended call's result, or raise what the caller must see."""
    if cancellation is None:
        try:
            return inner.result()
        except asyncio.CancelledError as own:
            raise _ShieldedCallCancelledError(
                "a shielded call raised CancelledError, but nothing cancelled "
                "the calling task"
            ) from own
    try:
        inner.result()
    except BaseException as failure:
        # The call's error becomes the cancellation's __context__ (ADR-078).
        cancellation.add_note(f"clean-up also failed: {type(failure).__name__}")
        raise cancellation
    raise cancellation


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

    A cancellation of the calling task should not interrupt the storage
    commit or rollback once it started: ``AbstractUnitOfWork`` shields both
    (ADR-079). An implementation of this Protocol alone shields its own.
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

    **Cancellation (ADR-079).** ``commit()`` runs its ``_commit()`` hook, and
    ``rollback()`` its ``_rollback()`` hook, to completion when the calling
    task is cancelled while they run: the cancellation propagates once the
    hook ended. A ``_commit()`` that completed marks the scope committed
    first, so leaving it drains instead of rolling back. ``_flush()`` and
    ``_write_outbox()`` are not shielded: a cancellation there rolls back.
    Under asyncio a shielded hook runs in a task of its own, in the caller's
    context (context variables stay visible both ways); under an anyio cancel
    scope or trio it runs inline in a shielded scope; with no async library
    running it runs inline. A ``CancelledError`` that a hook raises itself,
    while nothing cancels the caller, is that hook's failure: a private
    ``CQRSError`` raised from it. A hook that fails while the caller is being
    cancelled becomes a type-only note on the cancellation,
    ``clean-up also failed: <Type>``.

    Storage tied to the task that opened it, for example SQLAlchemy's
    ``async_scoped_session(scopefunc=current_task)``, sees another task in a
    shielded hook and silently loses the writes. Such a unit of work sets
    ``shield_hooks = False``::

        class TaskScopedUoW(AbstractUnitOfWork):
            shield_hooks = False  # hooks run inline in the calling task

    Its hooks then run inline, with no shield of either kind, and a
    cancellation can interrupt them. A unit of work that implements only the
    :class:`UnitOfWork` Protocol is not covered: it should shield its own
    storage commit and rollback.
    """

    shield_hooks: ClassVar[bool] = True
    """Run ``_commit()`` and ``_rollback()`` shielded from cancellation.

    ``False`` runs them inline in the calling task, cancellable, for storage
    keyed by the current task (B-115, B-119).
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
        4. ``_commit`` — commit the database transaction (overridable no-op),
           shielded from cancellation unless ``shield_hooks`` is ``False``.
        5. Mark as committed.

        A scope commits once: a second call raises before any hook runs.
        After a successful first commit its changes stay persisted and the
        scope stays committed, so leaving it does not roll back. After a
        first commit that failed part-way, the retry raises too: call
        ``rollback()`` (or enter the unit of work again) to start a new
        scope, and run the work again.

        A cancellation during ``_flush`` or ``_write_outbox`` stops the
        commit; the scope is not committed. Once ``_commit`` starts, a
        cancellation (also one requested earlier and not delivered yet)
        waits for it: when it completes, the scope is marked committed and
        the ``CancelledError`` is raised afterwards (ADR-079).

        Raises:
            UnitOfWorkAlreadyCommittedError: if ``commit()`` was already
                called in this scope. The message says whether the first
                commit succeeded.
            CQRSError: if ``_flush``, ``_write_outbox``, ``_commit``,
                or any hook in the chain raises a storage or persistence error;
                also when ``_commit`` raises ``CancelledError`` itself while
                nothing cancels the caller (that error is the ``__cause__``).
            CancelledError: the caller's cancellation, after ``_commit``
                ended; if ``_commit`` failed meanwhile, with the note
                ``clean-up also failed: <Type>``.
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

        async def commit_and_mark() -> None:
            await self._commit()
            self._committed = True

        # The point of no return (B-132): a cancellation, even one pending
        # since before this line, now waits for _commit to end.
        await self._run_hook(commit_and_mark)

    async def _run_hook(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Run a storage hook shielded, or inline if ``shield_hooks`` is off."""
        if self.shield_hooks:
            await _shielded(hook)
        else:
            await hook()

    async def rollback(self) -> None:
        """Roll back the current unit of work and start a new scope.

        1. ``_rollback`` — roll back the storage transaction (overridable
           no-op).
        2. Drain every repository's event buffer through ``pull_events()``
           and discard the events, so a later ``commit()`` never collects
           them, even from a repository that outlives this unit of work.
        3. Clear the collected events and the commit record.

        Steps 2 and 3 run even when ``_rollback`` or a repository's
        ``pull_events()`` raises; the error is then re-raised. When
        ``_rollback`` raised, its error is the one re-raised, and a drain
        error becomes a note on it naming the drain error's type only
        (``draining the event buffers also failed: <type>``); otherwise the
        first ``pull_events()`` error is re-raised (ADR-078). Calling ``rollback()``
        again drains nothing new; ``_rollback`` runs again, so it should be
        idempotent.

        ``_rollback`` is shielded from cancellation unless ``shield_hooks``
        is ``False``: a cancellation while it runs waits for it, and the
        ``CancelledError`` is raised after steps 2 and 3 (ADR-079).

        Override :meth:`_rollback`, not this method, to roll back storage.

        Raises:
            CQRSError: if the underlying storage rollback fails, or raises
                ``CancelledError`` itself while nothing cancels the caller.
            CancelledError: the caller's cancellation, once the rollback
                ended.
        """
        try:
            await self._run_hook(self._rollback)
        except BaseException as storage_error:
            # The storage error wins; a drain error becomes a type-only note
            # on it (ADR-078, B-88).
            try:
                self._discard_staged_events()
            except Exception as drain_error:
                storage_error.add_note(
                    "draining the event buffers also failed: "
                    f"{type(drain_error).__name__}"
                )
            finally:
                self._start_scope()
            raise
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

        Runs to completion when the caller is cancelled meanwhile; under
        asyncio in a task of its own, in the caller's context. Storage keyed
        by the current task needs ``shield_hooks = False``.

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

        Runs to completion when the caller is cancelled meanwhile; under
        asyncio in a task of its own, in the caller's context. Storage keyed
        by the current task needs ``shield_hooks = False``.

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
