"""Tests for EventUpcaster and UpcasterRegistry.

Covers concrete upcaster subclasses, registry registration and resolution,
upcaster chaining, UpcastError on transformation failure, and overwrite
semantics.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from pydomain.es.exceptions import UpcastError
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry

# ===================================================================
# Test Upcaster Subclasses
# ===================================================================


class ItemV1ToV2Upcaster(EventUpcaster):
    """Migrates ItemEvent from V1 (category missing) to V2 (adds category)."""

    source_type: ClassVar[str] = "ItemEvent"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        event["category"] = "general"
        return event


class ItemV2ToV3Upcaster(EventUpcaster):
    """Migrates ItemEvent from V2 to V3 (renames item_name -> title)."""

    source_type: ClassVar[str] = "ItemEvent"
    source_version: ClassVar[int] = 2
    target_version: ClassVar[int] = 3

    def _transform(self, event: dict) -> dict:
        event["title"] = event.pop("item_name")
        return event


class FaultyUpcaster(EventUpcaster):
    """An upcaster whose _transform always raises."""

    source_type: ClassVar[str] = "FaultyEvent"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict) -> dict:
        msg = f"Missing required key: {event!r}"
        raise ValueError(msg)


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def registry() -> UpcasterRegistry:
    return UpcasterRegistry()


# ===================================================================
# EventUpcaster
# ===================================================================


class TestEventUpcaster:
    """Concrete upcaster subclass works correctly."""

    def test_upcaster_transforms_payload(self) -> None:
        upcaster = ItemV1ToV2Upcaster()
        result = upcaster.upcast({"item_name": "Widget", "quantity": 5})
        assert result == {"item_name": "Widget", "quantity": 5, "category": "general"}

    def test_upcaster_mutates_and_returns_same_dict(self) -> None:
        """The upcaster mutates the input dict in-place and returns it."""
        original = {"item_name": "Widget", "quantity": 5}
        upcaster = ItemV1ToV2Upcaster()
        result = upcaster.upcast(original)
        assert result is original
        assert result == {"item_name": "Widget", "quantity": 5, "category": "general"}

    def test_upcaster_idempotent(self) -> None:
        """Applying the same upcaster twice to the same input gives same result."""
        upcaster = ItemV1ToV2Upcaster()
        first = upcaster.upcast({"item_name": "Widget", "quantity": 5})
        second = upcaster.upcast({"item_name": "Widget", "quantity": 5})
        assert first == second

    def test_base_upcaster_raises_error(self) -> None:
        """Instantiating EventUpcaster directly and calling upcast fails
        because the required ClassVars are not set."""
        upcaster = EventUpcaster()
        with pytest.raises((NotImplementedError, AttributeError)):
            upcaster.upcast({})


# ===================================================================
# UpcastError
# ===================================================================


class TestUpcastError:
    """UpcastError when _transform fails."""

    def test_upcast_error_on_transform_failure(self) -> None:
        upcaster = FaultyUpcaster()
        with pytest.raises(UpcastError) as exc_info:
            upcaster.upcast({"bad": "data"})
        assert "Failed to upcast" in str(exc_info.value)

    def test_upcast_error_preserves_cause(self) -> None:
        upcaster = FaultyUpcaster()
        with pytest.raises(UpcastError) as exc_info:
            upcaster.upcast({"bad": "data"})
        assert isinstance(exc_info.value.__cause__, ValueError)

    def test_upcast_error_includes_type_and_version(self) -> None:
        upcaster = FaultyUpcaster()
        with pytest.raises(UpcastError) as exc_info:
            upcaster.upcast({"bad": "data"})
        msg = str(exc_info.value)
        assert "FaultyEvent" in msg
        assert "v1" in msg or "v1" in msg.lower() or "1" in msg

    def test_upcast_error_is_domain_error(self) -> None:
        from pydomain.ddd.exceptions import DomainError

        upcaster = FaultyUpcaster()
        with pytest.raises(DomainError):
            upcaster.upcast({"bad": "data"})


# ===================================================================
# UpcasterRegistry - Registration
# ===================================================================


class TestRegistryRegistration:
    """Registering upcasters in the UpcasterRegistry."""

    def test_register_and_resolve_single(self, registry: UpcasterRegistry) -> None:
        registry.register(ItemV1ToV2Upcaster)
        chain = registry.resolve("ItemEvent", 1)
        assert len(chain) == 1
        assert chain[0] is ItemV1ToV2Upcaster

    def test_register_missing_classvar_raises_type_error(
        self, registry: UpcasterRegistry
    ) -> None:
        class BadUpcaster(EventUpcaster):
            # Missing source_type and source_version
            target_version: ClassVar[int] = 2

            def _transform(self, event: dict) -> dict:
                return event

        with pytest.raises(TypeError, match="source_type"):
            registry.register(BadUpcaster)

    def test_re_register_same_key_overwrites(self, registry: UpcasterRegistry) -> None:
        class FirstUpcaster(EventUpcaster):
            source_type: ClassVar[str] = "ItemEvent"
            source_version: ClassVar[int] = 1
            target_version: ClassVar[int] = 100

            def _transform(self, event: dict) -> dict:
                event["first"] = True
                return event

        class SecondUpcaster(EventUpcaster):
            source_type: ClassVar[str] = "ItemEvent"
            source_version: ClassVar[int] = 1
            target_version: ClassVar[int] = 200

            def _transform(self, event: dict) -> dict:
                event["second"] = True
                return event

        registry.register(FirstUpcaster)
        registry.register(SecondUpcaster)  # Overwrites
        chain = registry.resolve("ItemEvent", 1)
        assert len(chain) == 1
        assert chain[0] is SecondUpcaster
        # Verify the second upcaster's transform is applied
        upcaster = chain[0]()
        result = upcaster.upcast({"key": "val"})
        assert result == {"key": "val", "second": True}

    def test_register_multiple_different_keys(self, registry: UpcasterRegistry) -> None:
        registry.register(ItemV1ToV2Upcaster)
        registry.register(ItemV2ToV3Upcaster)
        # Resolving from V1 follows the chain to V3
        chain_v1 = registry.resolve("ItemEvent", 1)
        assert len(chain_v1) == 2  # V1->V2 then V2->V3
        assert chain_v1[0] is ItemV1ToV2Upcaster
        assert chain_v1[1] is ItemV2ToV3Upcaster
        # Resolving from V2 only gets V2->V3
        chain_v2 = registry.resolve("ItemEvent", 2)
        assert len(chain_v2) == 1
        assert chain_v2[0] is ItemV2ToV3Upcaster


# ===================================================================
# UpcasterRegistry - Resolution
# ===================================================================


class TestRegistryResolution:
    """Resolving upcasters from the UpcasterRegistry."""

    def test_resolve_empty_chain_when_no_upcaster(
        self, registry: UpcasterRegistry
    ) -> None:
        chain = registry.resolve("ItemEvent", 1)
        assert chain == []

    def test_resolve_unknown_type_returns_empty(
        self, registry: UpcasterRegistry
    ) -> None:
        chain = registry.resolve("NonExistent", 1)
        assert chain == []

    def test_resolve_at_unknown_version_returns_empty(
        self, registry: UpcasterRegistry
    ) -> None:
        registry.register(ItemV1ToV2Upcaster)
        chain = registry.resolve("ItemEvent", 999)
        assert chain == []

    def test_resolve_chaining_v1_to_v2_to_v3(self, registry: UpcasterRegistry) -> None:
        """V1->V2->V3 resolves to [V1ToV2, V2ToV3] in order."""
        registry.register(ItemV1ToV2Upcaster)
        registry.register(ItemV2ToV3Upcaster)
        chain = registry.resolve("ItemEvent", 1)
        assert len(chain) == 2
        assert chain[0] is ItemV1ToV2Upcaster
        assert chain[1] is ItemV2ToV3Upcaster

    def test_chained_upcasters_transform_correctly(
        self, registry: UpcasterRegistry
    ) -> None:
        """Applying resolved chain transforms payload through all versions."""
        registry.register(ItemV1ToV2Upcaster)
        registry.register(ItemV2ToV3Upcaster)
        chain = registry.resolve("ItemEvent", 1)
        payload = {"item_name": "Widget", "quantity": 5}
        for upcaster_cls in chain:
            upcaster = upcaster_cls()
            payload = upcaster.upcast(payload)
        assert "item_name" not in payload  # renamed to "title" in V2->V3
        assert payload["title"] == "Widget"
        assert payload["quantity"] == 5
        assert payload["category"] == "general"

    def test_resolve_at_v2_only_gets_v2_to_v3(self, registry: UpcasterRegistry) -> None:
        """Starting at V2 only resolves upcasters for V2 and beyond."""
        registry.register(ItemV1ToV2Upcaster)
        registry.register(ItemV2ToV3Upcaster)
        chain = registry.resolve("ItemEvent", 2)
        assert len(chain) == 1
        assert chain[0] is ItemV2ToV3Upcaster

    def test_resolve_at_target_version_no_op(self, registry: UpcasterRegistry) -> None:
        """If no upcaster is registered for the given version, chain is empty."""
        registry.register(ItemV1ToV2Upcaster)
        registry.register(ItemV2ToV3Upcaster)
        chain = registry.resolve("ItemEvent", 3)  # current version
        assert chain == []
