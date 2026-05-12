"""Tests for MessageBroker protocol and InMemoryMessageBroker.

Covers protocol conformance (isinstance check) and the full
InMemoryMessageBroker behaviour: publish capture, tuple structure,
topic routing, insertion order, and lifecycle safety.
"""

from __future__ import annotations

import pytest

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.infrastructure import MessageBroker
from pydomain.testing import InMemoryMessageBroker

# ── Sample integration events (prefixed with underscore to avoid collection) ─


class _OrderPlaced(IntegrationEvent):
    """Test integration event for publish-capture and ordering tests."""

    order_id: str


class _PaymentProcessed(IntegrationEvent):
    """Test integration event for topic-routing tests."""

    transaction_id: str


# ════════════════════════════════════════════════════════════════════════════
# Protocol conformance
# ════════════════════════════════════════════════════════════════════════════


class TestProtocolConformance:
    """MessageBroker is a runtime-checkable protocol (DCE-44)."""

    @pytest.mark.anyio
    async def test_in_memory_broker_conforms_to_protocol(self) -> None:
        """InMemoryMessageBroker satisfies the MessageBroker protocol."""
        assert isinstance(InMemoryMessageBroker(), MessageBroker)


# ════════════════════════════════════════════════════════════════════════════
# InMemoryMessageBroker behaviour
# ════════════════════════════════════════════════════════════════════════════


class TestInMemoryMessageBroker:
    """InMemoryMessageBroker publish, lifecycle, and state."""

    @pytest.mark.anyio
    async def test_published_is_empty_initially(self) -> None:
        """A fresh broker has an empty published list."""
        broker = InMemoryMessageBroker()
        assert broker.published == []

    @pytest.mark.anyio
    async def test_publish_captures_event(self) -> None:
        """publish() appends (topic, event) to the published list."""
        broker = InMemoryMessageBroker()
        event = _OrderPlaced(order_id="order-100")
        await broker.publish("orders", event)
        assert len(broker.published) == 1

    @pytest.mark.anyio
    async def test_published_tuple_structure(self) -> None:
        """Each published entry is a (topic, event) tuple."""
        broker = InMemoryMessageBroker()
        event = _OrderPlaced(order_id="order-101")
        await broker.publish("orders", event)
        topic, published_event = broker.published[0]
        assert topic == "orders"
        assert published_event is event

    @pytest.mark.anyio
    async def test_topic_routing(self) -> None:
        """Publishing to different topics preserves the topic in each tuple."""
        broker = InMemoryMessageBroker()
        event1 = _OrderPlaced(order_id="order-200")
        event2 = _PaymentProcessed(transaction_id="tx-300")
        await broker.publish("orders", event1)
        await broker.publish("payments", event2)
        assert broker.published[0][0] == "orders"
        assert broker.published[1][0] == "payments"

    @pytest.mark.anyio
    async def test_publish_order_is_preserved(self) -> None:
        """Events are captured in insertion order."""
        broker = InMemoryMessageBroker()
        event1 = _OrderPlaced(order_id="first")
        event2 = _OrderPlaced(order_id="second")
        event3 = _OrderPlaced(order_id="third")
        await broker.publish("topic", event1)
        await broker.publish("topic", event2)
        await broker.publish("topic", event3)
        ev1 = broker.published[0][1]
        ev2 = broker.published[1][1]
        ev3 = broker.published[2][1]
        assert isinstance(ev1, _OrderPlaced) and ev1.order_id == "first"
        assert isinstance(ev2, _OrderPlaced) and ev2.order_id == "second"
        assert isinstance(ev3, _OrderPlaced) and ev3.order_id == "third"

    @pytest.mark.anyio
    async def test_start_is_safe(self) -> None:
        """start() on a fresh instance does not raise."""
        broker = InMemoryMessageBroker()
        await broker.start()

    @pytest.mark.anyio
    async def test_stop_is_safe(self) -> None:
        """stop() on a fresh instance does not raise."""
        broker = InMemoryMessageBroker()
        await broker.stop()

    def test_message_broker_exported(self) -> None:
        """MessageBroker in infra __all__; InMemoryMessageBroker in testing __all__."""
        from pydomain.infrastructure import __all__ as infra_all
        from pydomain.testing import __all__ as testing_all

        assert "MessageBroker" in infra_all
        assert "InMemoryMessageBroker" in testing_all
