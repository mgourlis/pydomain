"""Contract suite for the ``MessageSubscriber`` Protocol (ADR-059, ADR-060)."""

from __future__ import annotations

from typing import Any

import pytest

from pydomain.infrastructure.message_subscriber import MessageSubscriber
from pydomain.testing.contracts._support import pending

__all__ = ["MessageSubscriberContract"]

_ORDERS = "contract.orders.placed"
_INVOICES = "contract.invoices.issued"


class _Recorder:
    """An async handler that records every payload it receives."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)


class MessageSubscriberContract:
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

    def test_satisfies_protocol(self, subscriber: MessageSubscriber) -> None:
        """The subscriber is a ``MessageSubscriber``."""
        pending()

    @pytest.mark.anyio
    async def test_start_then_stop(self, subscriber: MessageSubscriber) -> None:
        """A started subscriber stops cleanly."""
        pending()

    @pytest.mark.anyio
    async def test_handler_receives_payload_for_its_topic(
        self, subscriber: MessageSubscriber
    ) -> None:
        """A handler subscribed to a topic receives the raw dict payload."""
        pending()

    @pytest.mark.anyio
    async def test_handler_does_not_receive_other_topics(
        self, subscriber: MessageSubscriber
    ) -> None:
        """Each topic's messages reach only that topic's handler."""
        pending()
