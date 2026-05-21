"""Pipeline behavior types for the CQRS message bus.

This module defines the protocol types and helpers that compose the
pipeline (onion) middleware layer used by ``CommandBus`` and
``QueryBus``.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydomain.cqrs.idempotency import MISSING, ProcessedCommandStore
from pydomain.cqrs.locking import LockKeyResolver, LockProvider
from pydomain.ddd.domain_event import DomainEvent


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
        ctx.handler = self._handler

        async def terminal() -> Any:
            if ctx.kind == MessageKind.COMMAND:
                return await self._handler(message, ctx.uow)
            return await self._handler(message)

        chain: Callable[[], Any] = terminal
        for behavior in reversed(self._behaviors):
            prev = chain
            chain = self._wrap(behavior, prev, ctx)
        return await chain()

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


class AggregateLockingBehavior:
    """Pipeline behavior that acquires and releases locks for aggregate access.

    Lock keys are resolved from the message via a ``LockKeyResolver`` and
    acquired in **sorted** order to prevent deadlocks. Locks are released
    in reverse order inside a ``finally`` block to guarantee cleanup even
    when the handler raises an exception.

    If the resolver returns an empty list, the behavior skips locking and
    calls ``next()`` directly.

    Parameters
    ----------
    provider:
        The lock provider used to acquire/release named locks.
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

        acquired: list[str] = []
        try:
            for key in keys:
                await self._provider.acquire(key)
                acquired.append(key)
        except Exception:
            for key in reversed(acquired):
                await self._provider.release(key)
            raise

        try:
            return await next()
        finally:
            for key in reversed(keys):
                await self._provider.release(key)


# ── Idempotency ────────────────────────────────────────────────────────────


class IdempotencyBehavior:
    """Pipeline behavior that caches and returns results for duplicate commands.

    Checks the :class:`~pydomain.cqrs.idempotency.ProcessedCommandStore`
    before delegating to the inner handler.  If the command has already
    been processed the cached result is returned immediately — the inner
    handler is never called.

    Pipeline slot 3: after :class:`ValidationBehavior` (slot 2) and before
    :class:`AggregateLockingBehavior` (slot 4), avoiding wasted lock work
    on already-processed commands.

    If ``command_id`` is not present in ``ctx.metadata`` the behavior
    passes through to ``next()`` without consulting the store (allowing
    non-command messages to flow through the same pipeline).
    """

    def __init__(self, store: ProcessedCommandStore) -> None:
        self._store = store

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        command_id: UUID | None = ctx.metadata.get("command_id")
        if command_id is None:
            return await next()

        cached = await self._store.get(command_id)
        if cached is not MISSING:
            return cached

        result = await next()
        await self._store.set(command_id, result)
        return result
