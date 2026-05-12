"""Tests for EventRegistry and GenericDomainEvent.

Covers registration (DCE-45), serialization, deserialization, and the
weak-schema fallback for unregistered event types.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from pydomain.infrastructure import EventRegistry, GenericDomainEvent
from tests.infrastructure.conftest import OrderPlacedEvent


class ShipmentTracked(BaseModel):
    """Alternative event model used in tests that need a second registered type."""

    tracking_id: str
    status: str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> EventRegistry:
    return EventRegistry()


@pytest.fixture
def registered_registry(registry: EventRegistry) -> EventRegistry:
    registry.register(OrderPlacedEvent)
    return registry


# ---------------------------------------------------------------------------
# Registration and resolution
# ---------------------------------------------------------------------------


class TestRegistration:
    """Register event types and resolve them by name."""

    def test_register_and_resolve(self, registry: EventRegistry) -> None:
        """Registering a class and resolving by its __name__ returns the class."""
        registry.register(ShipmentTracked)
        resolved = registry.resolve("ShipmentTracked")
        assert resolved is ShipmentTracked

    def test_duplicate_registration_raises_value_error(
        self, registry: EventRegistry
    ) -> None:
        """Registering the same class twice raises ValueError."""
        registry.register(OrderPlacedEvent)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(OrderPlacedEvent)

    def test_resolve_unregistered_raises_key_error(
        self, registry: EventRegistry
    ) -> None:
        """Resolving an unregistered type name raises KeyError."""
        with pytest.raises(KeyError, match="UnknownEvent"):
            registry.resolve("UnknownEvent")


# ---------------------------------------------------------------------------
# Type-name resolution
# ---------------------------------------------------------------------------


class TestTypeName:
    """Registry.type_name() returns the correct class name."""

    def test_type_name_returns_class_name(
        self, registry: EventRegistry, order_placed_event: OrderPlacedEvent
    ) -> None:
        """type_name() returns type(event).__name__."""
        name = registry.type_name(order_placed_event)
        assert name == "OrderPlacedEvent"

    def test_type_name_for_unregistered_instance(self, registry: EventRegistry) -> None:
        """type_name() works even when the event class is not registered."""
        event = ShipmentTracked(tracking_id="TRK-001", status="in_transit")
        name = registry.type_name(event)
        assert name == "ShipmentTracked"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialize:
    """Registry.serialize() produces the correct structure."""

    def test_serialize_returns_type_and_data(
        self, registry: EventRegistry, order_placed_event: OrderPlacedEvent
    ) -> None:
        """Serialized dict contains 'type' and 'data' keys."""
        result = registry.serialize(order_placed_event)
        assert result == {
            "type": "OrderPlacedEvent",
            "data": order_placed_event.model_dump(),
        }

    def test_serialize_unregistered_event(self, registry: EventRegistry) -> None:
        """Serialization works even for unregistered event types."""
        event = ShipmentTracked(tracking_id="TRK-001", status="in_transit")
        result = registry.serialize(event)
        assert result["type"] == "ShipmentTracked"
        assert result["data"] == event.model_dump()


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------


class TestDeserialize:
    """Registry.deserialize() resolves registered types and falls back to
    GenericDomainEvent for unknown types."""

    def test_deserialize_round_trip(
        self, registered_registry: EventRegistry, order_placed_event: OrderPlacedEvent
    ) -> None:
        """Serializing then deserializing produces an equivalent event."""
        serialized = registered_registry.serialize(order_placed_event)
        deserialized = registered_registry.deserialize(serialized)
        assert isinstance(deserialized, OrderPlacedEvent)
        assert deserialized.order_id == order_placed_event.order_id
        assert deserialized.total == order_placed_event.total

    def test_deserialize_registered_type(
        self, registered_registry: EventRegistry
    ) -> None:
        """Deserializing a registered event type returns the correct model."""
        data: dict[str, Any] = {
            "type": "OrderPlacedEvent",
            "data": {"order_id": "ORD-002", "total": 49.50},
        }
        result = registered_registry.deserialize(data)
        assert isinstance(result, OrderPlacedEvent)
        assert result.order_id == "ORD-002"
        assert result.total == 49.50

    def test_deserialize_weak_schema_fallback(self, registry: EventRegistry) -> None:
        """Deserializing an unregistered type returns GenericDomainEvent."""
        data: dict[str, Any] = {"type": "Unknown", "data": {"key": "val"}}
        result = registry.deserialize(data)
        assert isinstance(result, GenericDomainEvent)

    def test_generic_domain_event_preserves_original_type_and_data(
        self, registry: EventRegistry
    ) -> None:
        """The fallback GenericDomainEvent carries the original type and
        data from the serialized payload."""
        data: dict[str, Any] = {
            "type": "LegacyEventV1",
            "data": {"user_id": 42, "action": "login"},
        }
        result = registry.deserialize(data)
        assert isinstance(result, GenericDomainEvent)
        assert result.type == "LegacyEventV1"
        assert result.data == {"user_id": 42, "action": "login"}

    def test_deserialize_empty_data(self, registry: EventRegistry) -> None:
        """Deserializing an unregistered type with empty data works."""
        data: dict[str, Any] = {"type": "EmptyEvent", "data": {}}
        result = registry.deserialize(data)
        assert isinstance(result, GenericDomainEvent)
        assert result.type == "EmptyEvent"
        assert result.data == {}
