"""Comprehensive tests for ``InboundEventGateway``.

Tests cover registration, message processing, failure modes, and
subscriber lifecycle delegation.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from pydantic import UUID4, BaseModel, ConfigDict, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.id_generator import IdGenerator
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
    async def test_validation_failure_log_names_locations_without_traceback(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The log names the class, topic and failing fields (ADR-071)."""
        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )
        caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

        await subscriber.simulate_message("orders.shipped", {"order_id": "test"})

        [record] = caplog.records
        assert record.getMessage() == (
            "Failed to validate payload for _OrderShippedIntegration on topic "
            "'orders.shipped' — discarding: ValidationError: 1 error for "
            "_OrderShippedIntegration at tracking_number (missing)"
        )
        assert record.exc_info is None

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

    def test_gateway_exposes_its_message_bus(
        self, gateway: InboundEventGateway, bus: MessageBus
    ) -> None:
        """The gateway's message_bus is the bus it was built with, read-only
        (decision-log B-25).
        """
        assert gateway.message_bus is bus
        with pytest.raises(AttributeError):
            gateway.message_bus = MessageBus()  # type: ignore[misc]

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

    @pytest.mark.anyio
    async def test_tracing_ids_propagated_to_domain_event(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
    ) -> None:
        """When integration event carries correlation_id, the translated
        DomainEvent is stamped with it. causation_id is set to the
        integration event's own event_id (the direct cause)."""
        dispatched: list[_OrderShippedDomain] = []

        async def event_handler(event: _OrderShippedDomain) -> None:
            dispatched.append(event)

        bus.register_event(_OrderShippedDomain, event_handler)
        gateway.register_translation(
            "orders.shipped",
            _OrderShippedIntegration,
            _translate_order_shipped,
        )

        cid = "0195e000-0000-7000-8000-000000000aaa"
        ieid = "0195e000-0000-7000-8000-000000000ccc"

        await subscriber.simulate_message(
            "orders.shipped",
            {
                "event_id": ieid,
                "order_id": "550e8400-e29b-41d4-a716-446655440000",
                "tracking_number": "TRACK123",
                "correlation_id": cid,
            },
        )

        assert len(dispatched) == 1
        assert dispatched[0].correlation_id == UUID(cid)
        # causation_id = integration event's own event_id, not its causation_id
        assert dispatched[0].causation_id == UUID(ieid)

    @pytest.mark.anyio
    async def test_no_tracing_ids_payload_still_dispatched(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
    ) -> None:
        """Integration events without tracing IDs dispatch with None tracing.
        This is backward-compatible — producers that don't stamp are fine."""
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
                "order_id": "550e8400-e29b-41d4-a716-446655440000",
                "tracking_number": "TRACK123",
            },
        )

        assert len(dispatched) == 1
        assert dispatched[0].correlation_id is None
        assert dispatched[0].causation_id is None

    @pytest.mark.anyio
    async def test_malformed_tracing_ids_survive(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
    ) -> None:
        """Non-UUID tracing IDs don't crash the gateway — it dispatches
        without tracing and logs a warning."""
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
                "order_id": "550e8400-e29b-41d4-a716-446655440000",
                "tracking_number": "TRACK123",
                "correlation_id": "not-a-uuid",
                "causation_id": "also-not-a-uuid",
            },
        )

        assert len(dispatched) == 1
        assert dispatched[0].correlation_id is None
        assert dispatched[0].causation_id is None


# ---------------------------------------------------------------------------
# Log lines (ADR-060 failure modes; ADR-071 for what they may carry)
# ---------------------------------------------------------------------------

_A_UUID = "550e8400-e29b-41d4-a716-446655440001"
_A_V1_UUID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


class _OrderShippedV4Correlated(DomainEvent):
    """A domain event that only accepts version-4 correlation ids."""

    order_id: UUID
    correlation_id: UUID4 | None = None  # type: ignore[assignment]


def _translate_to_v4_correlated(
    event: _OrderShippedIntegration,
) -> _OrderShippedV4Correlated:
    return _OrderShippedV4Correlated(order_id=UUID(event.order_id))


_VALID_SHIPPED = {
    "event_id": "e1",
    "order_id": "550e8400-e29b-41d4-a716-446655440000",
    "tracking_number": "TRACK123",
}


