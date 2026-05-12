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
    handler: Callable[..., Any]
    kind: MessageKind = MessageKind.COMMAND
    uow: Any | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    metadata: dict[str, Any] = field(default_factory=lambda: {})
    new_events: list[DomainEvent] = field(default_factory=list)


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


# ── Pipeline helpers ──────────────────────────────────────────────────────
# These are package-internal shared helpers consumed by CommandBus (and
# future QueryBus). The ``_`` prefix means "not part of the public API,"
# not "module-private." They are deliberately excluded from __all__.


def _stamp_events(
    events: list[DomainEvent],
    *,
    correlation_id: UUID,
    causation_id: UUID,
) -> list[DomainEvent]:
    """Return stamped copies of domain events with tracing IDs."""
    return [
        e.stamp(correlation_id=correlation_id, causation_id=causation_id)
        for e in events
    ]


async def _run_pipeline(
    behaviors: list[PipelineBehavior],
    ctx: MessageContext,
    terminal: Callable[[], Any],
) -> Any:
    """Execute the pipeline chain: outermost behavior ... terminal handler."""
    chain: Callable[[], Any] = terminal
    for behavior in reversed(behaviors):
        prev = chain
        chain = _wrap_behavior(behavior, prev, ctx)
    return await chain()


def _wrap_behavior(
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

    def __init__(self, payload_formatter: Callable[[Any], dict] | None = None) -> None:
        self._logger = logging.getLogger("pydomain.pipeline")
        self._payload_formatter = payload_formatter

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        kind_name = ctx.kind.name
        type_name = type(ctx.message).__name__
        handler_name = (
            ctx.handler.__name__
            if hasattr(ctx.handler, "__name__")
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

    def __init__(self, validators: dict[type, list[Callable]] | None = None) -> None:
        self._validators: dict[type, list[Callable]] = (
            dict(validators) if validators else {}
        )

    def register(self, message_type: type, validator: Callable) -> None:
        """Register a validator callable for the given message type.

        Validators are appended to the per-type list and run in
        registration order.
        """
        self._validators.setdefault(message_type, []).append(validator)

    async def handle(self, ctx: MessageContext, next: NextHandler) -> Any:
        validators = self._validators.get(type(ctx.message))
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
