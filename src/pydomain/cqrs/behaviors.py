"""Pipeline behavior types for the CQRS message bus.

This module defines the protocol types and helpers that compose the
pipeline (onion) middleware layer used by ``CommandBus`` and,
in the future, ``QueryBus``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydomain.ddd.domain_event import DomainEvent


class MessageKind(Enum):
    """Distinguishes message categories in pipeline behaviors."""

    COMMAND = auto()
    EVENT = auto()
    QUERY = auto()


@runtime_checkable
class UnitOfWork(Protocol):
    """Protocol for Unit of Work implementations.

    The UoW manages transactional scope and domain event collection.
    """

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    def collect_events(self) -> list[DomainEvent]: ...


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
