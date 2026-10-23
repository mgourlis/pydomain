from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, ClassVar, Self
from uuid import UUID, uuid4

import pytest
from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.id_generator import IdGenerator, Uuid7Generator

# A per-subclass event id generator (DCE-125, DDD-09, ADR-070).
DISPATCH_EVENT_ID = UUID("00000000-0000-4000-8000-0000000000e1")


class FixedIdGenerator:
    def __init__(self, value: UUID) -> None:
        self.value = value

    def generate(self) -> UUID:
        return self.value


class ShipmentDispatched(DomainEvent):
    _id_generator: ClassVar[IdGenerator[UUID]] = FixedIdGenerator(DISPATCH_EVENT_ID)
    shipment_id: UUID


class ShipmentDelivered(DomainEvent):
    shipment_id: UUID


class InvoiceIssued(DomainEvent):
    invoice_id: UUID
    lines: list[int]


VALIDATOR_CALLS: list[str] = []


class CountedShipment(DomainEvent):
    """Records every run of its model validators."""

    @model_validator(mode="before")
    @classmethod
    def _count_before(cls, data: Any) -> Any:
        VALIDATOR_CALLS.append("before")
        return data

    @model_validator(mode="after")
    def _count_after(self) -> Self:
        VALIDATOR_CALLS.append("after")
        return self


class TracedTogether(DomainEvent):
    """Both tracing ids or neither, checked after validation."""

    @model_validator(mode="after")
    def _both_or_neither(self) -> Self:
        if (self.correlation_id is None) != (self.causation_id is None):
            raise ValueError("correlation_id and causation_id come together")
        return self


def _version_7(value: UUID) -> UUID:
    if value.version != 7:
        raise ValueError("tracing ids must be UUIDv7")
    return value


class V7InUnion(DomainEvent):
    correlation_id: Annotated[UUID, AfterValidator(_version_7)] | None = None


class V7OnField(DomainEvent):
    correlation_id: Annotated[
        UUID | None, AfterValidator(lambda v: v and _version_7(v))
    ] = None


class TestDomainEventAutoGeneration:
    def test_event_id_auto_generated_when_omitted(self) -> None:
        event = DomainEvent()
        assert isinstance(event.event_id, UUID)

    def test_event_id_is_uuid_version_7(self) -> None:
        event = DomainEvent()
        assert event.event_id.version == 7

    def test_event_id_unique_across_instances(self) -> None:
        ids = {DomainEvent().event_id for _ in range(100)}
        assert len(ids) == 100

    def test_accepts_explicit_event_id(self) -> None:
        eid = uuid4()
        event = DomainEvent(event_id=eid)
        assert event.event_id == eid

    def test_occurred_at_auto_generated_when_omitted(self) -> None:
        event = DomainEvent()
        assert isinstance(event.occurred_at, datetime)

    def test_occurred_at_is_utc(self) -> None:
        event = DomainEvent()
        assert event.occurred_at.tzinfo == UTC

    def test_occurred_at_is_recent(self) -> None:
        before = datetime.now(UTC)
        event = DomainEvent()
        after = datetime.now(UTC)
        tolerance = timedelta(seconds=1)
        assert before - tolerance <= event.occurred_at <= after + tolerance

    def test_accepts_explicit_occurred_at(self) -> None:
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        event = DomainEvent(occurred_at=ts)
        assert event.occurred_at == ts

    def test_both_fields_auto_generated_together(self) -> None:
        event = DomainEvent()
        assert isinstance(event.event_id, UUID)
        assert isinstance(event.occurred_at, datetime)

    def test_one_field_auto_generated_when_other_explicit(self) -> None:
        eid = uuid4()
        event = DomainEvent(event_id=eid)
        assert event.event_id == eid
        assert isinstance(event.occurred_at, datetime)

        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        event2 = DomainEvent(occurred_at=ts)
        assert event2.occurred_at == ts
        assert isinstance(event2.event_id, UUID)

    def test_explicit_none_event_id_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            DomainEvent(event_id=None)  # type: ignore[arg-type]

    def test_explicit_none_occurred_at_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            DomainEvent(occurred_at=None)  # type: ignore[arg-type]