def _gateway_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "pydomain.message_subscriber"]


class TestInboundEventGatewayLogLines:
    """Each failure mode logs one line naming its class and topic."""

    @pytest.mark.anyio
    async def test_unknown_topic_warning_names_the_topic(
        self, gateway: InboundEventGateway, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger="pydomain.message_subscriber")

        await gateway._process_message("unknown.topic", {"order_id": "test"})

        assert [r.getMessage() for r in _gateway_records(caplog)] == [
            "No translation registered for topic 'unknown.topic' — discarding message"
        ]

    @pytest.mark.anyio
    async def test_translation_failure_names_class_and_topic_with_traceback(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Translator failures keep their traceback until W2-3 (ADR-071, B-33)."""

        def _failing_translator(
            event: _OrderShippedIntegration,
        ) -> _OrderShippedDomain:
            raise ValueError("translation failed")

        gateway.register_translation(
            "orders.shipped", _OrderShippedIntegration, _failing_translator
        )
        caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

        await subscriber.simulate_message("orders.shipped", dict(_VALID_SHIPPED))

        [record] = _gateway_records(caplog)
        assert record.getMessage() == (
            "Translation failed for _OrderShippedIntegration on topic "
            "'orders.shipped' — discarding"
        )
        assert record.exc_info is not None

    @pytest.mark.parametrize(
        ("correlation_id", "event_id", "translator", "problem"),
        [
            (
                "not-a-uuid",
                _A_UUID,
                _translate_order_shipped,
                "correlation_id is not a UUID",
            ),
            (_A_UUID, "e1", _translate_order_shipped, "event_id is not a UUID"),
            (
                "not-a-uuid",
                "e1",
                _translate_order_shipped,
                "correlation_id and event_id are not UUIDs",
            ),
            pytest.param(
                _A_V1_UUID,
                _A_UUID,
                _translate_to_v4_correlated,
                # The ValidationError's title is Pydantic's name for the field's type.
                "the domain event rejected them: ValidationError: 1 error for * "
                "at <model> (uuid_version)",
                id="both-parse-but-the-event-rejects-them",
            ),
        ],
    )
    @pytest.mark.anyio
    async def test_invalid_tracing_ids_warning_names_class_topic_and_fields(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        caplog: pytest.LogCaptureFixture,
        correlation_id: str,
        event_id: str,
        translator: Callable[[_OrderShippedIntegration], DomainEvent],
        problem: str,
    ) -> None:
        """The warning names the fields that failed to parse, never their values.

        When both ids parse but the domain event's own fields reject them, it
        describes the rejection instead (ADR-071). Changed for ADR-071 (B-43): it
        used to log both ids with ``%r``.
        """
        gateway.register_translation(
            "orders.shipped", _OrderShippedIntegration, translator
        )
        caplog.set_level(logging.WARNING, logger="pydomain.message_subscriber")

        await subscriber.simulate_message(
            "orders.shipped",
            {**_VALID_SHIPPED, "correlation_id": correlation_id, "event_id": event_id},
        )

        [message] = [r.getMessage() for r in _gateway_records(caplog)]
        assert fnmatch.fnmatchcase(
            message,
            "Invalid tracing IDs on _OrderShippedIntegration for topic "
            f"'orders.shipped' — {problem}. Dispatching without tracing.",
        ), message

    @pytest.mark.anyio
    async def test_dispatch_failure_names_event_class_and_topic(
        self,
        gateway: InboundEventGateway,
        subscriber: FakeMessageSubscriber,
        bus: MessageBus,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        gateway.register_translation(
            "orders.shipped", _OrderShippedIntegration, _translate_order_shipped
        )

        async def failing_dispatch(message: Any) -> Any:
            raise RuntimeError("dispatch failure")

        monkeypatch.setattr(bus, "dispatch", failing_dispatch)
        caplog.set_level(logging.ERROR, logger="pydomain.message_subscriber")

        with pytest.raises(RuntimeError):
            await subscriber.simulate_message("orders.shipped", dict(_VALID_SHIPPED))

        [record] = _gateway_records(caplog)
        assert record.getMessage() == (
            "Dispatch failed for _OrderShippedDomain (from _OrderShippedIntegration) "
            "on 'orders.shipped' — propagating"
        )


# ---------------------------------------------------------------------------
# Inbound event identity (ADR-080 points 1 and 2, DCE-149, INF-02)
# ---------------------------------------------------------------------------

# The documented value of the gateway's private namespace (ADR-080 point 3).
_INBOUND_NAMESPACE = UUID("52a1e11d-5cfe-52a6-971b-a1b2961dd04f")
_INTEGRATION_ID = "0195e000-0000-7000-8000-0000000000e1"
_ORDER_ID = "550e8400-e29b-41d4-a716-446655440000"
_SHIPPED = {
    "event_id": _INTEGRATION_ID,
    "order_id": _ORDER_ID,
    "tracking_number": "TRACK-SYN-1",
}
_EXPLICIT_ID = UUID("00000000-0000-4000-8000-0000000000f1")


def _derived(integration_id: str) -> UUID:
    return uuid5(_INBOUND_NAMESPACE, integration_id)


class _DeterministicShipped(DomainEvent):
    """Sets its own event_id in a before-validator, from its order."""

    order_id: UUID

    @model_validator(mode="before")
    @classmethod
    def _id_from_order(cls, data: Any) -> Any:
        if isinstance(data, dict) and "event_id" not in data:
            return {**data, "event_id": uuid5(_EXPLICIT_ID, str(data["order_id"]))}  # pyright: ignore[reportUnknownArgumentType]
        return data  # pyright: ignore[reportUnknownVariableType]


class _ShipmentHolder(BaseModel):
    """A plain model with an event field."""

    event: _OrderShippedDomain


class _ShipmentBatched(DomainEvent):
    """An event that carries another event."""

    shipment: _OrderShippedDomain


class _CamelShipped(DomainEvent):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True
    )
    order_id: UUID


async def _deliver(
    translator: Callable[[_OrderShippedIntegration], DomainEvent],
    *payloads: dict[str, Any],
    topics: tuple[str, ...] = ("orders.shipped",),
) -> list[DomainEvent]:
    """Deliver each payload on each topic; return the events dispatched."""
    bus = MessageBus()
    subscriber = FakeMessageSubscriber()
    gateway = InboundEventGateway(subscriber, bus)
    dispatched: list[DomainEvent] = []

    async def record(event: DomainEvent) -> None:
        dispatched.append(event)

    for event_type in (
        _OrderShippedDomain,
        _DeterministicShipped,
        _ShipmentBatched,
        _CamelShipped,
    ):
        bus.register_event(event_type, record)
    for topic in topics:
        gateway.register_translation(topic, _OrderShippedIntegration, translator)
    for payload in payloads or (_SHIPPED,):
        for topic in topics:
            await subscriber.simulate_message(topic, dict(payload))
    return dispatched


class TestInboundEventIdentity:
    """The gateway derives the id of a translated event whose id was generated."""

    @pytest.mark.anyio
    async def test_redelivered_integration_event_gets_the_same_event_id(
        self,
    ) -> None:
        first, second = await _deliver(_translate_order_shipped, _SHIPPED, _SHIPPED)

        assert first.event_id == second.event_id

    @pytest.mark.parametrize(
        "integration_id", [_INTEGRATION_ID, "shipment-0001"], ids=["uuid", "not-a-uuid"]
    )
    @pytest.mark.anyio
    async def test_inbound_event_id_is_uuid5_of_the_integration_event_id(
        self, integration_id: str
    ) -> None:
        """uuid5 takes any string; the payload's correlation_id plays no part."""
        payload = {
            **_SHIPPED,
            "event_id": integration_id,
            "correlation_id": "0195e000-0000-7000-8000-0000000000c1",
        }

        [event] = await _deliver(_translate_order_shipped, payload)

        assert _INBOUND_NAMESPACE == uuid5(
            NAMESPACE_URL, "https://github.com/mgourlis/pydomain/inbound-event-id"
        )
        assert event.event_id == _derived(integration_id)

    @pytest.mark.anyio
    async def test_event_id_is_derived_without_correlation_id(self) -> None:
        [event] = await _deliver(_translate_order_shipped)

        assert event.event_id == _derived(_INTEGRATION_ID)
        assert event.correlation_id is None
        assert event.causation_id is None

    @pytest.mark.anyio
    async def test_translator_event_id_is_kept(self) -> None:
        """A guard: an id the translator sets is never replaced."""

        def translate(event: _OrderShippedIntegration) -> _OrderShippedDomain:
            return _OrderShippedDomain(
                event_id=_EXPLICIT_ID,
                order_id=UUID(event.order_id),
                tracking=event.tracking_number,
            )

        [event] = await _deliver(translate)

        assert event.event_id == _EXPLICIT_ID

    @pytest.mark.anyio
    async def test_derived_event_id_ignores_the_topic(self) -> None:
        """One integration id on two topics gives one domain id (B-160)."""
        first, second = await _deliver(
            _translate_order_shipped, topics=("orders.shipped", "orders.shipped.eu")
        )

        assert first.event_id == second.event_id == _derived(_INTEGRATION_ID)

    @pytest.mark.anyio
    async def test_payload_without_event_id_warns_and_is_not_deduplicated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The WARNING names the class and topic only (B-146, ADR-071)."""
        sentinel = "SENTINEL-TRACKING-7f3a"
        payload = {"order_id": _ORDER_ID, "tracking_number": sentinel}
        caplog.set_level(logging.WARNING, logger="pydomain.message_subscriber")

        first, second = await _deliver(_translate_order_shipped, payload, payload)

        assert first.event_id != second.event_id
        records = _gateway_records(caplog)
        assert [(r.levelno, r.getMessage()) for r in records] == [
            (
                logging.WARNING,
                "No event_id in the payload of _OrderShippedIntegration on topic "
                "'orders.shipped' — dispatching without deduplication",
            )
        ] * 2
        assert all(sentinel not in repr(r.__dict__) for r in records)

    @pytest.mark.anyio
    async def test_generated_event_id_marker_survives_model_copy_and_stamp(
        self,
    ) -> None:
        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            return (
                _translate_order_shipped(event)
                .model_copy(update={"tracking": "TRACK-SYN-2"})
                .model_copy(deep=True)
                .stamp(correlation_id=uuid4(), causation_id=uuid4())
            )

        [event] = await _deliver(translate)

        assert event.event_id == _derived(_INTEGRATION_ID)

    @pytest.mark.anyio
    async def test_subclass_before_validator_event_id_counts_as_explicit(
        self,
    ) -> None:
        """A guard: a subclass validator's deterministic id is kept."""

        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            return _DeterministicShipped(order_id=UUID(event.order_id))

        [event] = await _deliver(translate)

        assert event.event_id == uuid5(_EXPLICIT_ID, _ORDER_ID)

    @pytest.mark.anyio
    async def test_model_copy_update_event_id_is_kept_as_explicit(self) -> None:
        """A guard: an id set with model_copy(update=...) is explicit (B-166)."""

        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            return (
                _translate_order_shipped(event)
                .model_copy(update={"event_id": _EXPLICIT_ID})
                .stamp(correlation_id=uuid4(), causation_id=uuid4())
            )

        [event] = await _deliver(translate)

        assert event.event_id == _EXPLICIT_ID

    @pytest.mark.anyio
    async def test_model_construct_event_reads_as_explicit(self) -> None:
        """A guard: model_construct() runs no validator, so nothing is derived."""

        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            return _OrderShippedDomain.model_construct(
                order_id=UUID(event.order_id), tracking=event.tracking_number
            )

        first, second = await _deliver(translate, _SHIPPED, _SHIPPED)

        assert first.event_id != second.event_id
        assert _derived(_INTEGRATION_ID) not in {first.event_id, second.event_id}

    @pytest.mark.parametrize("path", ["model_validate", "field_of_a_model"])
    @pytest.mark.anyio
    async def test_validating_an_existing_event_keeps_its_marker(
        self, path: str
    ) -> None:
        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            generated = _translate_order_shipped(event)
            if path == "model_validate":
                return _OrderShippedDomain.model_validate(generated)
            return _ShipmentHolder(event=generated).event

        [event] = await _deliver(translate)

        assert event.event_id == _derived(_INTEGRATION_ID)

    @pytest.mark.anyio
    async def test_nested_event_gets_its_own_marker(self) -> None:
        """Each event's marker comes from its own validation, not the outer one."""
        inner = {"order_id": _ORDER_ID, "tracking": "TRACK-SYN-1"}

        def inner_generated(event: _OrderShippedIntegration) -> DomainEvent:
            outer = _ShipmentBatched.model_validate(
                {"event_id": _EXPLICIT_ID, "shipment": inner}
            )
            return outer.shipment

        def outer_explicit(event: _OrderShippedIntegration) -> DomainEvent:
            return _ShipmentBatched.model_validate(
                {"event_id": _EXPLICIT_ID, "shipment": inner}
            )

        def inner_explicit(event: _OrderShippedIntegration) -> DomainEvent:
            outer = _ShipmentBatched.model_validate(
                {"shipment": {**inner, "event_id": _EXPLICIT_ID}}
            )
            return outer.shipment

        def outer_generated(event: _OrderShippedIntegration) -> DomainEvent:
            return _ShipmentBatched.model_validate(
                {"shipment": {**inner, "event_id": _EXPLICIT_ID}}
            )

        ids = [
            (await _deliver(translate))[0].event_id
            for translate in (
                inner_generated,
                outer_explicit,
                inner_explicit,
                outer_generated,
            )
        ]

        derived = _derived(_INTEGRATION_ID)
        assert ids == [derived, _EXPLICIT_ID, _EXPLICIT_ID, derived]

    @pytest.mark.anyio
    async def test_marker_after_a_failed_validation_reads_explicit(self) -> None:
        """A guard: a validation that raised leaves no generated report behind."""

        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            with pytest.raises(ValidationError):
                _ShipmentBatched.model_validate({"shipment": {"order_id": "bad"}})
            return _OrderShippedDomain(
                event_id=_EXPLICIT_ID,
                order_id=UUID(event.order_id),
                tracking=event.tracking_number,
            )

        [event] = await _deliver(translate)

        assert event.event_id == _EXPLICIT_ID

    @pytest.mark.anyio
    async def test_explicit_event_id_given_by_its_alias_is_kept(self) -> None:
        """A guard: an id passed under the field's alias is explicit."""

        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            return _CamelShipped.model_validate(
                {"eventId": str(_EXPLICIT_ID), "orderId": event.order_id}
            )

        [event] = await _deliver(translate)

        assert event.event_id == _EXPLICIT_ID

    @pytest.mark.anyio
    async def test_comparing_events_leaves_their_markers_unchanged(self) -> None:
        """Equality ignores the marker without rewriting either side's (B-158)."""

        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            generated = _translate_order_shipped(event)
            explicit = _OrderShippedDomain.model_validate(generated.model_dump())
            assert explicit == generated
            assert generated == explicit
            return generated

        def translate_explicit(event: _OrderShippedIntegration) -> DomainEvent:
            generated = _translate_order_shipped(event)
            explicit = _OrderShippedDomain.model_validate(generated.model_dump())
            assert generated == explicit
            assert explicit == generated
            return explicit

        [derived] = await _deliver(translate)
        [kept] = await _deliver(translate_explicit)

        assert derived.event_id == _derived(_INTEGRATION_ID)
        assert kept.event_id != _derived(_INTEGRATION_ID)


class _FixedIdGenerator:
    def generate(self) -> UUID:
        return _EXPLICIT_ID


class _FixedIdBatch(DomainEvent):
    """Generates the id _EXPLICIT_ID, and carries another event."""

    _id_generator: ClassVar[IdGenerator[UUID]] = _FixedIdGenerator()
    shipment: _OrderShippedDomain


class TestInboundEventIdentityNested:
    """An inner event's marker never comes from the outer validation."""

    @pytest.mark.anyio
    async def test_inner_explicit_id_equal_to_the_outer_generated_one_reads_explicit(
        self,
    ) -> None:
        def translate(event: _OrderShippedIntegration) -> DomainEvent:
            outer = _FixedIdBatch.model_validate(
                {
                    "shipment": {
                        "event_id": _EXPLICIT_ID,
                        "order_id": event.order_id,
                        "tracking": event.tracking_number,
                    }
                }
            )
            assert outer.event_id == _EXPLICIT_ID
            return outer.shipment

        [event] = await _deliver(translate)

        assert event.event_id == _EXPLICIT_ID
