"""Tests for DomainEvent.event_version field.

Covers the default value, subclass overrides, frozen immutability,
serialization via EventRegistry, and deserialization fallback.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure import EventRegistry


class TestEventVersionDefault:
    """event_version defaults to 1."""

    def test_default_value_is_1(self) -> None:
        event = DomainEvent()
        assert event.event_version == 1

    def test_included_in_model_dump(self) -> None:
        event = DomainEvent()
        dump = event.model_dump()
        assert "event_version" in dump
        assert dump["event_version"] == 1

    def test_included_in_repr(self) -> None:
        event = DomainEvent()
        assert "event_version=1" in repr(event)


class TestEventVersionInheritance:
    """Subclasses can override event_version."""

    def test_subclass_can_override_default(self) -> None:
        class OrderPlaced(DomainEvent):
            event_version: int = 2
            order_id: str

        event = OrderPlaced(order_id="ORD-001")
        assert event.event_version == 2

    def test_subclass_preserves_base_default(self) -> None:
        class OrderPlaced(DomainEvent):
            order_id: str

        event = OrderPlaced(order_id="ORD-001")
        assert event.event_version == 1

    def test_explicit_version_via_constructor(self) -> None:
        class OrderPlaced(DomainEvent):
            event_version: int = 2
            order_id: str

        event = OrderPlaced(order_id="ORD-001", event_version=3)
        assert event.event_version == 3

    def test_model_dump_reflects_override(self) -> None:
        class OrderPlaced(DomainEvent):
            event_version: int = 2
            order_id: str

        event = OrderPlaced(order_id="ORD-001")
        dump = event.model_dump()
        assert dump["event_version"] == 2


class TestEventVersionImmutability:
    """event_version is frozen like all other DomainEvent fields."""

    def test_cannot_set_after_creation(self) -> None:
        event = DomainEvent()
        with pytest.raises(ValidationError):
            event.event_version = 2  # type: ignore[misc]

    def test_cannot_set_on_subclass_instance(self) -> None:
        class OrderPlaced(DomainEvent):
            event_version: int = 2
            order_id: str

        event = OrderPlaced(order_id="ORD-001")
        with pytest.raises(ValidationError):
            event.event_version = 3  # type: ignore[misc]

    def test_model_copy_creates_new_instance(self) -> None:
        """model_copy on a frozen model creates a new instance with updated fields."""
        event = DomainEvent()
        updated = event.model_copy(update={"event_version": 3})
        assert updated.event_version == 3
        assert event.event_version == 1
        assert updated is not event


class TestEventVersionSerialization:
    """Version field behavior during EventRegistry serialization."""

    def test_serialize_includes_version_in_envelope(self) -> None:
        class OrderPlaced(DomainEvent):
            order_id: str

        registry = EventRegistry()
        event = OrderPlaced(order_id="ORD-001")
        result = registry.serialize(event)
        assert "version" in result
        assert result["version"] == 1

    def test_serialize_with_custom_version(self) -> None:
        class OrderShipped(DomainEvent):
            event_version: int = 5
            shipment_id: str

        registry = EventRegistry()
        event = OrderShipped(shipment_id="SHP-001")
        result = registry.serialize(event)
        assert result["version"] == 5

    def test_plain_basemodel_omits_version(self) -> None:
        """A plain BaseModel (no event_version attr) does not include version key."""

        class PlainEvent(BaseModel):
            order_id: str

        registry = EventRegistry()
        event = PlainEvent(order_id="ORD-001")
        result = registry.serialize(event)
        assert "version" not in result

    def test_deserialize_without_version_defaults_to_1(self) -> None:
        class OrderPlaced(DomainEvent):
            order_id: str

        registry = EventRegistry()
        registry.register(OrderPlaced)
        data = {"type": "OrderPlaced", "data": {"order_id": "ORD-001"}}
        result = registry.deserialize(data)
        assert isinstance(result, OrderPlaced)
        assert result.event_version == 1

    def test_serialize_round_trip_preserves_event_version(self) -> None:
        class OrderPlaced(DomainEvent):
            event_version: int = 2
            order_id: str

        registry = EventRegistry()
        registry.register(OrderPlaced)
        event = OrderPlaced(order_id="ORD-001")
        serialized = registry.serialize(event)
        deserialized = registry.deserialize(serialized)
        assert isinstance(deserialized, OrderPlaced)
        # event_version in payload (2) is used by model_validate
        assert deserialized.event_version == 2
        assert deserialized.order_id == "ORD-001"
