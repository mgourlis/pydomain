"""EventRegistry.deserialize() upcasts each event to its class version once.

DCE-117 (ADR-069, amends ADR-042): the version the upcaster chain reached is
written to ``event_version`` before validation, a chain that does not end at
the class's version raises ``UpcastError``, and only ``resolve()``'s
``KeyError`` means "unregistered". Contract: implement-upcaster.md,
"Application code only ever sees the latest event format."
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, create_model, field_validator

from pydomain.ddd.domain_event import DomainEvent
from pydomain.es.exceptions import UpcastError
from pydomain.es.upcasting import EventUpcaster, UpcasterRegistry
from pydomain.infrastructure import EventRegistry, GenericDomainEvent

Hop = tuple[int, int, bool]


class Shipment(DomainEvent):
    """Synthetic event at v3 whose upcasters record every hop they ran."""

    event_version: int = 3
    weight_grams: int
    hops: list[int] = []


def _shipment_class(version: int) -> type[BaseModel]:
    """A ``Shipment`` event class at *version*."""
    return create_model(
        "Shipment",
        __base__=DomainEvent,
        event_version=(int, version),
        weight_grams=(int, ...),
        hops=(list[int], []),
    )


def _shipment_upcaster(
    source: int, target: int, *, stamps_version: bool = False
) -> type[EventUpcaster]:
    """A non-idempotent upcaster: it records the hop and doubles the weight.

    *stamps_version* chooses whether ``_transform`` also sets
    ``event_version`` itself; the docs examples never did (A30).
    """

    def _transform(self: EventUpcaster, event: dict[str, Any]) -> dict[str, Any]:
        event["hops"] = [*event.get("hops", []), target]
        event["weight_grams"] = event["weight_grams"] * 2
        if stamps_version:
            event["event_version"] = target
        return event

    return type(
        f"ShipmentV{source}ToV{target}",
        (EventUpcaster,),
        {
            "source_type": "Shipment",
            "source_version": source,
            "target_version": target,
            "_transform": _transform,
        },
    )


def _registry(
    event_class: type[BaseModel], *upcasters: type[EventUpcaster]
) -> EventRegistry:
    upcaster_registry = UpcasterRegistry()
    for upcaster in upcasters:
        upcaster_registry.register(upcaster)
    registry = EventRegistry(upcaster_registry=upcaster_registry)
    registry.register(event_class)
    return registry


def _envelope(version: int, **data: Any) -> dict[str, Any]:
    """A stored envelope as ``serialize()`` of a class at *version* writes it."""
    return {
        "type": "Shipment",
        "version": version,
        "data": {"event_version": version, **data},
    }


@st.composite
def _upcaster_chains(draw: st.DrawFn) -> tuple[int, int, list[Hop]]:
    """A class version, a stored version and a complete chain between them.

    Hops may skip versions (v1 -> v3); each hop independently stamps
    ``event_version`` itself or leaves it stale.
    """
    class_version = draw(st.integers(min_value=1, max_value=8))
    stored = draw(st.integers(min_value=1, max_value=class_version))
    between = draw(
        st.lists(st.integers(min_value=stored, max_value=class_version), unique=True)
    )
    versions = sorted({stored, class_version, *between})
    hops = [
        (source, target, draw(st.booleans()))
        for source, target in zip(versions, versions[1:], strict=False)
    ]
    return class_version, stored, hops


# ---------------------------------------------------------------------------
# The chain runs once
# ---------------------------------------------------------------------------


@given(chain=_upcaster_chains(), weight=st.integers(min_value=0, max_value=10**6))
def test_round_trip_is_stable_over_random_upcaster_chains(
    chain: tuple[int, int, list[Hop]], weight: int
) -> None:
    """Read, serialize and read again gives the first read back."""
    class_version, stored, hops = chain
    registry = _registry(
        _shipment_class(class_version),
        *(_shipment_upcaster(s, t, stamps_version=stamp) for s, t, stamp in hops),
    )

    first = registry.deserialize(_envelope(stored, weight_grams=weight))
    second = registry.deserialize(registry.serialize(first))

    assert first.event_version == class_version  # type: ignore[attr-defined]
    assert first.hops == [target for _, target, _ in hops]  # type: ignore[attr-defined]
    assert second == first


def test_non_idempotent_upcaster_runs_once_across_a_round_trip() -> None:
    """The audit probe: an upcaster that scales a value scales it once."""
    registry = _registry(_shipment_class(2), _shipment_upcaster(1, 2))

    first = registry.deserialize(_envelope(1, weight_grams=25))
    again = registry.deserialize(registry.serialize(first))

    assert first.weight_grams == 50  # type: ignore[attr-defined]
    assert again.weight_grams == 50  # type: ignore[attr-defined]


def test_deserialize_stamps_the_version_the_chain_reached() -> None:
    """Upcasters need not set event_version; the registry sets it."""
    registry = _registry(Shipment, _shipment_upcaster(1, 2), _shipment_upcaster(2, 3))

    event = registry.deserialize(_envelope(1, weight_grams=1))

    assert isinstance(event, Shipment)
    assert event.event_version == 3
    assert registry.serialize(event)["version"] == 3


def test_deserialize_stamps_the_version_when_the_payload_has_none() -> None:
    """A payload without event_version takes the envelope's, not the default."""
    registry = _registry(Shipment, _shipment_upcaster(2, 3))

    event = registry.deserialize(
        {"type": "Shipment", "version": 2, "data": {"weight_grams": 1}}
    )

    assert isinstance(event, Shipment)
    assert event.event_version == 3
    assert event.hops == [3]


