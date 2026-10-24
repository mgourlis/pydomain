"""Event bus for the CQRS layer.

The ``EventBus`` routes domain events to their registered handlers.
Multiple handlers are supported per event type and run sequentially in
registration order with failure isolation: a handler failure is logged
and the remaining handlers continue (ADR-046). What happens next depends
on the path (ADR-081 point 1):

- ``dispatch(event)``, the direct path, raises ``EventHandlersFailed``
  once every handler has run, if any failed. The inbound gateway relies
  on it to NACK.
- ``dispatch_many(events)``, the post-command path, swallows the failures:
  the command has committed and its result is returned.
"""

from __future__ import annotations

import logging

from pydomain._describe import describe, describe_stack
from pydomain.cqrs.behaviors import (
    MessageContext,
    MessageKind,
    MessagePipeline,
    PipelineBehavior,
    _log_name,
)
from pydomain.cqrs.exceptions import EventHandlersFailed
from pydomain.cqrs.handlers import EventHandler
from pydomain.ddd.domain_event import DomainEvent

logger = logging.getLogger("pydomain.event_bus")


class EventBus:
    """Routes domain events to their registered handlers.

    Supports `N` handlers per event type.  Handlers are invoked
    sequentially in registration order.  If a handler raises an
    ``Exception``, it is logged and the remaining handlers continue
    uninterrupted. Afterwards ``dispatch`` raises ``EventHandlersFailed``
    holding every failure, and ``dispatch_many`` swallows them. A
    cancellation or another ``BaseException`` propagates at once, and the
    later handlers do not run.

    Use pipeline behaviors (such as
    :class:`~pydomain.cqrs.behaviors.EventIdempotencyBehavior`) to add
    cross-cutting concerns like idempotency at the handler level.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[MessagePipeline]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register[TEvent: DomainEvent](
        self,
        event_type: type[TEvent],
        handler: EventHandler[TEvent],
        behaviors: list[PipelineBehavior] | None = None,
    ) -> None:
        """Register an event handler.

        Multiple handlers can be registered for the same event type.
        Handlers are invoked in registration order.

        The handler is wrapped in a ``MessagePipeline`` at registration
        time.  If pipeline behaviors are provided, they are composed
        around the handler in onion order (first behavior is outermost).

        Parameters
        ----------
        event_type:
            The domain event class to handle.
        handler:
            An ``EventHandler`` that receives a domain event instance.
            Event handlers return ``None`` (fire-and-forget).
        behaviors:
            Optional list of pipeline behaviors that wrap the handler.
        """
        self._handlers.setdefault(event_type, []).append(
            MessagePipeline(handler=handler, behaviors=behaviors),
        )

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, event: DomainEvent) -> None:
        """Dispatch a single event to its registered handlers.

        Every handler runs, even when an earlier one failed.

        Parameters
        ----------
        event:
            The domain event to dispatch.

        Raises
        ------
        EventHandlersFailed
            Once every handler has run, if any of them raised an
            ``Exception``; it holds each handler's own exception in
            registration order.
        """
        failures = await self._execute(event)
        if failures:
            raise EventHandlersFailed(type(event).__name__, failures)

    async def dispatch_many(self, events: list[DomainEvent]) -> None:
        """Dispatch events sequentially, one at a time.

        Event N+1 handlers run only after ALL handlers for event N finish.
        No concurrency — events are dispatched in strict sequence.

        This is the post-command path: the command has committed, so a
        handler failure is logged and swallowed, never raised.

        Parameters
        ----------
        events:
            The domain events to dispatch.
        """
        for event in events:
            await self._execute(event)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _execute(self, event: DomainEvent) -> list[Exception]:
        """Run every handler of *event*; log and return each failure."""
        failures: list[Exception] = []
        # A snapshot: a handler registered while this dispatch runs takes part
        # from the next one (ADR-081 point 7).
        pipelines = tuple(self._handlers.get(type(event), ()))
        for pipeline in pipelines:
            ctx = MessageContext(
                message=event,
                kind=MessageKind.EVENT,
                uow=None,
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                metadata={},
            )
            try:
                await pipeline.execute(ctx, event)
            except Exception as exc:
                # A type-only line and a value-free stack, never exc_info:
                # the exception's text may quote the event (ADR-081 point 4).
                logger.error(
                    "Event handler %s failed for %s: %s\n%s",
                    _log_name(ctx.handler),
                    type(event).__name__,
                    describe(exc),
                    describe_stack(exc),
                )
                failures.append(exc)
        return failures
