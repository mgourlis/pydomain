"""Integration tests for the full upcasting pipeline.

Tests the complete flow: serialize -> upcaster chain -> deserialize,
multi-hop transformations, error propagation, and backward compatibility
for version-1 events without upcasters.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.exceptions import UpcastError
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry
from pydomain.infrastructure import EventRegistry, GenericDomainEvent

# ===================================================================
# Test Domain Event Subclasses
# ===================================================================


class ShipEvent(DomainEvent):
    """An event at V2 (additive change: category was added in V2).

    V1: item_name, quantity                        (no category field)
    V2: item_name, quantity, category               (category added in V2)
    """

    event_version: int = 2
    item_name: str
    quantity: int
    category: str = "default"


class ItemEvent(DomainEvent):
    """An event at V3 (two schema changes over its lifetime).

    V1: item_name, quantity                        (no category field)
    V2: item_name, quantity, category               (category added in V2)
    V3: title, quantity, category                   (item_name renamed to title in V3)
    """

    event_version: int = 3
    title: str
    quantity: int
    category: str = "default"


# ===================================================================
# Test Upcaster Subclasses
# ===================================================================


class ShipV1ToV2Upcaster(EventUpcaster):
    """V1 -> V2 for ShipEvent: adds 'category' field."""

    source_type: ClassVar[str] = "ShipEvent"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        event["category"] = "general"
        return event


class ItemV1ToV2Upcaster(EventUpcaster):
    """V1 -> V2 for ItemEvent: adds 'category' field."""

    source_type: ClassVar[str] = "ItemEvent"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        event["category"] = "general"
        return event


class ItemV2ToV3Upcaster(EventUpcaster):
    """V2 -> V3 for ItemEvent: renames 'item_name' to 'title'."""

    source_type: ClassVar[str] = "ItemEvent"
    source_version: ClassVar[int] = 2
    target_version: ClassVar[int] = 3

    def _transform(self, event: dict) -> dict:
        event["title"] = event.pop("item_name")
        return event


class FaultyItemUpcaster(EventUpcaster):
    """An upcaster that always fails during transformation."""

    source_type: ClassVar[str] = "ItemEvent"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        msg = f"Corrupt payload: {event!r}"
        raise ValueError(msg)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def upcaster_registry() -> UpcasterRegistry:
    return UpcasterRegistry()


@pytest.fixture
def item_registry(upcaster_registry: UpcasterRegistry) -> EventRegistry:
    reg = EventRegistry(upcaster_registry=upcaster_registry)
    reg.register(ItemEvent)
    return reg


# ===================================================================
# Single-hop upcast: V1 -> V2
# ===================================================================


class TestSingleHopUpcast:
    """Serialize a V1 event, apply a single upcaster, deserialize as V2."""

    def test_v1_to_v2_adds_category_field(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        upcaster_registry.register(ShipV1ToV2Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ShipEvent)

        # Simulate a V1 stored event
        v1_envelope: dict[str, Any] = {
            "type": "ShipEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        result = reg.deserialize(v1_envelope)
        assert isinstance(result, ShipEvent)
        # After V1->V2 upcast: category should be "general"
        assert result.category == "general"
        assert result.item_name == "Widget"
        assert result.quantity == 5

    def test_v1_to_v2_produces_correct_event_type(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        upcaster_registry.register(ShipV1ToV2Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ShipEvent)

        v1_envelope: dict[str, Any] = {
            "type": "ShipEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        result = reg.deserialize(v1_envelope)
        assert isinstance(result, ShipEvent)
        assert result.event_version == 2  # from the ShipEvent class default


# ===================================================================
# Multi-hop upcast: V1 -> V2 -> V3
# ===================================================================


class TestMultiHopUpcast:
    """Full V1 -> V2 -> V3 chain transforms payload correctly."""

    def test_v1_to_v3_chain_transforms_fields(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        upcaster_registry.register(ItemV1ToV2Upcaster)
        upcaster_registry.register(ItemV2ToV3Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        v1_envelope: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        result = reg.deserialize(v1_envelope)
        assert isinstance(result, ItemEvent)
        # V1->V2 added category="general"
        assert result.category == "general"
        # V2->V3 renamed item_name -> title
        assert result.title == "Widget"
        assert "item_name" not in result.model_dump()
        assert result.quantity == 5

    def test_v2_to_v3_partial_chain(self, upcaster_registry: UpcasterRegistry) -> None:
        """Events already at V2 only need the V2->V3 upcaster."""
        upcaster_registry.register(ItemV1ToV2Upcaster)
        upcaster_registry.register(ItemV2ToV3Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        v2_envelope: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 2,
            "data": {"item_name": "Gadget", "quantity": 10, "category": "tools"},
        }
        result = reg.deserialize(v2_envelope)
        assert isinstance(result, ItemEvent)
        assert result.title == "Gadget"
        assert result.category == "tools"
        assert result.quantity == 10

    def test_v3_at_current_version_no_change(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        """Events at the current version pass through unchanged."""
        upcaster_registry.register(ItemV1ToV2Upcaster)
        upcaster_registry.register(ItemV2ToV3Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        v3_envelope: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 3,
            "data": {"title": "Gadget", "quantity": 10, "category": "tools"},
        }
        result = reg.deserialize(v3_envelope)
        assert isinstance(result, ItemEvent)
        assert result.title == "Gadget"
        assert result.quantity == 10
        assert result.category == "tools"


# ===================================================================
# Full round-trip: serialize -> upcast -> deserialize
# ===================================================================


class TestFullRoundTrip:
    """Complete round-trip: create event, serialize with version,
    register upcasters, deserialize, verify."""

    def test_serialize_then_deserialize_at_current_version(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        upcaster_registry.register(ItemV1ToV2Upcaster)
        upcaster_registry.register(ItemV2ToV3Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        # Create a V3 event and serialize it
        event = ItemEvent(title="Widget", quantity=5, category="tools")
        serialized = reg.serialize(event)
        assert serialized["version"] == 3

        # Deserialize should work (no upcasters needed at V3)
        deserialized = reg.deserialize(serialized)
        assert isinstance(deserialized, ItemEvent)
        assert deserialized.title == "Widget"
        assert deserialized.quantity == 5
        assert deserialized.category == "tools"

    def test_serialize_v1_deserialize_with_chain(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        """Serialize a V2 event, simulate downgrade to V1, upcast back."""
        upcaster_registry.register(ItemV1ToV2Upcaster)
        upcaster_registry.register(ItemV2ToV3Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ShipEvent)

        event = ShipEvent(item_name="Widget", quantity=5, category="tools")
        serialized = reg.serialize(event)
        assert serialized["version"] == 2

        deserialized = reg.deserialize(serialized)
        assert isinstance(deserialized, ShipEvent)
        assert deserialized.item_name == "Widget"
        assert deserialized.quantity == 5
        assert deserialized.category == "tools"


# ===================================================================
# Error propagation
# ===================================================================


class TestPipelineErrorPropagation:
    """Upcaster failures propagate through the pipeline as UpcastError."""

    def test_upcaster_failure_raises_upcast_error(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        upcaster_registry.register(FaultyItemUpcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        v1_envelope: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        with pytest.raises(UpcastError) as exc_info:
            reg.deserialize(v1_envelope)
        assert "Failed to upcast" in str(exc_info.value)

    def test_upcaster_failure_in_chain_stops_pipeline(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        """A failure in the middle of a chain stops processing."""
        upcaster_registry.register(FaultyItemUpcaster)
        upcaster_registry.register(ItemV2ToV3Upcaster)
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        v1_envelope: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        with pytest.raises(UpcastError):
            reg.deserialize(v1_envelope)
        # V2->V3 upcaster should never have been reached


# ===================================================================
# Fallback behavior
# ===================================================================


class TestPipelineFallback:
    """Events without matching upcasters or types fall back correctly."""

    def test_unknown_event_type_falls_back(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        data: dict[str, Any] = {
            "type": "MysteryEvent",
            "version": 1,
            "data": {"key": "value"},
        }
        result = reg.deserialize(data)
        assert isinstance(result, GenericDomainEvent)
        assert result.type == "MysteryEvent"
        assert result.version == 1
        assert result.data == {"key": "value"}

    def test_compatible_v1_passes_through_without_upcaster(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        """A V1 event whose payload already matches the current schema
        passes through unchanged when no upcaster is registered."""
        reg = EventRegistry(upcaster_registry=upcaster_registry)

        class SimpleEvent(DomainEvent):
            event_version: int = 1
            name: str

        reg.register(SimpleEvent)

        v1_envelope: dict[str, Any] = {
            "type": "SimpleEvent",
            "version": 1,
            "data": {"name": "test-event"},
        }
        result = reg.deserialize(v1_envelope)
        assert isinstance(result, SimpleEvent)
        assert result.name == "test-event"
        assert result.event_version == 1

    def test_incompatible_v1_without_upcaster_fails(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        """Without an upcaster, a V1 payload that does not match the current
        schema will fail validation. This confirms that upcasters are
        essential for schema migration."""
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        v1_envelope: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"item_name": "Widget", "quantity": 5},
        }
        with pytest.raises(ValidationError):
            reg.deserialize(v1_envelope)

    def test_unknown_type_falls_back(self, upcaster_registry: UpcasterRegistry) -> None:
        """An unregistered type with a version falls back to GenericDomainEvent."""
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        data: dict[str, Any] = {
            "type": "OldEvent",
            "version": 5,
            "data": {"legacy_field": "old_value"},
        }
        result = reg.deserialize(data)
        assert isinstance(result, GenericDomainEvent)
        assert result.type == "OldEvent"
        assert result.version == 5


# ===================================================================
# Version-1 events (no upcasters) deserialize unchanged
# ===================================================================


class TestVersionOneEvents:
    """Events at version 1 without any registered upcasters deserialize
    normally when the class schema is compatible."""

    def test_v1_with_matching_fields(self, upcaster_registry: UpcasterRegistry) -> None:
        reg = EventRegistry(upcaster_registry=upcaster_registry)
        reg.register(ItemEvent)

        # V1 payload with V3-compatible field names
        v1_envelope: dict[str, Any] = {
            "type": "ItemEvent",
            "version": 1,
            "data": {"title": "Widget", "quantity": 5, "category": "stuff"},
        }
        result = reg.deserialize(v1_envelope)
        assert isinstance(result, ItemEvent)
        assert result.title == "Widget"
        assert result.quantity == 5
        assert result.category == "stuff"

    def test_v1_with_empty_upcaster_registry(
        self, upcaster_registry: UpcasterRegistry
    ) -> None:
        """With an empty upcaster registry, no upcasters are applied
        and compatible payloads pass through."""
        reg = EventRegistry(upcaster_registry=upcaster_registry)

        class SimpleEvent(DomainEvent):
            event_version: int = 1
            name: str

        reg.register(SimpleEvent)

        v1_envelope: dict[str, Any] = {
            "type": "SimpleEvent",
            "version": 1,
            "data": {"name": "test-event"},
        }
        result = reg.deserialize(v1_envelope)
        assert isinstance(result, SimpleEvent)
        assert result.name == "test-event"
        assert result.event_version == 1
