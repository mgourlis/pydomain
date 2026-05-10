from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pydomain.ddd.domain_event import DomainEvent
from pydomain.ddd.id_generator import Uuid7Generator


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
