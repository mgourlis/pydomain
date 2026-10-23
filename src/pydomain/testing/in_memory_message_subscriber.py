"""In-memory message subscriber for testing.

Provides an in-memory implementation of the ``MessageSubscriber`` protocol
that records subscriptions and allows tests to simulate incoming messages
via ``simulate_message()``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class InMemoryMessageSubscriber:
    """In-memory ``MessageSubscriber`` for testing.

    Records topic subscriptions and provides ``simulate_message()`` to
    manually inject messages as if they arrived from an external broker.
    ``start()`` and ``stop()`` set the ``started`` and ``stopped`` flags
    for test assertions.

    ``outcomes`` records each delivery as a broker would settle it
    (ADR-081 point 9): ``"ack"`` when the handler returned, and
    ``"nack <ExceptionType>"`` when it raised an ``Exception``, which
    ``simulate_message()`` then re-raises. A ``BaseException`` such as a
    cancellation, and an unknown topic, record nothing. Behind an
    ``InboundEventGateway`` a handler failure is always recorded as
    ``"nack EventHandlersFailed"``, the exception the gateway re-raises.

    Example::

        subscriber = InMemoryMessageSubscriber()
        subscriber.subscribe("orders", my_handler)
        await subscriber.start()
        await subscriber.simulate_message("orders", {"order_id": "o1"})
        assert subscriber.outcomes == ["ack"]
        await subscriber.stop()
    """

    def __init__(self) -> None:
        self.subscriptions: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}
        self.started = False
        self.stopped = False
        self.outcomes: list[str] = []

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Register a handler for *topic*."""
        self.subscriptions[topic] = handler

    async def simulate_message(self, topic: str, payload: dict[str, Any]) -> None:
        """Manually invoke the handler registered for *topic*.

        Appends ``"ack"`` to ``outcomes`` when the handler returns, and
        ``"nack <ExceptionType>"`` when it raises an ``Exception``.

        Raises
        ------
        KeyError
            If no handler is registered for *topic*; nothing is recorded.
        Exception
            Whatever the handler raised, unchanged, after recording the NACK.
        """
        handler = self.subscriptions[topic]
        try:
            await handler(payload)
        except Exception as exc:
            self.outcomes.append(f"nack {type(exc).__name__}")
            raise
        self.outcomes.append("ack")

    async def start(self) -> None:
        """Set the started flag."""
        self.started = True

    async def stop(self) -> None:
        """Set the stopped flag."""
        self.stopped = True
