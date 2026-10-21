"""Contract suite for the ``MessageBroker`` Protocol (ADR-051, ADR-061)."""

from __future__ import annotations

import pytest

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.infrastructure.message_broker import MessageBroker
from pydomain.testing.contracts._support import pending

__all__ = ["MessageBrokerContract"]


class _ContractOrderPlaced(IntegrationEvent):
    """Synthetic integration event used when ``new_event`` is not overridden."""

    order_id: str


class MessageBrokerContract:
    """Cases every ``MessageBroker`` implementation passes.

    Override the ``broker`` fixture to return a fresh, unstarted broker for
    each test. Override :attr:`topic` and :meth:`new_event` when your broker
    accepts only known topics or event types.

    The Protocol has no read side, so the suite checks the lifecycle and that
    ``publish`` accepts what the Protocol says it must. Check delivery in your
    adapter's own tests.
    """

    topic = "contract.orders.placed"

    @pytest.fixture
    def broker(self) -> MessageBroker:
        """Return a fresh, unstarted broker. Override in your subclass."""
        raise NotImplementedError(
            "Override the `broker` fixture to return your adapter."
        )

    def new_event(self) -> IntegrationEvent:
        """Return an integration event your broker can publish on :attr:`topic`."""
        return _ContractOrderPlaced(order_id="order-1")

    def test_satisfies_protocol(self, broker: MessageBroker) -> None:
        """The broker is a ``MessageBroker``."""
        pending()

    @pytest.mark.anyio
    async def test_start_then_stop(self, broker: MessageBroker) -> None:
        """A started broker stops cleanly."""
        pending()

    @pytest.mark.anyio
    async def test_publish_on_started_broker(self, broker: MessageBroker) -> None:
        """A started broker accepts an event for a topic."""
        pending()

    @pytest.mark.anyio
    async def test_publish_accepts_headers(self, broker: MessageBroker) -> None:
        """``publish`` accepts trace-context ``headers`` (ADR-061)."""
        pending()

    @pytest.mark.anyio
    async def test_publish_ignores_unsupported_keyword_arguments(
        self, broker: MessageBroker
    ) -> None:
        """Keyword arguments the broker does not support are ignored."""
        pending()
