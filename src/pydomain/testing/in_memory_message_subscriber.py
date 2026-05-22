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
    ``start()`` and ``stop()`` toggle state flags for test assertions.

    Example::

        subscriber = InMemoryMessageSubscriber()
        subscriber.subscribe("orders", my_handler)
        await subscriber.start()
        await subscriber.simulate_message("orders", {"order_id": "o1"})
        await subscriber.stop()
    """

    def __init__(self) -> None:
        self.subscriptions: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}
        self.started = False
        self.stopped = False

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        """Register a handler for *topic*."""
        self.subscriptions[topic] = handler

    async def simulate_message(self, topic: str, payload: dict[str, Any]) -> None:
        """Manually invoke the handler registered for *topic*.

        Raises
        ------
        KeyError
            If no handler is registered for *topic*.
        """
        handler = self.subscriptions.get(topic)
        if handler is not None:
            await handler(payload)

    async def start(self) -> None:
        """Toggle the started flag."""
        self.started = True

    async def stop(self) -> None:
        """Toggle the stopped flag."""
        self.stopped = True
