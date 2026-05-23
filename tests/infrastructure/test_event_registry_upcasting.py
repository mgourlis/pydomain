"""Tests for EventRegistry.deserialize() with UpcasterRegistry integration.

Covers deserialization with and without upcasters, chain application,
weak-schema fallback with version preservation, and error propagation.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.exceptions import UpcastError
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry
from pydomain.infrastructure import EventRegistry, GenericDomainEvent
from tests.infrastructure.conftest import OrderPlacedEvent

# ---------------------------------------------------------------------------
# Test Domain Event Subclasses
# ---------------------------------------------------------------------------


class ItemEvent(DomainEvent):
    """Test event representing an item (current version = V2 with category)."""

    event_version: int = 2
    item_name: str
    quantity: int
    category: str = "default"


class PersonEvent(DomainEvent):
    """Test event with a renamed field across versions."""

    event_version: int = 2
    person_name: str  # renamed from "name" in V1
    email: str


# ---------------------------------------------------------------------------
# Test Upcaster Subclasses
# ---------------------------------------------------------------------------


class ItemV1ToV2Upcaster(EventUpcaster):
    """V1->V2: adds 'category' field to ItemEvent payload."""

    source_type: ClassVar[str] = "ItemEvent"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        event["category"] = "general"
        return event


class PersonV1ToV2Upcaster(EventUpcaster):
    """V1->V2: renames 'name' to 'person_name' in PersonEvent payload."""

    source_type: ClassVar[str] = "PersonEvent"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        event["person_name"] = event.pop("name")
        return event


class FaultyItemUpcaster(EventUpcaster):
    """An upcaster that always fails."""

    source_type: ClassVar[str] = "ItemEvent"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        msg = f"Corrupt payload: {event!r}"
        raise ValueError(msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def upcaster_registry() -> UpcasterRegistry:
    return UpcasterRegistry()


@pytest.fixture
def registry() -> EventRegistry:
    return EventRegistry()


@pytest.fixture
def registered_registry(registry: EventRegistry) -> EventRegistry:
    registry.register(OrderPlacedEvent)
    return registry


@pytest.fixture
def item_registry(upcaster_registry: UpcasterRegistry) -> EventRegistry:
    reg = EventRegistry(upcaster_registry=upcaster_registry)
    reg.register(ItemEvent)
    return reg


# ---------------------------------------------------------------------------
# Deserialization WITHOUT upcaster_registry (existing behavior)
# ---------------------------------------------------------------------------


class TestDeserializeWithoutUpcasterRegistry:
    """EventRegistry without an UpcasterRegistry behaves exactly as before."""

    def test_deserialize_round_trip(self, registered_registry: EventRegistry) -> None:
        event = OrderPlacedEvent(order_id="ORD-001", total=99.95)
        serialized = registered_registry.serialize(event)
        deserialized = registered_registry.deserialize(serialized)
        assert isinstance(deserialized, OrderPlacedEvent)
        assert deserialized.order_id == "ORD-001"
        assert deserialized.total == 99.95

    def test_deserialize_registered_type(
        self, registered_registry: EventRegistry
    ) -> None:
        data: dict[str, Any] = {
            "type": "OrderPlacedEvent",
            "data": {"order_id": "ORD-002", "total": 49.50},
        }
        result = registered_registry.deserialize(data)
        assert isinstance(result, OrderPlacedEvent)
        assert result.order_id == "ORD-002"

    def test_deserialize_weak_schema_fallback(self, registry: EventRegistry) -> None:
        data: dict[str, Any] = {"type": "Unknown", "data": {"key": "val"}}
        result = registry.deserialize(data)
        assert isinstance(result, GenericDomainEvent)

    def test_generic_domain_event_preserves_type_and_data(
        self, registry: EventRegistry
    ) -> None:
        data: dict[str, Any] = {
            "type": "LegacyEvent",
            "data": {"user_id": 42},
        }
        result = registry.deserialize(data)
        assert isinstance(result, GenericDomainEvent)
        assert result.type == "LegacyEvent"
        assert result.data == {"user_id": 42}

    def test_generic_domain_event_with_version(self, registry: EventRegistry) -> None:
        """GenericDomainEvent preserves the version from the envelope."""
        data: dict[str, Any] = {
            "type": "LegacyEvent",
            "version": 3,
            "data": {"user_id": 42},
        }
        result = registry.deserialize(data)
        assert isinstance(result, GenericDomainEvent)
        assert result.version == 3

    def test_deserialize_empty_data(self, registry: EventRegistry) -> None:
        data: dict[str, Any] = {"type": "EmptyEvent", "data": {}}
        result = registry.deserialize(data)
        assert isinstance(result, GenericDomainEvent)
        assert result.type == "EmptyEvent"
        assert result.data == {}


# ---------------------------------------------------------------------------
# Deserialization WITH upcaster_registry
# ---------------------------------------------------------------------------


class TestDeserializeWithUpcasterRegistry:
    """EventRegistry with an UpcasterRegistry applies upcasters before
    model_validate."""

    def test_applies_upcaster_chain(self, upcaster_registry: UpcasterRegistry) -> None:
        upcaster_registry.register(ItemV1ToV2Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        # V1 payload (no category field)
        data: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        result = reg.deserialize(data)
        assert isinstance(result, ItemEvent)
        # category should have been added by upcaster
        assert result.category == "general"
        assert result.item_name == "Widget"
        assert result.quantity == 5

    def test_upcaster_renames_field(self, upcaster_registry: UpcasterRegistry) -> None:
        upcaster_registry.register(PersonV1ToV2Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(PersonEvent)

        # V1 payload has "name" instead of "person_name"
        data: dict[str, Any] = {
            "type": "PersonEvent",
            "version": 1,
            "data": {"name": "Alice", "email": "alice@example.com"},
        }
        result = reg.deserialize(data)
        assert isinstance(result, PersonEvent)
        assert result.person_name == "Alice"
        assert result.email == "alice@example.com"

    def test_event_at_current_version_no_upcasters(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        # Already at V2 - no upcasters needed
        data: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 2,
            "data": {"item_name": "Widget", "quantity": 5, "category": "tools"},
        }
        result = reg.deserialize(data)
        assert isinstance(result, ItemEvent)
        assert result.item_name == "Widget"
        assert result.quantity == 5
        assert result.category == "tools"

    def test_unknown_type_falls_back_with_version(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        data: dict[str, Any] = {
            "type": "UnknownEvent",
            "version": 3,
            "data": {"some_field": "value"},
        }
        result = reg.deserialize(data)
        assert isinstance(result, GenericDomainEvent)
        assert result.type == "UnknownEvent"
        assert result.version == 3
        assert result.data == {"some_field": "value"}


# ---------------------------------------------------------------------------
# UpcastError propagation during deserialization
# ---------------------------------------------------------------------------


class TestDeserializeUpcastError:
    """Upcaster failures raise UpcastError during deserialize."""

    def test_upcaster_failure_propagates(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        upcaster_registry.register(FaultyItemUpcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        data: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        with pytest.raises(UpcastError) as exc_info:
            reg.deserialize(data)
        assert "Failed to upcast" in str(exc_info.value)

    def test_upcaster_failure_preserves_cause(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        upcaster_registry.register(FaultyItemUpcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        data: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        with pytest.raises(UpcastError) as exc_info:
            reg.deserialize(data)
        assert isinstance(exc_info.value.__cause__, ValueError)


# ---------------------------------------------------------------------------
# Same EventRegistry works for multiple upcaster chains
# ---------------------------------------------------------------------------


class TestMultipleUpcasterChains:
    """A single EventRegistry can handle different event types with their
    own upcaster chains."""

    def test_multiple_event_types_each_with_upcasters(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        upcaster_registry.register(ItemV1ToV2Upcaster)
        upcaster_registry.register(PersonV1ToV2Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)
        reg.register(PersonEvent)

        # ItemEvent V1
        item_data: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"item_name": "Gadget", "quantity": 10},
        }
        item_result = reg.deserialize(item_data)
        assert isinstance(item_result, ItemEvent)
        assert item_result.category == "general"

        # PersonEvent V1
        person_data: dict[str, Any] = {
            "type": "PersonEvent",
            "version": 1,
            "data": {"name": "Bob", "email": "bob@example.com"},
        }
        person_result = reg.deserialize(person_data)
        assert isinstance(person_result, PersonEvent)
        assert person_result.person_name == "Bob"
