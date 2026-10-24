"""Pipeline behavior types for the CQRS message bus.

This module defines the protocol types and helpers that compose the
pipeline (onion) middleware layer used by ``CommandBus`` and
``QueryBus``.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import time
import types
import warnings
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, NoReturn, Protocol, runtime_checkable
from uuid import NAMESPACE_URL, UUID, uuid5

from pydomain._describe import describe
from pydomain.cqrs.exceptions import (
    CommandInProgressError,
    EventIdempotencyMisuseError,
    EventInProgressError,
)
from pydomain.cqrs.idempotency import (
    MISSING,
    MessageReservationStore,
    ProcessedMessageStore,
)
from pydomain.cqrs.locking import LockKeyResolver, LockProvider
from pydomain.cqrs.unit_of_work import _shielded
from pydomain.ddd.domain_event import DomainEvent

_COMMITTED_MARK = "_committed_result"
"""The private attribute of a command's ``MessageContext`` that marks it committed.

The command pipeline's terminal step sets it, to the handler's result, once
the commit completed (ADR-079, "The committed mark"); only
``IdempotencyBehavior`` reads it.
"""


class MessageKind(Enum):
    """Distinguishes message categories in pipeline behaviors."""

    COMMAND = auto()
    EVENT = auto()
    QUERY = auto()


@dataclass(eq=False)
class MessageContext:
    """Mutable carrier that flows through the pipeline.

    Every behavior and the terminal handler receive the same context
    instance so behaviors can pass data downstream via ``metadata``.
    """

    message: Any
    handler: Callable[..., Any] | None = None
    kind: MessageKind = MessageKind.COMMAND
    uow: Any | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=lambda: {})
    new_events: list[DomainEvent] = field(default_factory=list)  # pyright: ignore[reportUnknownVariableType]


@runtime_checkable
class NextHandler(Protocol):
    async def __call__(self) -> Any: ...


@runtime_checkable
class PipelineBehavior(Protocol):
    """Protocol for pipeline behaviors.

    Behaviors wrap message handlers in a decorator (onion) pattern.
    Each behavior runs before and after calling ``next()``.

    In a command pipeline ``next()`` includes the commit (ADR-079): after
    it returns, the command committed and ``ctx.new_events`` holds the
    committed events, unless an inner behavior short-circuited, in which
    case nothing was committed and the scope rolls back on exit. After it
    raises, in general the handler or the commit failed and the rollback
    runs on exit; the table in ``concepts/cqrs/pipeline-behaviors.md``
    ("What next() means in a command pipeline") lists every case. Writes
    through ``ctx.uow`` after ``next()`` are not committed.
    """

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        """Run logic before and/or after calling ``next()``."""
        ...


# ── MessagePipeline ──────────────────────────────────────────────────────────


class MessagePipeline:
    """Composable pipeline that wraps a handler with behaviors.

    Each behavior runs before and after the handler in onion (decorator)
    order — the first behavior in the list is the outermost. Pipeline
    instances are constructed at registration time (once per message type)
    and reused across dispatches.

    Parameters
    ----------
    handler:
        The terminal handler callable. Receives the message passed to
        ``execute()``.
    behaviors:
        Ordered list of pipeline behaviors. ``None`` is equivalent to an
        empty list (no middleware).
    """

    def __init__(
        self,
        handler: Callable[..., Any],
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        self._handler = handler
        self._behaviors = behaviors or []

    async def execute(
        self,
        ctx: MessageContext,
        message: Any,
    ) -> Any:
        """Run the pipeline: outermost behavior ... handler(message, ctx.uow).

        The terminal invocation forwards both the *message* and the
        UoW from ``ctx.uow``.  Only command handlers (``MessageKind.COMMAND``)
        receive the transaction-scoped Unit of Work as a second parameter;
        query and event handlers are invoked with a single argument.
        """
        return await self._run(ctx, self._terminal(ctx, message))

    async def _run(self, ctx: MessageContext, terminal: Callable[[], Any]) -> Any:
        """Wrap *terminal* in the behaviors, outermost first, and await it."""
        ctx.handler = self._handler
        chain: Callable[[], Any] = terminal
        for behavior in reversed(self._behaviors):
            prev = chain
            chain = self._wrap(behavior, prev, ctx)
        return await chain()

    def _terminal(self, ctx: MessageContext, message: Any) -> Callable[[], Any]:
        """Return the innermost step of the chain, which calls the handler."""

        async def terminal() -> Any:
            if ctx.kind == MessageKind.COMMAND:
                return await self._handler(message, ctx.uow)
            return await self._handler(message)

        return terminal

    @staticmethod
    def _wrap(
        behavior: PipelineBehavior,
        next_handler: Callable[[], Any],
        ctx: MessageContext,
    ) -> Callable[[], Any]:
        """Wrap a single behavior around the next handler in the chain."""

        async def wrapper() -> Any:
            return await behavior.handle(ctx, next_handler)

        return wrapper


class LoggingBehavior:
    """Pipeline behavior that logs entry, success, and failure of message handling.

    Uses ``logging.getLogger("pydomain.pipeline")`` as its logger.
    Measures wall-clock duration via ``time.perf_counter()``.

    Parameters
    ----------
    payload_formatter:
        Optional callable that receives the message and returns a dict
        attached as ``extra={"payload": ...}`` on the entry log record.
    """

    def __init__(
        self,
        payload_formatter: Callable[[Any], dict[str, Any]] | None = None,
    ) -> None:
        self._logger = logging.getLogger("pydomain.pipeline")
        self._payload_formatter = payload_formatter

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        kind_name = ctx.kind.name
        type_name = type(ctx.message).__name__
        handler_name = (
            ctx.handler.__name__
            if ctx.handler is not None and hasattr(ctx.handler, "__name__")
            else str(ctx.handler)
        )

        correlation = ctx.correlation_id or "N/A"
        causation = ctx.causation_id or "N/A"
        command_id = ctx.metadata.get("command_id", "N/A")

        extra: dict[str, Any] | None = None
        if self._payload_formatter is not None:
            extra = {"payload": self._payload_formatter(ctx.message)}

        self._logger.info(
            "Processing %s %s with %s [correlation=%s, causation=%s, command=%s]",
            kind_name,
            type_name,
            handler_name,
            correlation,
            causation,
            command_id,
            extra=extra,
        )

        start = time.perf_counter()
        try:
            result = await next()
        except Exception as exc:
            duration = (time.perf_counter() - start) * 1000
            self._logger.error(
                "Failed %s %s after %.2fms: %s",
                kind_name,
                type_name,
                duration,
                type(exc).__name__,
            )
            raise
        else:
            duration = (time.perf_counter() - start) * 1000
            self._logger.info(
                "Completed %s %s in %.2fms",
                kind_name,
                type_name,
                duration,
            )
            return result


class ValidationBehavior:
    """Pipeline behavior that runs registered validators before the handler.

    Validators are registered per message type via ``register()`` and
    executed in registration order before calling the next behavior or
    terminal handler. If any validator raises, the remaining validators
    and the handler are skipped — the exception propagates upstream.

    Supports both synchronous validators and async validators (coroutines,
    Futures, or any awaitable).
    """

    def __init__(
        self,
        validators: dict[type, list[Callable[..., Any]]] | None = None,
    ) -> None:
        self._validators: dict[type, list[Callable[..., Any]]] = (
            dict(validators) if validators else {}
        )

    def register(self, message_type: type, validator: Callable[..., Any]) -> None:
        """Register a validator callable for the given message type.

        Validators are appended to the per-type list and run in
        registration order.
        """
        self._validators.setdefault(message_type, []).append(validator)

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        validators = self._validators.get(type(ctx.message))  # pyright: ignore[reportUnknownArgumentType]
        if validators is not None:
            for validator in validators:
                result = validator(ctx.message)
                if inspect.isawaitable(result):
                    await result
        return await next()


_pipeline_logger = logging.getLogger("pydomain.pipeline")

_HANDED_OVER: dict[asyncio.Task[list[BaseException]], str] = {}
"""Background release tasks still running, with the message type to log.

