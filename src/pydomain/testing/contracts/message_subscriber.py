"""Contract suite for the ``MessageSubscriber`` Protocol (ADR-059, ADR-060)."""

from __future__ import annotations

from typing import Any

import pytest

from pydomain.infrastructure.message_subscriber import MessageSubscriber
from pydomain.testing.contracts._backend import AsyncioBackend

__all__ = ["MessageSubscriberContract"]

_ORDERS = "contract.orders.placed"
_INVOICES = "contract.invoices.issued"


class _Recorder:
    """An async handler that records every payload it receives."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


class MessageSubscriberContract(AsyncioBackend):
    """Cases every ``MessageSubscriber`` implementation passes.

    Override the ``subscriber`` fixture to return a fresh, unstarted
    subscriber for each test, and :meth:`deliver` to put a message on a topic
    the way your broker does.
    """

    @pytest.fixture
    def subscriber(self) -> MessageSubscriber:
        """Return a fresh, unstarted subscriber. Override in your subclass."""
        raise NotImplementedError(
            "Override the `subscriber` fixture to return your adapter."
        )

    async def deliver(
        self, subscriber: MessageSubscriber, topic: str, payload: dict[str, Any]
    ) -> None:
        """Deliver *payload* on *topic* to the started *subscriber*.

        Return only once the subscriber has invoked the handler registered
        for *topic*, if any. Override in your subclass.
        """
        raise NotImplementedError(
            "Override `deliver` to send a message through your broker."
        )

    @pytest.mark.anyio
    async def test_satisfies_protocol(self, subscriber: MessageSubscriber) -> None:
        """The subscriber is a ``MessageSubscriber``."""
        assert isinstance(subscriber, MessageSubscriber)

    @pytest.mark.anyio
    async def test_start_then_stop(self, subscriber: MessageSubscriber) -> None:
        """A started subscriber stops cleanly."""
        await subscriber.start()
        await subscriber.stop()

    @pytest.mark.anyio
    async def test_handler_receives_payload_for_its_topic(
        self, subscriber: MessageSubscriber
    ) -> None:
        """A handler subscribed to a topic receives the raw dict payload."""
        handler = _Recorder()
        payload = {"order_id": "order-1", "line_count": 2}
        subscriber.subscribe(_ORDERS, handler)

        await subscriber.start()
        try:
            await self.deliver(subscriber, _ORDERS, payload)
        finally:
            await subscriber.stop()

        assert handler.payloads == [payload]

    @pytest.mark.anyio
    async def test_handler_does_not_receive_other_topics(
        self, subscriber: MessageSubscriber
    ) -> None:
        """Each topic's messages reach only that topic's handler."""
        orders, invoices = _Recorder(), _Recorder()
        subscriber.subscribe(_ORDERS, orders)
        subscriber.subscribe(_INVOICES, invoices)

        await subscriber.start()
        try:
            await self.deliver(subscriber, _ORDERS, {"order_id": "order-1"})
            await self.deliver(subscriber, _INVOICES, {"invoice_id": "invoice-1"})
        finally:
            await subscriber.stop()

        assert orders.payloads == [{"order_id": "order-1"}]
        assert invoices.payloads == [{"invoice_id": "invoice-1"}]
