"""Tests for EventUpcaster and UpcasterRegistry.

Covers concrete upcaster subclasses, registry registration and resolution,
upcaster chaining, UpcastError on transformation failure, and the checks
``register`` makes on its input (ADR-069).
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

    def test_subclass_without_transform_names_itself(self) -> None:
        """A subclass that forgets _transform fails with its own name."""

        class NoTransform(EventUpcaster):
            source_type: ClassVar[str] = "ItemEvent"
            source_version: ClassVar[int] = 1
            target_version: ClassVar[int] = 2

        with pytest.raises(UpcastError) as exc_info:
            NoTransform().upcast({})
        assert isinstance(exc_info.value.__cause__, NotImplementedError)
        assert str(exc_info.value.__cause__) == "NoTransform must implement _transform"

    def test_transform_returning_a_non_dict_raises_upcast_error(self) -> None:
        """upcast() refuses a result that is not a dict, naming its type only."""

        class ReturnsNone(EventUpcaster):
            source_type: ClassVar[str] = "ItemEvent"
            source_version: ClassVar[int] = 1
            target_version: ClassVar[int] = 2

            def _transform(self, event: dict) -> dict:
                event["secret"] = "payload-value"
                return None  # type: ignore[return-value]

        with pytest.raises(UpcastError) as exc_info:
            ReturnsNone().upcast({})
        assert str(exc_info.value) == (
            "ReturnsNone returned NoneType, not a dict, when upcasting "
            "'ItemEvent' from v1 to v2"
        )


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

    def test_re_register_same_key_is_refused(self, registry: UpcasterRegistry) -> None:
        """A second upcaster for the same step raises and the first stays.

        This test asserted the overwrite until DCE-117 (ES-14, ADR-069).
        """

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
        with pytest.raises(ValueError, match="already registered: FirstUpcaster"):
            registry.register(SecondUpcaster)
        assert registry.resolve("ItemEvent", 1) == [FirstUpcaster]

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

    def test_downgrade_that_would_close_a_cycle_is_refused(
        self, registry: UpcasterRegistry
    ) -> None:
        """A v2 -> v1 upcaster is refused at registration, so no cycle forms.

        Until DCE-117 (ES-14, ADR-069) this registered the downgrade and
        expected resolve() to detect the cycle. The guard itself is tested by
        test_cycle_built_after_registration_raises_upcast_error_fast.
        """

        class V1ToV2(EventUpcaster):
            source_type: ClassVar[str] = "CycleEvent"
            source_version: ClassVar[int] = 1
            target_version: ClassVar[int] = 2

            def _transform(self, event: dict) -> dict:
                return event

        class V2ToV1(EventUpcaster):
            source_type: ClassVar[str] = "CycleEvent"
            source_version: ClassVar[int] = 2
            target_version: ClassVar[int] = 1  # loops back!

            def _transform(self, event: dict) -> dict:
                return event

        registry.register(V1ToV2)
        with pytest.raises(ValueError, match="must be greater than source_version"):
            registry.register(V2ToV1)
        assert registry.resolve("CycleEvent", 1) == [V1ToV2]


# ===================================================================
# DCE-117: register validates its input (ES-14, ADR-069)
# ===================================================================


def _upcaster(
    source: int | None = 1, target: int | None = 2, *, event_type: str | None = "Box"
) -> type[EventUpcaster]:
    """An upcaster class with the given class variables; None leaves one out."""
    namespace: dict[str, object] = {"_transform": lambda self, event: event}
    if event_type is not None:
        namespace["source_type"] = event_type
    if source is not None:
        namespace["source_version"] = source
    if target is not None:
        namespace["target_version"] = target
    return type("BoxUpcaster", (EventUpcaster,), namespace)


@pytest.mark.parametrize(
    "missing",
    [
        {"event_type": None},
        {"source": None},
        {"target": None},
    ],
    ids=["source_type", "source_version", "target_version"],
)
def test_register_without_one_class_variable_raises_type_error(
    registry: UpcasterRegistry, missing: dict[str, None]
) -> None:
    """Each of the three class variables is required on its own."""
    with pytest.raises(TypeError, match="must define"):
        registry.register(_upcaster(**missing))  # type: ignore[arg-type]
    assert registry.resolve("Box", 1) == []


def _register_twice(registry: UpcasterRegistry) -> None:
    registry.register(_upcaster(1, 2))
    registry.register(_upcaster(1, 3))


@pytest.mark.parametrize(
    "register",
    [
        _register_twice,
        lambda registry: registry.register(_upcaster(3, 1)),
        lambda registry: registry.register(_upcaster(2, 2)),
    ],
    ids=["duplicate", "downgrade", "same-version"],
)
def test_duplicate_or_downgrade_upcaster_registration_raises(
    registry: UpcasterRegistry, register: object
) -> None:
    """A second upcaster for a step, or a step that does not go up, is refused."""
    with pytest.raises(ValueError, match="'Box'"):
        register(registry)  # type: ignore[operator]


def test_refused_duplicate_keeps_the_first_upcaster(
    registry: UpcasterRegistry,
) -> None:
    """A refused registration does not replace the registered upcaster."""
    first = _upcaster(1, 2)
    registry.register(first)
    with pytest.raises(ValueError):
        registry.register(_upcaster(1, 2))
    assert registry.resolve("Box", 1) == [first]


class _CountedTarget(type):
    """Metaclass whose ``target_version`` can change after registration.

    Class variables are mutable, so a cycle can still be built after
    ``register`` checked the upcaster. The read counter makes a resolve
    that never detects the cycle fail at once instead of looping forever.
    """

    reads = 0
    next_version = 2

    @property
    def target_version(cls) -> int:
        _CountedTarget.reads += 1
        if _CountedTarget.reads > 100:
            msg = "resolve() kept following the cycle"
            raise AssertionError(msg)
        return _CountedTarget.next_version


def test_cycle_built_after_registration_raises_upcast_error_fast(
    registry: UpcasterRegistry,
) -> None:
    """The cycle guard stops a v1 -> v2 -> v1 loop at the repeated version."""

    class LoopV2(EventUpcaster, metaclass=_CountedTarget):
        source_type: ClassVar[str] = "Loop"
        source_version: ClassVar[int] = 2

        def _transform(self, event: dict) -> dict:
            return event

    _CountedTarget.reads = 0
    _CountedTarget.next_version = 3
    registry.register(_upcaster(1, 2, event_type="Loop"))
    registry.register(LoopV2)
    _CountedTarget.next_version = 1  # v2 now points back at v1

    with pytest.raises(UpcastError, match=r"cycle detected for 'Loop' at version 1"):
        registry.resolve("Loop", 1)