The strong reference keeps each task alive until it ends (B-51).
"""


async def _release_each(provider: LockProvider, keys: list[str]) -> list[BaseException]:
    """Release *keys* in the order given; return the failures instead of raising.

    Every key gets its ``release()`` call, whatever an earlier one raised
    (ADR-076). Returning the failures keeps their messages out of asyncio's
    own log of a task nobody waits for.
    """
    failures: list[BaseException] = []
    for key in keys:
        try:
            await provider.release(key)
        except BaseException as failure:
            failures.append(failure)
    return failures


def _hand_over(provider: LockProvider, keys: list[str], message_type: str) -> None:
    """Release *keys* in a background task the caller's cancellation cannot reach."""
    task = asyncio.ensure_future(_release_each(provider, keys))
    _HANDED_OVER[task] = message_type
    task.add_done_callback(_handed_over_done)


def _handed_over_done(task: asyncio.Task[list[BaseException]]) -> None:
    """Drop the reference; log the task's failed releases by type only."""
    message_type = _HANDED_OVER.pop(task)
    # A task cancelled before its first step (at loop shutdown) released nothing.
    failures = [asyncio.CancelledError()] if task.cancelled() else task.result()
    for failure in failures:
        _pipeline_logger.warning(
            "Abandoned lock release for %s failed with %s",
            message_type,
            type(failure).__name__,
        )