def test_payload_at_the_class_version_is_not_upcast() -> None:
    """An event already at the class's version passes through unchanged."""
    registry = _registry(Shipment, _shipment_upcaster(1, 2), _shipment_upcaster(2, 3))

    event = registry.deserialize(_envelope(3, weight_grams=7))

    assert event == Shipment(
        event_id=event.event_id,  # type: ignore[attr-defined]
        occurred_at=event.occurred_at,  # type: ignore[attr-defined]
        weight_grams=7,
    )


# ---------------------------------------------------------------------------
# The chain must end at the class version
# ---------------------------------------------------------------------------


def test_chain_gap_raises_upcast_error() -> None:
    """v1 -> v2 registered, class at v3: the v2 payload is never validated."""
    registry = _registry(Shipment, _shipment_upcaster(1, 2))

    with pytest.raises(UpcastError, match=r"'Shipment'.*v2.*v3"):
        registry.deserialize(_envelope(1, weight_grams=1))


def test_stored_version_without_any_upcaster_raises_upcast_error() -> None:
    """A v1 payload whose fields happen to fit the v3 class still raises."""
    registry = _registry(Shipment)

    with pytest.raises(UpcastError, match="Shipment"):
        registry.deserialize(_envelope(1, weight_grams=1))


def test_chain_beyond_the_class_version_raises_upcast_error() -> None:
    """A chain that overshoots the class does not end at the class's version."""
    registry = _registry(Shipment, _shipment_upcaster(1, 2), _shipment_upcaster(2, 4))

    with pytest.raises(UpcastError, match="Shipment"):
        registry.deserialize(_envelope(1, weight_grams=1))


def test_stored_version_beyond_the_class_version_validates_unchanged() -> None:
    """An event written by newer code (a rolling deploy) reaches validation as it is.

    No upcaster runs, even one registered for the newer version, and the
    payload's own event_version is not replaced by the older class's.
    """
    registry = _registry(Shipment, _shipment_upcaster(4, 5))

    event = registry.deserialize(_envelope(4, weight_grams=1))

    assert isinstance(event, Shipment)
    assert event.event_version == 4
    assert (event.weight_grams, event.hops) == (1, [])


def test_chain_from_the_class_version_raises_upcast_error() -> None:
    """An upcaster registered from the class's own version overshoots it."""
    registry = _registry(Shipment, _shipment_upcaster(3, 4))

    with pytest.raises(UpcastError, match="ends at v4, but the class is at v3"):
        registry.deserialize(_envelope(3, weight_grams=1))


class Parcel(BaseModel):
    """A registered model without an ``event_version`` field."""

    label: str


class ParcelV1ToV2(EventUpcaster):
    source_type: ClassVar[str] = "Parcel"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        return event


def test_chain_for_a_model_without_event_version_raises_upcast_error() -> None:
    """A model without an event_version field is at version 1."""
    registry = _registry(Parcel, ParcelV1ToV2)

    with pytest.raises(UpcastError, match="Parcel"):
        registry.deserialize({"type": "Parcel", "data": {"label": "P-1"}})


def test_model_without_event_version_round_trips_with_upcaster_registry() -> None:
    """A plain model is at version 1 and gets no event_version field added."""
    registry = _registry(Parcel)

    parcel = registry.deserialize(registry.serialize(Parcel(label="P-1")))

    assert parcel == Parcel(label="P-1")


def test_stale_version_without_upcaster_registry_is_unchanged() -> None:
    """Without an UpcasterRegistry, versioning stays opt-in."""
    registry = EventRegistry()
    registry.register(Shipment)

    event = registry.deserialize(_envelope(1, weight_grams=1))

    assert isinstance(event, Shipment)
    assert event.event_version == 1


# ---------------------------------------------------------------------------
# Only resolve() decides "unregistered"
# ---------------------------------------------------------------------------


class Lookup(DomainEvent):
    """An event whose validator raises KeyError on an unknown code."""

    code: str

    @field_validator("code")
    @classmethod
    def known_code(cls, value: str) -> str:
        return {"A": "alpha"}[value]


def test_validator_key_error_is_not_reported_as_unregistered() -> None:
    """A KeyError from a registered class's validator reaches the caller."""
    registry = EventRegistry()
    registry.register(Lookup)

    with pytest.raises(KeyError, match="B"):
        registry.deserialize({"type": "Lookup", "data": {"code": "B"}})


class Crate(DomainEvent):
    event_version: int = 2
    size: int


class CrateV1ToV2(EventUpcaster):
    """Overrides upcast() itself, so nothing wraps its exceptions."""

    source_type: ClassVar[str] = "Crate"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def upcast(self, event: dict[str, Any]) -> dict[str, Any]:
        event["size"] = event["old_size"]
        return event


def test_upcast_override_key_error_is_not_reported_as_unregistered() -> None:
    """A KeyError from an upcaster's upcast() override reaches the caller."""
    registry = _registry(Crate, CrateV1ToV2)

    with pytest.raises(KeyError, match="old_size"):
        registry.deserialize({"type": "Crate", "version": 1, "data": {}})


def test_unregistered_type_still_falls_back_with_upcaster_registry() -> None:
    """Narrowing the KeyError keeps the GenericDomainEvent fallback (ADR-044)."""
    registry = EventRegistry(upcaster_registry=UpcasterRegistry())

    event = registry.deserialize({"type": "Gone", "version": 2, "data": {"a": 1}})

    assert event == GenericDomainEvent(type="Gone", data={"a": 1}, version=2)