class TestDomainEventImmutability:
    def test_cannot_set_event_id(self) -> None:
        event = DomainEvent()
        with pytest.raises(ValidationError):
            event.event_id = uuid4()  # type: ignore[misc]

    def test_cannot_set_occurred_at(self) -> None:
        event = DomainEvent()
        with pytest.raises(ValidationError):
            event.occurred_at = datetime.now(UTC)  # type: ignore[misc]

    def test_cannot_set_correlation_id(self) -> None:
        event = DomainEvent()
        with pytest.raises(ValidationError):
            event.correlation_id = uuid4()  # type: ignore[misc]

    def test_cannot_set_causation_id(self) -> None:
        event = DomainEvent()
        with pytest.raises(ValidationError):
            event.causation_id = uuid4()  # type: ignore[misc]

    def test_delattr_raises(self) -> None:
        event = DomainEvent()
        with pytest.raises(ValidationError):
            del event.event_id

    def test_model_copy_creates_new_instance(self) -> None:
        event = DomainEvent()
        cid = uuid4()
        copy = event.model_copy(update={"correlation_id": cid})
        assert copy.correlation_id == cid
        assert event.correlation_id is None
        assert copy is not event


class TestDomainEventEquality:
    def test_equal_when_all_fields_match(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = DomainEvent(event_id=eid, occurred_at=ts)
        b = DomainEvent(event_id=eid, occurred_at=ts)
        assert a == b

    def test_not_equal_when_event_id_differs(self) -> None:
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = DomainEvent(event_id=uuid4(), occurred_at=ts)
        b = DomainEvent(event_id=uuid4(), occurred_at=ts)
        assert a != b

    def test_not_equal_when_occurred_at_differs(self) -> None:
        eid = uuid4()
        a = DomainEvent(event_id=eid, occurred_at=datetime(2024, 1, 15, tzinfo=UTC))
        b = DomainEvent(event_id=eid, occurred_at=datetime(2024, 6, 15, tzinfo=UTC))
        assert a != b

    def test_not_equal_when_correlation_id_differs(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = DomainEvent(event_id=eid, occurred_at=ts, correlation_id=None)
        b = DomainEvent(event_id=eid, occurred_at=ts, correlation_id=uuid4())
        assert a != b

    def test_not_equal_when_causation_id_differs(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = DomainEvent(event_id=eid, occurred_at=ts, causation_id=None)
        b = DomainEvent(event_id=eid, occurred_at=ts, causation_id=uuid4())
        assert a != b

    def test_identity_not_used_for_equality(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = DomainEvent(event_id=eid, occurred_at=ts)
        b = DomainEvent(event_id=eid, occurred_at=ts)
        assert a is not b
        assert a == b

    def test_not_equal_to_different_type(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        event = DomainEvent(event_id=eid, occurred_at=ts)
        assert event != {"event_id": eid, "occurred_at": ts}

    def test_not_equal_to_none(self) -> None:
        event = DomainEvent()
        assert event != None  # noqa: E711

    def test_autogenerated_instances_are_never_equal(self) -> None:
        a = DomainEvent()
        b = DomainEvent()
        assert a != b


class TestDomainEventHashability:
    def test_equal_objects_have_equal_hashes(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = DomainEvent(event_id=eid, occurred_at=ts)
        b = DomainEvent(event_id=eid, occurred_at=ts)
        assert hash(a) == hash(b)

    def test_can_be_used_in_set(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        evt = DomainEvent(event_id=eid, occurred_at=ts)
        s = {evt, evt}
        assert len(s) == 1

    def test_can_be_used_as_dict_key(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        d = {DomainEvent(event_id=eid, occurred_at=ts): "value"}
        assert d[DomainEvent(event_id=eid, occurred_at=ts)] == "value"

    def test_different_events_have_different_hashes(self) -> None:
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = DomainEvent(event_id=uuid4(), occurred_at=ts)
        b = DomainEvent(event_id=uuid4(), occurred_at=ts)
        assert hash(a) != hash(b)

    def test_autogenerated_events_in_set_deduplication(self) -> None:
        s = {DomainEvent(), DomainEvent()}
        assert len(s) == 2


class TestDomainEventCorrelationCausation:
    def test_correlation_id_defaults_to_none(self) -> None:
        assert DomainEvent().correlation_id is None

    def test_causation_id_defaults_to_none(self) -> None:
        assert DomainEvent().causation_id is None

    def test_correlation_id_accepts_uuid(self) -> None:
        cid = uuid4()
        assert DomainEvent(correlation_id=cid).correlation_id == cid

    def test_causation_id_accepts_uuid(self) -> None:
        caid = uuid4()
        assert DomainEvent(causation_id=caid).causation_id == caid

    def test_both_correlation_and_causation_id_can_be_set(self) -> None:
        cid = uuid4()
        caid = uuid4()
        event = DomainEvent(correlation_id=cid, causation_id=caid)
        assert event.correlation_id == cid
        assert event.causation_id == caid

    def test_model_dump_includes_correlation_id(self) -> None:
        event = DomainEvent()
        assert "correlation_id" in event.model_dump()
        assert event.model_dump()["correlation_id"] is None

    def test_model_dump_includes_causation_id(self) -> None:
        event = DomainEvent()
        assert "causation_id" in event.model_dump()
        assert event.model_dump()["causation_id"] is None


class TestDomainEventConfigure:
    def test_configure_affects_new_events(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        try:
            DomainEvent.configure(id_generator=FixedGen())
            event = DomainEvent()
            assert event.event_id == fixed
        finally:
            DomainEvent.configure(id_generator=Uuid7Generator())

    def test_configure_with_uuid7_restores_default(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        try:
            DomainEvent.configure(id_generator=FixedGen())
        finally:
            DomainEvent.configure(id_generator=Uuid7Generator())

        ids = {DomainEvent().event_id for _ in range(10)}
        assert len(ids) == 10
        for eid in ids:
            assert eid.version == 7

    def test_configure_does_not_affect_previously_created_event(self) -> None:
        event_before = DomainEvent()
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        try:
            DomainEvent.configure(id_generator=FixedGen())
        finally:
            DomainEvent.configure(id_generator=Uuid7Generator())

        assert event_before.event_id != fixed

    def test_configure_affects_subclass_instances(self) -> None:
        fixed = uuid4()

        class OrderPlaced(DomainEvent):
            order_id: UUID

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        try:
            DomainEvent.configure(id_generator=FixedGen())
            event = OrderPlaced(order_id=uuid4())
            assert event.event_id == fixed
        finally:
            DomainEvent.configure(id_generator=Uuid7Generator())


class TestDomainEventSerialization:
    def test_model_dump_round_trip(self) -> None:
        event = DomainEvent()
        restored = DomainEvent.model_validate(event.model_dump())
        assert restored == event

    def test_model_dump_json_round_trip(self) -> None:
        event = DomainEvent()
        restored = DomainEvent.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_model_dump_includes_all_base_fields(self) -> None:
        event = DomainEvent()
        data = event.model_dump()
        for key in ("event_id", "occurred_at", "correlation_id", "causation_id"):
            assert key in data

    def test_json_serialization_handles_none_fields(self) -> None:
        event = DomainEvent()
        serialized = event.model_dump_json()
        assert isinstance(serialized, str)
        assert "null" in serialized

    def test_model_validate_from_dict(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        event = DomainEvent.model_validate({"event_id": eid, "occurred_at": ts})
        assert event.event_id == eid
        assert event.occurred_at == ts

    def test_model_validate_from_dict_auto_fills_missing(self) -> None:
        event = DomainEvent.model_validate({})
        assert isinstance(event.event_id, UUID)
        assert isinstance(event.occurred_at, datetime)


class TestDomainEventInheritance:
    class OrderPlaced(DomainEvent):
        order_id: UUID

    def test_subclass_adds_fields(self) -> None:
        oid = uuid4()
        event = self.OrderPlaced(order_id=oid)
        assert event.order_id == oid

    def test_subclass_auto_generates_event_id(self) -> None:
        event = self.OrderPlaced(order_id=uuid4())
        assert isinstance(event.event_id, UUID)

    def test_subclass_auto_generates_occurred_at(self) -> None:
        event = self.OrderPlaced(order_id=uuid4())
        assert isinstance(event.occurred_at, datetime)

    def test_subclass_is_frozen(self) -> None:
        event = self.OrderPlaced(order_id=uuid4())
        with pytest.raises(ValidationError):
            event.order_id = uuid4()  # type: ignore[misc]

    def test_subclass_equality_includes_subclass_fields(self) -> None:
        oid = uuid4()
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = self.OrderPlaced(event_id=eid, occurred_at=ts, order_id=oid)
        b = self.OrderPlaced(event_id=eid, occurred_at=ts, order_id=uuid4())
        assert a != b

    def test_different_subclasses_not_equal(self) -> None:
        class OrderCancelled(DomainEvent):
            order_id: UUID

        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        oid = uuid4()
        a = self.OrderPlaced(event_id=eid, occurred_at=ts, order_id=oid)
        b = OrderCancelled(event_id=eid, occurred_at=ts, order_id=oid)
        assert a != b

    def test_subclass_serialization_round_trip(self) -> None:
        oid = uuid4()
        event = self.OrderPlaced(order_id=oid)
        restored = self.OrderPlaced.model_validate(event.model_dump())
        assert restored == event
        assert restored.order_id == oid

    def test_subclass_hash_consistency(self) -> None:
        oid = uuid4()
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        a = self.OrderPlaced(event_id=eid, occurred_at=ts, order_id=oid)
        b = self.OrderPlaced(event_id=eid, occurred_at=ts, order_id=oid)
        assert hash(a) == hash(b)
        c = self.OrderPlaced(event_id=eid, occurred_at=ts, order_id=uuid4())
        assert hash(a) != hash(c)

    def test_subclass_inherits_correlation_causation_defaults(self) -> None:
        event = self.OrderPlaced(order_id=uuid4())
        assert event.correlation_id is None
        assert event.causation_id is None

    def test_subclass_respects_configure(self) -> None:
        fixed = uuid4()

        class FixedGen:
            def generate(self) -> UUID:
                return fixed

        try:
            DomainEvent.configure(id_generator=FixedGen())
            event = self.OrderPlaced(order_id=uuid4())
            assert event.event_id == fixed
        finally:
            DomainEvent.configure(id_generator=Uuid7Generator())


class TestDomainEventEdgeCases:
    def test_construct_with_only_correlation_id(self) -> None:
        cid = uuid4()
        event = DomainEvent(correlation_id=cid)
        assert event.correlation_id == cid
        assert isinstance(event.event_id, UUID)
        assert isinstance(event.occurred_at, datetime)

    def test_construct_with_only_causation_id(self) -> None:
        caid = uuid4()
        event = DomainEvent(causation_id=caid)
        assert event.causation_id == caid
        assert isinstance(event.event_id, UUID)
        assert isinstance(event.occurred_at, datetime)

    def test_construct_with_all_four_optional_fields(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        cid = uuid4()
        caid = uuid4()
        event = DomainEvent(
            event_id=eid, occurred_at=ts, correlation_id=cid, causation_id=caid
        )
        assert event.event_id == eid
        assert event.occurred_at == ts
        assert event.correlation_id == cid
        assert event.causation_id == caid

    def test_model_validate_empty_dict(self) -> None:
        event = DomainEvent.model_validate({})
        assert isinstance(event.event_id, UUID)
        assert isinstance(event.occurred_at, datetime)

    def test_occurred_at_preserves_microseconds(self) -> None:
        ts = datetime(2024, 1, 15, 10, 30, 0, 123456, tzinfo=UTC)
        event = DomainEvent(occurred_at=ts)
        assert event.occurred_at.microsecond == 123456
        restored = DomainEvent.model_validate(event.model_dump())
        assert restored.occurred_at.microsecond == 123456

    def test_event_id_must_be_uuid(self) -> None:
        with pytest.raises(ValidationError):
            DomainEvent(event_id="not-a-uuid")  # type: ignore[arg-type]

    def test_occurred_at_must_be_datetime(self) -> None:
        with pytest.raises(ValidationError):
            DomainEvent(occurred_at=[])  # type: ignore[arg-type]

    def test_correlation_id_must_be_uuid_or_none(self) -> None:
        with pytest.raises(ValidationError):
            DomainEvent(correlation_id="abc")  # type: ignore[arg-type]

    def test_repr_includes_field_values(self) -> None:
        eid = uuid4()
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        event = DomainEvent(event_id=eid, occurred_at=ts)
        r = repr(event)
        assert str(eid) in r
        assert "2024" in r


class TestDomainEventStamp:
    def test_stamp_sets_correlation_id(self) -> None:
        event = DomainEvent()
        cid = uuid4()
        caid = uuid4()
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        assert stamped.correlation_id == cid

    def test_stamp_sets_causation_id(self) -> None:
        event = DomainEvent()
        cid = uuid4()
        caid = uuid4()
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        assert stamped.causation_id == caid

    def test_stamp_returns_new_copy(self) -> None:
        event = DomainEvent()
        cid = uuid4()
        caid = uuid4()
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        assert stamped is not event

    def test_stamp_does_not_mutate_original(self) -> None:
        event = DomainEvent()
        original_id = event.event_id
        original_ts = event.occurred_at
        cid = uuid4()
        caid = uuid4()
        _ = event.stamp(correlation_id=cid, causation_id=caid)
        assert event.correlation_id is None
        assert event.causation_id is None
        assert event.event_id == original_id
        assert event.occurred_at == original_ts

    def test_stamp_preserves_event_id(self) -> None:
        eid = uuid4()
        event = DomainEvent(event_id=eid)
        cid = uuid4()
        caid = uuid4()
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        assert stamped.event_id == eid

    def test_stamp_preserves_occurred_at(self) -> None:
        ts = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        event = DomainEvent(occurred_at=ts)
        cid = uuid4()
        caid = uuid4()
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        assert stamped.occurred_at == ts

    def test_stamp_on_subclass_preserves_domain_fields(self) -> None:
        class OrderPlaced(DomainEvent):
            order_id: UUID

        oid = uuid4()
        event = OrderPlaced(order_id=oid)
        cid = uuid4()
        caid = uuid4()
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        assert isinstance(stamped, OrderPlaced)
        assert stamped.order_id == oid
        assert stamped.correlation_id == cid
        assert stamped.causation_id == caid
        assert event.correlation_id is None
        assert event.causation_id is None

    def test_stamped_copy_is_frozen(self) -> None:
        event = DomainEvent()
        cid = uuid4()
        caid = uuid4()
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        with pytest.raises(ValidationError):
            stamped.correlation_id = uuid4()  # type: ignore[misc]

    def test_stamp_then_stamp_again_replaces_ids(self) -> None:
        event = DomainEvent()
        cid1 = uuid4()
        cid2 = uuid4()
        caid1 = uuid4()
        caid2 = uuid4()
        stamped1 = event.stamp(correlation_id=cid1, causation_id=caid1)
        stamped2 = stamped1.stamp(correlation_id=cid2, causation_id=caid2)
        assert stamped2.correlation_id == cid2
        assert stamped2.causation_id == caid2
        assert stamped1.correlation_id == cid1
        assert stamped1.causation_id == caid1


class TestPerSubclassIdGenerator:
    """A subclass's own _id_generator fills event_id (arc42 8.11, ADR-070)."""

    def test_subclass_id_generator_is_honoured_by_events_and_commands(self) -> None:
        """Given an event subclass with its own generator, when it is built
        without an event_id, then the event_id comes from that generator."""
        assert ShipmentDispatched(shipment_id=uuid4()).event_id == DISPATCH_EVENT_ID

    def test_subclass_id_generator_does_not_reach_siblings_or_base(self) -> None:
        """Other event types keep the base generator."""
        assert ShipmentDelivered(shipment_id=uuid4()).event_id != DISPATCH_EVENT_ID
        assert DomainEvent().event_id != DISPATCH_EVENT_ID

    def test_explicit_event_id_wins_over_subclass_generator(self) -> None:
        explicit = uuid4()
        event = ShipmentDispatched(shipment_id=uuid4(), event_id=explicit)
        assert event.event_id == explicit

    def test_event_id_generation_does_not_mutate_input(self) -> None:
        """Validating a dict without event_id leaves the dict unchanged."""
        data = {"shipment_id": str(uuid4())}
        first = ShipmentDelivered.model_validate(data)
        second = ShipmentDelivered.model_validate(data)
        assert "event_id" not in data
        assert first.event_id != second.event_id


class TestStampIsolation:
    """The stamped copy shares no mutable payload with the original (ADR-011)."""

    def test_stamped_copy_does_not_share_payload_with_original(self) -> None:
        """Given an event with a list payload, when its stamped copy's list is
        changed, then the original's list is unchanged."""
        event = InvoiceIssued(invoice_id=uuid4(), lines=[1, 2])
        stamped = event.stamp(correlation_id=uuid4(), causation_id=uuid4())

        stamped.lines.append(3)

        assert event.lines == [1, 2]

    def test_stamped_copy_round_trips(self) -> None:
        """A stamped event validates again from its own dump."""
        event = InvoiceIssued(invoice_id=uuid4(), lines=[1, 2])
        stamped = event.stamp(correlation_id=uuid4(), causation_id=uuid4())
        assert InvoiceIssued.model_validate(stamped.model_dump()) == stamped


class TestStampValidation:
    """stamp() validates its ids as the constructor does (ADR-070 decision 5)."""

    @pytest.mark.parametrize(
        ("correlation_id", "causation_id"),
        [
            ("x", 123),
            ("not-a-uuid", uuid4()),
            (uuid4(), 123),
        ],
        ids=["both", "correlation", "causation"],
    )
    def test_stamp_rejects_non_uuid_ids(
        self, correlation_id: object, causation_id: object
    ) -> None:
        """Given an event, when it is stamped with an id that is not a UUID,
        then a ValidationError is raised and the original is unchanged."""
        event = InvoiceIssued(invoice_id=uuid4(), lines=[1])
        with pytest.raises(ValidationError):
            event.stamp(
                correlation_id=correlation_id,  # type: ignore[arg-type]
                causation_id=causation_id,  # type: ignore[arg-type]
            )
        assert event.correlation_id is None
        assert event.causation_id is None

    def test_stamp_coerces_uuid_strings(self) -> None:
        """A UUID string is converted to a UUID, as the constructor does."""
        correlation_id, causation_id = uuid4(), uuid4()
        stamped = InvoiceIssued(invoice_id=uuid4(), lines=[1]).stamp(
            correlation_id=str(correlation_id),  # type: ignore[arg-type]
            causation_id=str(causation_id),  # type: ignore[arg-type]
        )
        assert stamped.correlation_id == correlation_id
        assert stamped.causation_id == causation_id


class TestStampRunsOnlyTheIdValidation:
    """stamp() validates the two ids only; no model validator runs (ADR-070 A)."""

    def test_stamp_runs_no_model_validators(self) -> None:
        """Given an event with counting before and after model validators, when
        it is stamped, then neither validator runs."""
        event = CountedShipment()
        VALIDATOR_CALLS.clear()

        event.stamp(correlation_id=uuid4(), causation_id=uuid4())

        assert VALIDATOR_CALLS == []

    def test_stamp_keeps_a_both_or_neither_invariant(self) -> None:
        """An after-validator that needs both ids or neither accepts the stamp."""
        stamped = TracedTogether().stamp(correlation_id=uuid4(), causation_id=uuid4())
        assert stamped.correlation_id is not None
        assert stamped.causation_id is not None

    @pytest.mark.parametrize("event_type", [V7InUnion, V7OnField])
    def test_stamp_applies_the_fields_own_constraints(
        self, event_type: type[DomainEvent]
    ) -> None:
        """A constraint declared on the id field applies to stamp() too."""
        event = event_type()
        with pytest.raises(ValidationError):
            event.stamp(correlation_id=uuid4(), causation_id=uuid4())
        v7 = Uuid7Generator().generate()
        assert event.stamp(correlation_id=v7, causation_id=uuid4()).correlation_id == v7


class FieldValidatedTrace(DomainEvent):
    """Rejects every correlation_id through a @field_validator."""

    @field_validator("correlation_id")
    @classmethod
    def _reject(cls, value: UUID | None) -> UUID | None:
        if value is not None:
            raise ValueError("rejected by the field validator")
        return value


class TestStampSkipsFieldValidatorMethods:
    def test_a_subclass_field_validator_does_not_run_in_stamp(self) -> None:
        """Only the field's type and constraints apply in stamp(); a
        @field_validator method of the event class does not run."""
        with pytest.raises(ValidationError):
            FieldValidatedTrace(correlation_id=uuid4())
        stamped = FieldValidatedTrace().stamp(
            correlation_id=uuid4(), causation_id=uuid4()
        )
        assert stamped.correlation_id is not None


class TestIdGenerationPaths:
    """Where the per-subclass generator applies: every validated path."""

    def test_subclass_id_generator_is_honoured_from_json(self) -> None:
        payload = json.dumps({"shipment_id": str(uuid4())})
        event = ShipmentDispatched.model_validate_json(payload)
        assert event.event_id == DISPATCH_EVENT_ID

    def test_event_id_is_in_model_fields_set(self) -> None:
        """A generated event_id counts as set, so exclude_unset keeps it."""
        event = ShipmentDelivered(shipment_id=uuid4())
        assert "event_id" in event.model_fields_set
        assert "event_id" in event.model_dump(exclude_unset=True)

    def test_model_construct_uses_the_base_generator(self) -> None:
        """model_construct() skips validation, so the field's default factory,
        which uses the base class's generator, fills event_id."""
        event = ShipmentDispatched.model_construct(shipment_id=uuid4())
        assert event.event_id != DISPATCH_EVENT_ID


class CamelShipmentDelivered(DomainEvent):
    """An event whose fields have camelCase aliases."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True
    )
    shipment_id: UUID


class RequiredTraceDelivered(DomainEvent):
    """An event that narrows correlation_id to a required UUID."""

    correlation_id: UUID = Field(default_factory=uuid4)


class TestReviewProbesForIdsAndStamp:
    """Review probes (PR #103) for event ids and stamp()."""

    def test_explicit_event_id_given_by_its_alias_is_kept(self) -> None:
        """The id guard sees an id passed under the field's alias."""
        explicit, shipment = uuid4(), uuid4()
        event = CamelShipmentDelivered.model_validate(
            {"eventId": str(explicit), "shipmentId": str(shipment)}
        )
        assert event.event_id == explicit

    def test_stamp_refuses_none_for_a_narrowed_correlation_id(self) -> None:
        """stamp() validates against the subclass's field, not the base's."""
        with pytest.raises(ValidationError):
            RequiredTraceDelivered().stamp(correlation_id=None, causation_id=uuid4())


class TestEventIdMarker:
    """The marker of a generated event_id is invisible to dumps and to equality.

    ADR-080 point 1 (B-156, B-158): ``DomainEvent`` records whether it
    generated its ``event_id``, for the inbound gateway. These guards pin that
    the record changes neither ``model_dump`` nor equality; the gateway tests
    pin what it is for.
    """

    def test_event_id_marker_leaves_model_dump_and_exclude_unset_unchanged(
        self,
    ) -> None:
        shipment = uuid4()
        generated = ShipmentDelivered(shipment_id=shipment)
        explicit = ShipmentDelivered(event_id=uuid4(), shipment_id=shipment)

        for event in (generated, explicit):
            assert set(event.model_dump()) == {
                "event_id",
                "occurred_at",
                "event_version",
                "correlation_id",
                "causation_id",
                "shipment_id",
            }
            assert event.model_fields_set == {"event_id", "shipment_id"}
            assert set(event.model_dump(exclude_unset=True)) == {
                "event_id",
                "shipment_id",
            }
            assert set(json.loads(event.model_dump_json())) == set(event.model_dump())

    def test_round_trips_stay_equal_with_the_marker(self) -> None:
        """A generated event equals its explicit round trips, with equal hashes."""
        base = DomainEvent()
        event = ShipmentDelivered(shipment_id=uuid4())
        stamped = event.stamp(correlation_id=uuid4(), causation_id=uuid4())

        pairs = [
            (base, DomainEvent.model_validate(base.model_dump())),
            (base, DomainEvent.model_validate_json(base.model_dump_json())),
            (event, ShipmentDelivered.model_validate(event.model_dump())),
            (event, ShipmentDelivered.model_validate_json(event.model_dump_json())),
            (stamped, ShipmentDelivered.model_validate(stamped.model_dump())),
            (event, event.model_copy(update={"event_id": event.event_id})),
        ]

        for original, restored in pairs:
            assert restored == original
            assert original == restored
            assert hash(restored) == hash(original)
            assert len({original, restored}) == 1