def _current_asyncio_task() -> asyncio.Task[Any] | None:
    """Return the running asyncio task, or ``None`` under another backend."""
    try:
        return asyncio.current_task()
    except RuntimeError:  # no running asyncio event loop (trio)
        return None


def _cancellations(task: asyncio.Task[Any] | None) -> int:
    """Return the cancellations requested of *task* and not yet withdrawn."""
    return 0 if task is None else task.cancelling()


def _note_failures(error: BaseException, failures: list[BaseException]) -> None:
    """Add a type-only note to *error* for each failure other than itself."""
    for failure in failures:
        if failure is not error:
            error.add_note(f"clean-up also failed: {type(failure).__name__}")


def _raise_first_fatal(failures: list[BaseException]) -> None:
    """Raise the first failure that is not an ``Exception``, noting the others."""
    for failure in failures:
        if not isinstance(failure, Exception):
            _note_failures(failure, failures)
            raise failure


class AggregateLockingBehavior:
    """Pipeline behavior that acquires and releases locks for aggregate access.

    Lock keys are resolved from the message via a ``LockKeyResolver`` and
    acquired in **sorted** order to prevent deadlocks (ADR-019). Every key
    whose ``acquire()`` returned is released, in reverse order, however the
    command ends: after the handler returns, or after any
    ``BaseException`` from an ``acquire()`` or the handler, a cancellation
    included (ADR-076). In a command pipeline the commit runs inside
    ``next()``, so the locks are held through it (ADR-079); a provider's
    lease or TTL must cover the commit as well as the handler.

    The releases run inline, in the task that acquired the locks, so a
    provider whose locks belong to that task works. Each ``release()`` is
    isolated, so one that raises does not skip the others. When a
    cancellation of the task arrives while a release runs (a second
    ``cancel()``, an anyio cancel scope), the interrupted key and the keys
    after it move to one background task that the cancellation cannot
    reach, and the cancellation propagates at once (B-51), also when the
    provider converts the cancellation into its own error or swallows it.
    The background task calls the interrupted release again, so
    ``release()`` must be ownership-safe (see
    :meth:`~pydomain.cqrs.locking.LockProvider.release`). A task-owned
    provider whose ``release()`` awaits refuses those background releases.
    Cancellation detection and the hand-over need asyncio: with no asyncio
    task running (another anyio backend, such as trio) every release runs
    inline, with no hand-over (B-69). A failed release surfaces by its type
    only, never the lock key (ADR-071):

    - after a successful handler and commit, as a WARNING on
      ``pydomain.pipeline``; the result is still returned: the commit has
      already happened;
    - after a failed handler, commit or ``acquire()``, as a note
      ``clean-up also failed: <Type>`` on the error that propagates;
    - a release that raises a ``BaseException`` that is not an
      ``Exception`` (a cancellation) is re-raised once every release ran,
      with a note for each of the other failed releases;
    - a failed release before the cancellation is a note on the
      ``CancelledError``; the background task logs each of its failures as
      a WARNING, and logs ``CancelledError`` if it is cancelled at
      shutdown. A loop closed without cancelling its tasks
      (``loop.close()`` after ``run_until_complete``) leaves a waiting
      background task referenced; ``asyncio.run`` and anyio cancel it.

    Locks are not reentrant: a handler must not dispatch a nested command
    whose keys include one it holds. ``FakeLockProvider`` raises
    :class:`~pydomain.cqrs.exceptions.LockReentryError` when the same task
    acquires a key again; a nested dispatch in a new task deadlocks.

    If the resolver returns an empty list, the behavior skips locking and
    calls ``next()`` directly.

    Parameters
    ----------
    provider:
        The lock provider used to acquire/release named locks. ``release()``
        runs in the task that called ``acquire()``, except for the keys
        handed to the background task after a cancellation.
    resolver:
        Resolves lock keys from the incoming message.
    """

    def __init__(self, provider: LockProvider, resolver: LockKeyResolver) -> None:
        self._provider = provider
        self._resolver = resolver

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        keys = list(dict.fromkeys(sorted(self._resolver.resolve(ctx.message))))
        if not keys:
            return await next()

        message_type = type(ctx.message).__name__
        acquired: list[str] = []
        try:
            for key in keys:
                await self._provider.acquire(key)
                acquired.append(key)
            result = await next()
        except BaseException as error:
            failures = await self._release(acquired, message_type)
            _raise_first_fatal(failures)
            _note_failures(error, failures)
            raise

        failures = await self._release(acquired, message_type)
        _raise_first_fatal(failures)
        for failure in failures:
            _pipeline_logger.warning(
                "Lock release failed for %s: %s", message_type, describe(failure)
            )
        return result

    async def _release(self, keys: list[str], message_type: str) -> list[BaseException]:
        """Release *keys* in reverse order, in this task; return the failures.

        When this task is cancelled while a release runs, the interrupted key
        and the ones after it go to a background task and ``CancelledError``
        propagates at once (B-51). That holds when the provider re-raises the
        cancellation, converts it into its own error or swallows it: after
        each release the task's cancellation count is checked. A
        ``CancelledError`` from a release while nothing cancels this task is
        that release's failure (B-50). Without an asyncio task (another
        backend) the releases run inline with no hand-over (B-69).
        """
        task = _current_asyncio_task()
        pending = keys[::-1]
        failures: list[BaseException] = []
        while pending:
            requested = _cancellations(task)
            try:
                await self._provider.release(pending[0])
            except asyncio.CancelledError as cancelled:
                # Delivered by a cancellation of this task, including one
                # requested before the releases began (still counted).
                if _cancellations(task) > 0:
                    self._hand_over_and_raise(
                        pending, failures, message_type, cancelled
                    )
                failures.append(cancelled)
            except BaseException as failure:
                if _cancellations(task) > requested:  # a converted cancellation
                    self._hand_over_and_raise(pending, failures, message_type)
                failures.append(failure)
            else:
                if _cancellations(task) > requested:  # a swallowed cancellation
                    self._hand_over_and_raise(pending[1:], failures, message_type)
            del pending[0]
        return failures

    def _hand_over_and_raise(
        self,
        keys: list[str],
        failures: list[BaseException],
        message_type: str,
        cancelled: asyncio.CancelledError | None = None,
    ) -> NoReturn:
        """Hand *keys* to a background task; raise the cancellation with notes."""
        _hand_over(self._provider, keys, message_type)
        error = cancelled if cancelled is not None else asyncio.CancelledError()
        _note_failures(error, failures)
        raise error


