"""Comprehensive tests for ``InboundEventGateway``.

Tests cover registration, message processing, failure modes, and
subscriber lifecycle delegation.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

import pytest

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure import MessageBus
from pydomain.infrastructure.message_subscriber import InboundEventGateway

# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class FakeMessageSubscriber:
    """In-memory test double for ``MessageSubscriber`` protocol."""

    def __init__(self) -> None:
        self.subscriptions: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}
        self.started = False
        self.stopped = False

    def subscribe(
        self,
        topic: str,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.subscriptions[topic] = handler

    async def simulate_message(self, topic: str, payload: dict[str, Any]) -> None:
        """Manually invoke the handler for *topic*."""
        handler = self.subscriptions.get(topic)
        if handler is not None:
            await handler(payload)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


# ---------------------------------------------------------------------------
# Test types
# ---------------------------------------------------------------------------


class _OrderShippedIntegration(IntegrationEvent):
    order_id: str
    tracking_number: str


class _OrderShippedDomain(DomainEvent):
    order_id: UUID
    tracking: str


def _translate_order_shipped(
    event: _OrderShippedIntegration,
) -> _OrderShippedDomain:
    return _OrderShippedDomain(
        order_id=UUID(event.order_id),
        tracking=event.tracking_number,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def subscriber() -> FakeMessageSubscriber:
    return FakeMessageSubscriber()


@pytest.fixture
def gateway(
    subscriber: FakeMessageSubscriber,
    bus: MessageBus,
) -> InboundEventGateway:
    return InboundEventGateway(subscriber=subscriber, message_bus=bus)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInboundEventGateway:
    """InboundEventGateway registration, processing, and lifecycle."""

    @pytest.mark.anyio
    async def test_register_translation_subscribes_to_topic(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
    ) -> None:
        """Registering a translation subscribes the gateway to the topic."""
        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )

        assert "orders.shipped" in subscriber.subscriptions

    @pytest.mark.anyio
    async def test_process_message_happy_path(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
    ) -> None:
        """Integration event flows through validation, translation, dispatch."""
        dispatched: list[_OrderShippedDomain] = []

        async def event_handler(event: _OrderShippedDomain) -> None:
            dispatched.append(event)

        bus.register_event(_OrderShippedDomain, event_handler)

        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )

        order_id = "550e8400-e29b-41d4-a716-446655440000"
        await subscriber.simulate_message(
            "orders.shipped",
            {"order_id": order_id, "tracking_number": "TRACK123"},
        )

        assert len(dispatched) == 1
        assert dispatched[0].order_id == UUID(order_id)
        assert dispatched[0].tracking == "TRACK123"

    @pytest.mark.anyio
    async def test_process_message_unknown_topic_logs_warning(
        self,
        gateway: InboundEventGateway,
        caplog: Any,
    ) -> None:
        """Unknown topic triggers a warning log and is discarded."""
        caplog.set_level(logging.WARNING, logger="pydomain.message_subscriber")

        await gateway._process_message(
            "unknown.topic",
            {"order_id": "test"},
        )

        warning_logs = [
            r
            for r in caplog.records
            if "No translation registered for topic" in r.getMessage()
            and r.levelno == logging.WARNING
        ]
        assert len(warning_logs) == 1
        assert "unknown.topic" in warning_logs[0].getMessage()

    @pytest.mark.anyio
    async def test_process_message_validation_error_logged(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        caplog: Any,
    ) -> None:
        """Payload missing required fields logs error and discards."""
        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )

        caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

        await subscriber.simulate_message(
            "orders.shipped",
            {"order_id": "test"},  # missing tracking_number
        )

        error_logs = [
            r
            for r in caplog.records
            if "Failed to validate payload" in r.getMessage()
            and r.levelno == logging.ERROR
        ]
        assert len(error_logs) == 1

    @pytest.mark.anyio
    async def test_process_message_translator_failure_logged(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        caplog: Any,
    ) -> None:
        """Translator that raises logs error and discards the message."""

        def _failing_translator(
            event: _OrderShippedIntegration,
        ) -> _OrderShippedDomain:
            raise ValueError("translation failed")

        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _failing_translator,
        )

        caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

        await subscriber.simulate_message(
            "orders.shipped",
            {
                "order_id": "550e8400-e29b-41d4-a716-446655440000",
                "tracking_number": "TRACK123",
            },
        )

        error_logs = [
            r
            for r in caplog.records
            if "Translation failed" in r.getMessage() and r.levelno == logging.ERROR
        ]
        assert len(error_logs) == 1

    @pytest.mark.anyio
    async def test_process_message_dispatch_failure_propagates(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
        monkeypatch: Any,
    ) -> None:
        """Dispatch failures propagate out of ``_process_message``."""
        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )

        async def failing_dispatch(message: Any) -> Any:
            raise RuntimeError("dispatch failure")

        monkeypatch.setattr(bus, "dispatch", failing_dispatch)

        with pytest.raises(RuntimeError, match="dispatch failure"):
            await subscriber.simulate_message(
                "orders.shipped",
                {
                    "order_id": "550e8400-e29b-41d4-a716-446655440000",
                    "tracking_number": "TRACK123",
                },
            )

    @pytest.mark.anyio
    async def test_start_stop_delegates(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
    ) -> None:
        """Gateway.start() and stop() delegate to the subscriber."""
        assert not subscriber.started
        assert not subscriber.stopped

        await gateway.start()
        assert subscriber.started

        await gateway.stop()
        assert subscriber.stopped

    @pytest.mark.anyio
    async def test_multiple_topics_independent(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
    ) -> None:
        """Multiple registered topics are processed independently."""
        dispatched: list[str] = []

        class _OrderPlacedIntegration(IntegrationEvent):
            order_id: str

        class _OrderPlacedDomain(DomainEvent):
            order_id: UUID

        def translate_placed(
            event: _OrderPlacedIntegration,
        ) -> _OrderPlacedDomain:
            return _OrderPlacedDomain(order_id=UUID(event.order_id))

        async def handler_shipped(event: _OrderShippedDomain) -> None:
            dispatched.append("shipped")

        async def handler_placed(event: _OrderPlacedDomain) -> None:
            dispatched.append("placed")

        bus.register_event(_OrderShippedDomain, handler_shipped)
        bus.register_event(_OrderPlacedDomain, handler_placed)

        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )
        gateway.register_translation(
            "orders.placed",
            _OrderPlacedIntegration,
            translate_placed,
        )

        await subscriber.simulate_message(
            "orders.shipped",
            {"order_id": str(uuid4()), "tracking_number": "TRK001"},
        )
        await subscriber.simulate_message(
            "orders.placed",
            {"order_id": str(uuid4())},
        )

        assert dispatched == ["shipped", "placed"]

    @pytest.mark.anyio
    async def test_extra_fields_in_payload_accepted(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
    ) -> None:
        """Extra fields in the payload are silently ignored (Pydantic v2 default)."""
        dispatched: list[_OrderShippedDomain] = []

        async def event_handler(event: _OrderShippedDomain) -> None:
            dispatched.append(event)

        bus.register_event(_OrderShippedDomain, event_handler)
        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )

        await subscriber.simulate_message(
            "orders.shipped",
            {
                "order_id": str(uuid4()),
                "tracking_number": "TRK001",
                "extra_field": "should be ignored",
                "another_extra": 42,
            },
        )

        assert len(dispatched) == 1

    @pytest.mark.anyio
    async def test_same_topic_re_registration_overwrites(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
    ) -> None:
        """Registering the same topic twice replaces the first registration."""
        dispatched: list[str] = []

        class _OrderShippedV2(IntegrationEvent):
            order_id: str
            version: str

        class _OrderShippedDomainV2(DomainEvent):
            order_id: UUID
            version: str

        def translate_v2(event: _OrderShippedV2) -> _OrderShippedDomainV2:
            return _OrderShippedDomainV2(
                order_id=UUID(event.order_id),
                version=event.version,
            )

        async def handler_v1(event: _OrderShippedDomain) -> None:
            dispatched.append("v1")

        async def handler_v2(event: _OrderShippedDomainV2) -> None:
            dispatched.append("v2")

        bus.register_event(_OrderShippedDomain, handler_v1)
        bus.register_event(_OrderShippedDomainV2, handler_v2)

        # First registration
        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )
        # Second registration overwrites
        gateway.register_translation(
            "orders.shipped",
            _OrderShippedV2,
            translate_v2,
        )

        await subscriber.simulate_message(
            "orders.shipped",
            {"order_id": str(uuid4()), "version": "2.0"},
        )

        assert dispatched == ["v2"]