# ── Idempotency ────────────────────────────────────────────────────────────


class IdempotencyBehavior:
    """Pipeline behavior that caches and returns results for duplicate commands.

    Checks the :class:`~pydomain.cqrs.idempotency.ProcessedMessageStore`
    before delegating to the inner handler.  If the command has already
    been processed the cached result is returned immediately — the inner
    handler is never called, and nothing is committed.

    Otherwise the result is recorded with ``set()`` after ``next()``
    returns, which in a command pipeline is after the commit (ADR-079):
    a command whose commit fails is not recorded, and its retry runs the
    handler again. A ``set()`` that raises an ``Exception`` is logged as a
    WARNING on ``pydomain.pipeline`` naming its type only (ADR-071), and
    the result is still returned. Recording is at-least-once: a crash
    between the commit and the record, a failed record, or a ``_commit()``
    that raises after the storage commit took effect runs the handler
    again on retry.

    A command whose commit completed is recorded even when ``next()``
    then raises any ``BaseException`` but ``GeneratorExit``: a cancellation
    that arrived during the commit (which finishes first), during a lock
    release after it, a handler that called ``commit()`` itself, or a
    behavior that calls ``next()`` a second time (``PipelineReentryError``).
    The behavior records the result the terminal step marked, then
    re-raises, so a retry returns it instead of running the committed work
    again (ADR-079). A ``set()`` that raises there becomes a type-only note
    on the re-raised error, ``clean-up also failed: <Type>``; a cancellation
    of the record itself propagates. A dispatch closed while suspended
    (``GeneratorExit``, as when a pending task is garbage-collected) is not
    recorded: a closing coroutine cannot await the store.

    When the store also implements
    :class:`~pydomain.cqrs.idempotency.MessageReservationStore`, the
    behavior reserves the command after a ``get()`` miss, before the
    handler runs (ADR-079 point 4), so one ``command_id`` runs at most once
    at a time, with or without lock keys:

    - with no token, it calls ``get()`` again and returns a result that
      completed in between; otherwise it raises
      :class:`~pydomain.cqrs.exceptions.CommandInProgressError`, naming
      the command type only, and nothing of the duplicate runs or commits;
    - a command that raises before its commit (the handler, the commit, a
      cancellation) releases its reservation with its token, so a retry
      runs; a ``release()`` that raises an ``Exception`` is a type-only
      note on the error, and the id stays reserved until the store's TTL;
    - a committed command completes the reservation with ``set()``; a
      failed ``set()`` keeps it until the TTL, so an immediate duplicate
      is still refused (B-129);
    - a dispatch closed while suspended (``GeneratorExit``) awaits
      nothing, so its reservation stays until the TTL.

    With a store that implements only ``ProcessedMessageStore``, the
    behavior keeps the ``get``/``set`` flow, and two concurrent dispatches
    of one ``command_id`` can both run the handler.

    Every ``reserve()``, ``release()`` and ``set()`` after a commit runs to
    completion when the task is cancelled meanwhile, through the unit of
    work's cancellation shield; under asyncio it runs in a task of its own,
    so the store must work from any task (see
    :class:`~pydomain.cqrs.idempotency.ProcessedMessageStore`). A token
    that ``reserve()`` returned while the caller was cancelled is released
    at once, then the cancellation propagates (B-125).

    Pipeline slot 3: after :class:`ValidationBehavior` (slot 2) and before
    :class:`AggregateLockingBehavior` (slot 4), avoiding wasted lock work
    on already-processed commands.

    If ``command_id`` is not present in ``ctx.metadata`` the behavior
    passes through to ``next()`` without consulting the store (allowing
    non-command messages to flow through the same pipeline).
    """

    def __init__(self, store: ProcessedMessageStore) -> None:
        self._store = store
        self._reservations: MessageReservationStore | None = (
            store if isinstance(store, MessageReservationStore) else None
        )

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        command_id: UUID | None = ctx.metadata.get("command_id")
        if command_id is None:
            return await next()

        cached = await self._store.get(command_id)
        if cached is not MISSING:
            return cached

        held: tuple[MessageReservationStore, Hashable] | None = None
        if self._reservations is not None:
            token = await _reserve(self._reservations, command_id)
            if token is None:
                cached = await self._store.get(command_id)
                if cached is not MISSING:
                    return cached
                raise CommandInProgressError(
                    f"Command {type(ctx.message).__name__} is already in progress"
                )
            held = (self._reservations, token)

        try:
            result = await next()
        except GeneratorExit:
            # A suspended dispatch being closed cannot await the record or the
            # release; a reservation stays until the store's TTL frees it.
            raise
        except BaseException as error:
            committed = getattr(ctx, _COMMITTED_MARK, MISSING)
            if committed is MISSING:
                if held is not None:
                    await _release(held[0], command_id, held[1], error)
                raise
            # Committed, then something raised: record it, so a retry does not
            # run it again (B-121). A failed record is a type-only note on the
            # error (B-134); a cancellation of the record propagates.
            try:
                await _shielded(lambda: self._store.set(command_id, committed))
            except Exception as failure:
                error.add_note(f"clean-up also failed: {type(failure).__name__}")
            raise
        try:
            await _shielded(lambda: self._store.set(command_id, result))
        except Exception as failure:
            _pipeline_logger.warning(
                "Idempotency record failed for %s: %s",
                type(ctx.message).__name__,
                describe(failure),
            )
        return result


async def _reserve(
    reservations: MessageReservationStore, message_id: UUID
) -> Hashable | None:
    """Reserve *message_id* under the shield; return the token or ``None``.

    When the caller is cancelled while ``reserve`` runs, ``reserve`` still
    completes; a token it returned is released at once, then the
    cancellation propagates, so no reservation leaks (B-125).
    """
    taken: list[Hashable | None] = []

    async def reserve() -> Hashable | None:
        taken.append(await reservations.reserve(message_id))
        return taken[0]

    try:
        return await _shielded(reserve)
    except GeneratorExit:
        raise
    except BaseException as error:
        token = taken[0] if taken else None
        if token is not None:
            await _release(reservations, message_id, token, error)
        raise


async def _release(
    reservations: MessageReservationStore,
    message_id: UUID,
    token: Hashable,
    error: BaseException,
) -> None:
    """Release *token*'s reservation under the shield, while *error* propagates.

    A ``release`` that raises an ``Exception`` becomes a type-only note on
    *error* (ADR-071); the reservation then stays until the store's TTL. A
    cancellation of the release itself propagates.
    """
    try:
        await _shielded(lambda: reservations.release(message_id, token))
    except Exception as failure:
        error.add_note(f"clean-up also failed: {type(failure).__name__}")


_EVENT_KEY_NAMESPACE = uuid5(
    NAMESPACE_URL, "https://github.com/mgourlis/pydomain/event-idempotency-key"
)
"""The namespace of event keys, ``6122d529-5666-59f9-aeb0-cda93ddbb124``.

Fixed for good: stored keys outlive releases (ADR-080 point 3).
"""

_NO_HANDLER: Any = object()
"""The handler an ``EventIdempotencyBehavior`` has before its first dispatch."""


def _log_name(handler: Any) -> str:
    """Name *handler* in a log line by ``__qualname__`` (ADR-081, B-201).

    A ``functools.partial`` is unwrapped to its ``func``, recursively. Only
    a function, a method, a builtin or a class is named by its own
    ``__qualname__``; anything else (a callable instance, even one whose
    class defines ``__get__``) by its type's, since its ``__getattr__`` may
    answer with data. Never ``repr()`` or ``str()``, which an application
    may fill with data.
    """
    while isinstance(handler, functools.partial):
        handler = handler.func
    if _names_itself(handler):
        name: str = handler.__qualname__
        return name
    return type(handler).__qualname__


def _names_itself(handler: Any) -> bool:
    """Whether *handler*'s ``__qualname__`` is its own, not ``__getattr__``'s."""
    return (
        inspect.isfunction(handler)
        or inspect.ismethod(handler)
        or inspect.isbuiltin(handler)
        or isinstance(handler, type | types.MethodDescriptorType)
    )


def _handler_name(handler: Any) -> str:
    """Return *handler*'s ``module:qualname``; an instance's type gives it."""
    named = handler if hasattr(handler, "__qualname__") else type(handler)
    return f"{named.__module__}:{named.__qualname__}"


class EventIdempotencyBehavior:
    """Pipeline behavior that skips an event its consumer already handled.

    The key is ``uuid5(uuid5(EVENT_KEY_NAMESPACE, consumer),
    str(event.event_id))`` (ADR-080 point 3), a ``UUID``, so a store shared
    with :class:`IdempotencyBehavior` never mixes event keys with command
    ids. ``EVENT_KEY_NAMESPACE`` is the fixed value
    ``6122d529-5666-59f9-aeb0-cda93ddbb124``. Each event has its own
    ``event_id`` and each consumer its own name, so sibling events of one
    command and several handlers of one event type sharing a store each get
    a key of their own. An inbound event's ``event_id`` is derived from the
    integration event's by the ``InboundEventGateway``, so every redelivery
    of one message has one key. ``causation_id`` plays no part. In-process
    events follow the same rule (ADR-080 point 7): nothing in the library
    delivers one twice today, but one event object dispatched twice is
    handled once.

    The event is marked after its handler succeeds (ADR-080 point 4). When
    the store also implements
    :class:`~pydomain.cqrs.idempotency.MessageReservationStore`:

    - a ``get(key)`` hit skips the handler and logs at INFO on
      ``pydomain.pipeline``, naming the event type and its ``event_id``;
    - otherwise the behavior calls ``reserve(key)``. With no token it calls
      ``get(key)`` again and skips a delivery that completed in between;
      otherwise it raises
      :class:`~pydomain.cqrs.exceptions.EventInProgressError`, naming the
      event type only, and the handler does not run;
    - a handler that returns completes the reservation with
      ``set(key, True)``. A ``set`` that raises an ``Exception`` is a WARNING
      naming its type only (ADR-071); the reservation stays until the
      store's TTL, after which a redelivery runs again (B-164);
    - a handler that raises, a cancellation included, releases its token,
      and the error leaves the pipeline unchanged (ADR-080 point 6). A
      ``release`` that raises an ``Exception`` is a type-only note on the
      error, ``clean-up also failed: <Type>``, and the key stays reserved
      until the TTL;
    - a dispatch closed while suspended (``GeneratorExit``) awaits nothing,
      so its reservation stays until the TTL.

    The store calls that change state, ``reserve``, ``release`` and
    ``set``, and without reservations ``check_and_set`` (B-174), run to
    completion when the task is cancelled meanwhile, through the unit of
    work's cancellation shield, as for commands (ADR-079), so the store must
    work from any task. The reads, ``get`` and ``contains``, are not
    shielded: a cancelled read leaves nothing behind (B-175). A token
    that ``reserve`` returned while the caller was cancelled is released at
    once, then the cancellation propagates (B-125).

    With a store that implements only ``ProcessedMessageStore``, the behavior
    checks ``contains(key)``, runs the handler, then calls
    ``check_and_set(key)``. Two concurrent deliveries can then both run the
    handler. A ``check_and_set`` that raises an ``Exception`` is a type-only
    WARNING: the event stays unmarked, and a redelivery runs the handler
    again (B-165).

    Either way deduplication is at-least-once: the handler's work and the
    mark are not atomic, so a crash between them runs the handler again on
    redelivery.

    One instance serves one handler (B-159). It learns its handler at its
    first dispatch and raises
    :class:`~pydomain.cqrs.exceptions.EventIdempotencyMisuseError` at the
    first dispatch to a different one, before any store call. Handlers
    compare by identity, then with ``==`` (a bound method by its instance and
    function), so one function registered twice is one handler and is not
    detected, and neither are callable instances that compare equal (two
    equal ``@dataclass`` instances): they share the instance's keys (B-178).
    The check comes before the pass-through of a message that is not a
    :class:`~pydomain.ddd.domain_event.DomainEvent`, so one instance shared
    by any two pipelines, two ``QueryBus`` registrations included, raises
    too (B-179). Called directly with a context whose ``handler`` is
    ``None``, the behavior learns ``None`` as its handler (named
    ``builtins:NoneType`` without ``consumer``); a pipeline always sets it.

    Parameters
    ----------
    store:
        The store of marks, and of reservations when it implements
        ``MessageReservationStore``.
    consumer:
        A stable name for the handler, unique among the consumers sharing
        *store*. Renaming it changes every key. Optional until 0.5.0: left
        out, the constructor emits a ``DeprecationWarning`` once, and the
        behavior names the consumer by the handler's ``module:qualname``
        (a callable instance's type for an instance). Those names collide
        across instances: one function in two registrations, two lambdas in
        one scope, every ``functools.partial``, bound methods of two
        instances and two callable instances of one class.

    Example::

        store = MyProcessedMessageStore()  # implements ProcessedMessageStore
        bus = EventBus()
        bus.register(
            OrderReceived,
            my_handler,
            behaviors=[EventIdempotencyBehavior(store, consumer="order-projection")],
        )
    """

    def __init__(
        self, store: ProcessedMessageStore, *, consumer: str | None = None
    ) -> None:
        self._store = store
        self._reservations: MessageReservationStore | None = (
            store if isinstance(store, MessageReservationStore) else None
        )
        self._namespace: UUID | None = (
            None if consumer is None else uuid5(_EVENT_KEY_NAMESPACE, consumer)
        )
        self._handler: Any = _NO_HANDLER
        if consumer is None:
            warnings.warn(
                "EventIdempotencyBehavior without consumer= keys events on the "
                "handler's module and qualified name; pass consumer=, which "
                "pydomain 0.5.0 requires",
                DeprecationWarning,
                stacklevel=2,
            )

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        namespace = self._namespace_for(ctx.handler)
        event = ctx.message
        if not isinstance(event, DomainEvent):
            return await next()
        key = uuid5(namespace, str(event.event_id))
        if self._reservations is None:
            return await self._mark_after_success(event, key, next)

        if await self._store.get(key) is not MISSING:
            return _skip(event)
        token = await _reserve(self._reservations, key)
        if token is None:
            if await self._store.get(key) is not MISSING:
                return _skip(event)
            raise EventInProgressError(
                f"Event {type(event).__name__} is already in progress"
            )

        try:
            result = await next()
        except GeneratorExit:
            # A suspended dispatch being closed cannot await the release; the
            # reservation stays until the store's TTL frees it.
            raise
        except BaseException as error:
            await _release(self._reservations, key, token, error)
            raise
        await _record(event, lambda: _shielded(lambda: self._store.set(key, True)))
        return result

    def _namespace_for(self, handler: Any) -> UUID:
        """Learn the handler at the first dispatch; refuse a second one."""
        if self._handler is _NO_HANDLER:
            self._handler = handler
            if self._namespace is None:
                self._namespace = uuid5(_EVENT_KEY_NAMESPACE, _handler_name(handler))
        elif handler is not self._handler and handler != self._handler:
            raise EventIdempotencyMisuseError(
                "One EventIdempotencyBehavior instance serves one handler; "
                "build an instance for each registration"
            )
        assert self._namespace is not None
        return self._namespace

    async def _mark_after_success(
        self, event: DomainEvent, key: UUID, next: NextHandler
    ) -> Any:
        """The flow of a store without reservations: check, handle, mark."""
        if await self._store.contains(key):
            return _skip(event)
        result = await next()
        await _record(event, lambda: _shielded(lambda: self._store.check_and_set(key)))
        return result


def _skip(event: DomainEvent) -> None:
    """Log the skipped duplicate *event* by its type and ``event_id``."""
    _pipeline_logger.info(
        "Skipping duplicate event %s (event_id=%s)",
        type(event).__name__,
        event.event_id,
    )


async def _record(event: DomainEvent, call: Callable[[], Awaitable[Any]]) -> None:
    """Await the mark *call*; log an ``Exception`` from it by its type only."""
    try:
        await call()
    except Exception as failure:
        _pipeline_logger.warning(
            "Idempotency record failed for %s: %s",
            type(event).__name__,
            describe(failure),
        )
