from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ConfigDict, ValidationError

from pydomain.cqrs import IntegrationEvent
from tests.support.content_sentinel import CONTENT_SENTINEL, assert_absent_from_strings

UUID7_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

ISO8601_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$",
)


class TestAutoGeneration:
    def test_event_id_is_non_empty_str(self) -> None:
        event = IntegrationEvent()
        assert isinstance(event.event_id, str)
        assert len(event.event_id) > 0

    def test_event_id_is_valid_uuid7_format(self) -> None:
        event = IntegrationEvent()
        assert UUID7_PATTERN.match(event.event_id) is not None

    def test_occurred_at_is_valid_iso_8601(self) -> None:
        event = IntegrationEvent()
        assert ISO8601_PATTERN.match(event.occurred_at) is not None

    def test_unique_ids_across_100_instances(self) -> None:
        ids = {IntegrationEvent().event_id for _ in range(100)}
        assert len(ids) == 100

    def test_accepts_explicit_event_id(self) -> None:
        eid = "0194a2b0-1234-7abc-def0-123456789abc"
        event = IntegrationEvent(event_id=eid)
        assert event.event_id == eid

    def test_accepts_explicit_occurred_at(self) -> None:
        ts = "2024-01-15T10:30:00.123456+00:00"
        event = IntegrationEvent(occurred_at=ts)
        assert event.occurred_at == ts

    def test_accepts_both_explicit_values(self) -> None:
        eid = "0194a2b0-1234-7abc-def0-123456789abc"
        ts = "2024-01-15T10:30:00.123456+00:00"
        event = IntegrationEvent(event_id=eid, occurred_at=ts)
        assert event.event_id == eid
        assert event.occurred_at == ts


class TestImmutability:
    def test_cannot_set_event_id_after_construction(self) -> None:
        event = IntegrationEvent()
        with pytest.raises(ValidationError):
            event.event_id = "overwrite"  # type: ignore[misc]

    def test_cannot_set_occurred_at_after_construction(self) -> None:
        event = IntegrationEvent()
        with pytest.raises(ValidationError):
            event.occurred_at = "overwrite"  # type: ignore[misc]

    def test_cannot_delete_event_id(self) -> None:
        event = IntegrationEvent()
        with pytest.raises(ValidationError):
            del event.event_id

    def test_cannot_delete_occurred_at(self) -> None:
        event = IntegrationEvent()
        with pytest.raises(ValidationError):
            del event.occurred_at


class TestPrimitiveValidation:
    def test_accepts_str_field(self) -> None:
        class WithStr(IntegrationEvent):
            value: str

        event = WithStr(value="hello")
        assert event.value == "hello"

    def test_accepts_int_field(self) -> None:
        class WithInt(IntegrationEvent):
            value: int

        event = WithInt(value=42)
        assert event.value == 42

    def test_accepts_float_field(self) -> None:
        class WithFloat(IntegrationEvent):
            value: float

        event = WithFloat(value=3.14)
        assert event.value == 3.14

    def test_accepts_bool_field(self) -> None:
        class WithBool(IntegrationEvent):
            value: bool

        event = WithBool(value=True)
        assert event.value is True

    def test_accepts_dict_field(self) -> None:
        class WithDict(IntegrationEvent):
            value: dict

        event = WithDict(value={"key": "value"})
        assert event.value == {"key": "value"}

    def test_accepts_list_field(self) -> None:
        class WithList(IntegrationEvent):
            value: list

        event = WithList(value=[1, 2, 3])
        assert event.value == [1, 2, 3]

    def test_accepts_none_field(self) -> None:
        class WithNone(IntegrationEvent):
            value: None = None

        event = WithNone(value=None)
        assert event.value is None

    def test_accepts_optional_primitive_with_none(self) -> None:
        class WithOptional(IntegrationEvent):
            value: str | None = None

        event = WithOptional(value=None)
        assert event.value is None

        event2 = WithOptional(value="hello")
        assert event2.value == "hello"

    def test_rejects_uuid_field(self) -> None:
        class WithUuid(IntegrationEvent):
            uid: UUID

        with pytest.raises(ValueError, match="primitive"):
            WithUuid(uid=UUID("0194a2b0-1234-7abc-def0-123456789abc"))

    def test_rejects_datetime_field(self) -> None:
        class WithDatetime(IntegrationEvent):
            dt: datetime

        with pytest.raises(ValueError, match="primitive"):
            WithDatetime(dt=datetime.now(UTC))

    def test_rejects_decimal_field(self) -> None:
        class WithDecimal(IntegrationEvent):
            value: Decimal

        with pytest.raises(ValueError, match="primitive"):
            WithDecimal(value=Decimal("10.5"))

    def test_rejects_custom_object_field(self) -> None:
        class CustomObject:
            pass

        class WithCustom(IntegrationEvent):
            obj: CustomObject
            model_config = ConfigDict(arbitrary_types_allowed=True)

        with pytest.raises(ValueError, match="primitive"):
            WithCustom(obj=CustomObject())

    def test_rejects_bytes_field(self) -> None:
        class WithBytes(IntegrationEvent):
            data: bytes

        with pytest.raises(ValueError, match="primitive"):
            WithBytes(data=b"binary data")

    def test_rejects_set_field(self) -> None:
        class WithSet(IntegrationEvent):
            data: set

        with pytest.raises(ValueError, match="primitive"):
            WithSet(data={1, 2, 3})

    def test_rejects_tuple_field(self) -> None:
        class WithTuple(IntegrationEvent):
            data: tuple

        with pytest.raises(ValueError, match="primitive"):
            WithTuple(data=(1, 2, 3))


# ── DCE-122 CQRS-15: values nested in dict and list fields ─────────────

_CQRS15 = "DCE-122 CQRS-15: primitive validation checks only top-level fields"


class _WithAny(IntegrationEvent):
    value: Any
    model_config = ConfigDict(arbitrary_types_allowed=True)


class _SentinelObject:
    """A non-primitive whose repr carries content."""

    def __repr__(self) -> str:
        return f"_SentinelObject({CONTENT_SENTINEL})"


_NON_PRIMITIVES = st.sampled_from(
    [
        datetime(2026, 1, 1, tzinfo=UTC),
        UUID("0194a2b0-1234-7abc-def0-123456789abc"),
        Decimal("10.5"),
        b"binary",
        (1, 2),
        {1, 2},
        float("nan"),
        float("inf"),
        {1: "synthetic"},
    ]
)

_JSON_LEAVES = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=5)
)

_JSON_VALUES = st.recursive(
    _JSON_LEAVES,
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=5), children, max_size=3)
    ),
    max_leaves=10,
)


@st.composite
def _nested_non_primitive(draw: st.DrawFn) -> object:
    """A dict or list holding one non-primitive value at some depth."""
    value: object = draw(_NON_PRIMITIVES)
    for _ in range(draw(st.integers(min_value=1, max_value=4))):
        if draw(st.booleans()):
            siblings = draw(st.lists(_JSON_LEAVES, max_size=2))
            value = [*siblings, value]
        else:
            key = draw(st.text(max_size=5))
            siblings_map = draw(
                st.dictionaries(st.text(max_size=5), _JSON_LEAVES, max_size=2)
            )
            value = {**siblings_map, key: value}
    return value


class TestNestedPrimitiveValidation:
    def test_rejects_datetime_nested_in_dict_field(self) -> None:
        class WithDict(IntegrationEvent):
            data: dict[str, Any]

        with pytest.raises(ValidationError, match="primitive"):
            WithDict(data={"at": datetime(2026, 1, 1, tzinfo=UTC)})

    def test_rejects_datetime_nested_in_list_field(self) -> None:
        class WithList(IntegrationEvent):
            items: list[Any]

        with pytest.raises(ValidationError, match="primitive"):
            WithList(items=["ok", datetime(2026, 1, 1, tzinfo=UTC)])

    def test_nested_rejection_names_no_value(self) -> None:
        with pytest.raises(ValidationError) as caught:
            _WithAny(value={"note": [_SentinelObject()]})

        assert_absent_from_strings(error["msg"] for error in caught.value.errors())

    @settings(report_multiple_bugs=False)
    @given(value=_nested_non_primitive())
    def test_rejects_a_non_primitive_value_at_any_depth(self, value: object) -> None:
        with pytest.raises(ValidationError, match="primitive"):
            _WithAny(value=value)

    @given(value=_JSON_VALUES)
    def test_accepts_json_safe_values_at_any_depth(self, value: object) -> None:
        event = _WithAny(value=value)

        assert json.loads(json.dumps(event.model_dump()))["value"] == value


_B62 = "DCE-122 CQRS-15 (B-62): keys, floats, the path and stamp() are not checked"


_B70 = "DCE-122 B-70: the recursive check overflows on deep or cyclic payloads"


def _nested_lists(depth: int) -> object:
    value: object = "leaf"
    for _ in range(depth):
        value = [value]
    return value


class TestPayloadDepthAndCycles:
    """The strict-JSON walk is iterative, depth-limited and cycle-safe (B-70)."""

    def test_accepts_100_levels_of_nesting(self) -> None:
        _WithAny(value=_nested_lists(100))

    def test_rejects_101_levels_as_nested_too_deep(self) -> None:
        with pytest.raises(ValidationError, match=r"nested too deep at value\[0\]"):
            _WithAny(value=_nested_lists(101))

    def test_rejects_a_payload_past_the_recursion_limit(self) -> None:
        with pytest.raises(ValidationError, match="nested too deep"):
            _WithAny(value=_nested_lists(5000))

    def test_rejects_a_dict_cycle(self) -> None:
        cyclic: dict[str, Any] = {"note": "synthetic"}
        cyclic["self"] = cyclic

        with pytest.raises(
            ValidationError, match=r"contains a cycle at value\['self'\]"
        ):
            _WithAny(value=cyclic)

    def test_rejects_a_list_cycle(self) -> None:
        cyclic: list[Any] = ["synthetic"]
        cyclic.append(cyclic)

        with pytest.raises(ValidationError, match=r"contains a cycle at value\[1\]"):
            _WithAny(value=cyclic)

    def test_a_shared_container_is_not_a_cycle(self) -> None:
        shared = ["synthetic"]

        event = _WithAny(value={"a": shared, "b": shared})

        assert event.value == {"a": ["synthetic"], "b": ["synthetic"]}


class TestStrictJsonPayloads:
    def test_nested_message_names_field_type_and_path(self) -> None:
        class WithDict(IntegrationEvent):
            data: dict[str, Any]

        with pytest.raises(ValidationError) as caught:
            WithDict(data={"at": datetime(2026, 1, 1, tzinfo=UTC)})

        assert "Field 'data' has disallowed type 'datetime' at data['at']" in str(
            caught.value
        )

    def test_nested_list_path_uses_the_index(self) -> None:
        class WithList(IntegrationEvent):
            items: list[Any]

        with pytest.raises(ValidationError) as caught:
            WithList(items=["ok", {"lines": [Decimal("1.5")]}])

        assert "at items[1]['lines'][0]" in str(caught.value)

    def test_rejects_a_non_str_dict_key(self) -> None:
        class WithDict(IntegrationEvent):
            data: dict  # type: ignore[type-arg]

        with pytest.raises(ValidationError, match="primitive"):
            WithDict(data={1: "synthetic"})

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_a_non_finite_float(self, value: float) -> None:
        class WithFloat(IntegrationEvent):
            amount: float

        with pytest.raises(ValidationError, match="primitive"):
            WithFloat(amount=value)

    def test_stamp_rejects_non_string_ids(self) -> None:
        event = IntegrationEvent()

        with pytest.raises(ValidationError):
            event.stamp(
                correlation_id=cast(str, UUID("0194a2b0-1234-7abc-def0-123456789abc")),
                causation_id="synthetic-cause",
            )


class TestSerialization:
    def test_model_dump_contains_only_primitives(self) -> None:
        event = IntegrationEvent()
        data = event.model_dump()
        primitives = (str, int, float, bool, dict, list)
        for key, value in data.items():
            assert isinstance(value, primitives) or value is None, (
                f"Field {key!r} has non-primitive type {type(value).__name__}"
            )

    def test_model_dump_with_subclass_contains_only_primitives(self) -> None:
        class OrderEvent(IntegrationEvent):
            order_id: str
            amount: float

        event = OrderEvent(order_id="ORD-001", amount=99.99)
        data = event.model_dump()
        primitives = (str, int, float, bool, dict, list)
        for key, value in data.items():
            assert isinstance(value, primitives) or value is None, (
                f"Field {key!r} has non-primitive type {type(value).__name__}"
            )

    def test_model_validate_round_trip(self) -> None:
        event = IntegrationEvent()
        data = event.model_dump()
        restored = IntegrationEvent.model_validate(data)
        assert restored == event

    def test_model_validate_round_trip_with_subclass(self) -> None:
        class OrderEvent(IntegrationEvent):
            order_id: str
            amount: float

        event = OrderEvent(order_id="ORD-001", amount=99.99)
        data = event.model_dump()
        restored = OrderEvent.model_validate(data)
        assert restored == event
        assert restored.order_id == "ORD-001"
        assert restored.amount == 99.99

    def test_model_dump_json_round_trip(self) -> None:
        event = IntegrationEvent()
        json_str = event.model_dump_json()
        restored = IntegrationEvent.model_validate_json(json_str)
        assert restored == event

    def test_model_dump_json_round_trip_with_subclass(self) -> None:
        class OrderEvent(IntegrationEvent):
            order_id: str
            amount: float

        event = OrderEvent(order_id="ORD-001", amount=99.99)
        json_str = event.model_dump_json()
        restored = OrderEvent.model_validate_json(json_str)
        assert restored == event
        assert restored.order_id == "ORD-001"
        assert restored.amount == 99.99

    def test_missing_fields_get_auto_generated_on_validate(self) -> None:
        event = IntegrationEvent.model_validate({})
        assert isinstance(event.event_id, str)
        assert isinstance(event.occurred_at, str)

    def test_model_dump_includes_both_base_fields(self) -> None:
        event = IntegrationEvent()
        data = event.model_dump()
        assert "event_id" in data
        assert "occurred_at" in data


class TestEquality:
    def test_equal_when_all_fields_match(self) -> None:
        eid = "0194a2b0-1234-7abc-def0-123456789abc"
        ts = "2024-01-15T10:30:00.123456+00:00"
        a = IntegrationEvent(event_id=eid, occurred_at=ts)
        b = IntegrationEvent(event_id=eid, occurred_at=ts)
        assert a == b

    def test_not_equal_when_event_id_differs(self) -> None:
        ts = "2024-01-15T10:30:00.123456+00:00"
        a = IntegrationEvent(
            event_id="0194a2b0-1234-7abc-def0-123456789abc",
            occurred_at=ts,
        )
        b = IntegrationEvent(
            event_id="0194a2b0-1234-7abc-def0-123456789abd",
            occurred_at=ts,
        )
        assert a != b

    def test_not_equal_when_occurred_at_differs(self) -> None:
        eid = "0194a2b0-1234-7abc-def0-123456789abc"
        a = IntegrationEvent(
            event_id=eid,
            occurred_at="2024-01-15T10:30:00.123456+00:00",
        )
        b = IntegrationEvent(
            event_id=eid,
            occurred_at="2024-06-15T10:30:00.123456+00:00",
        )
        assert a != b

    def test_not_equal_to_dict_with_same_values(self) -> None:
        eid = "0194a2b0-1234-7abc-def0-123456789abc"
        ts = "2024-01-15T10:30:00.123456+00:00"
        event = IntegrationEvent(event_id=eid, occurred_at=ts)
        assert event != {"event_id": eid, "occurred_at": ts}

    def test_not_equal_to_none(self) -> None:
        event = IntegrationEvent()
        assert event is not None

    def test_autogenerated_instances_are_never_equal(self) -> None:
        a = IntegrationEvent()
        b = IntegrationEvent()
        assert a != b

    def test_not_equal_to_different_subclass_type(self) -> None:
        eid = "0194a2b0-1234-7abc-def0-123456789abc"
        ts = "2024-01-15T10:30:00.123456+00:00"

        class TypeA(IntegrationEvent):
            pass

        class TypeB(IntegrationEvent):
            pass

        a = TypeA(event_id=eid, occurred_at=ts)
        b = TypeB(event_id=eid, occurred_at=ts)
        assert a != b


class TestInheritance:
    class OrderPlaced(IntegrationEvent):
        order_id: str
        total_usd: float
        placed_at: str
        metadata: dict | None = None

    def test_subclass_with_primitive_fields_works(self) -> None:
        event = self.OrderPlaced(
            order_id="ORD-001",
            total_usd=99.99,
            placed_at="2024-01-15T10:30:00.123456+00:00",
        )
        assert event.order_id == "ORD-001"
        assert event.total_usd == 99.99
        assert event.placed_at == "2024-01-15T10:30:00.123456+00:00"
        assert event.metadata is None

    def test_subclass_auto_generates_event_id(self) -> None:
        event = self.OrderPlaced(
            order_id="ORD-001",
            total_usd=99.99,
            placed_at="2024-01-15T10:30:00.123456+00:00",
        )
        assert isinstance(event.event_id, str)
        assert len(event.event_id) > 0

    def test_subclass_auto_generates_occurred_at(self) -> None:
        event = self.OrderPlaced(
            order_id="ORD-001",
            total_usd=99.99,
            placed_at="2024-01-15T10:30:00.123456+00:00",
        )
        assert isinstance(event.occurred_at, str)
        assert len(event.occurred_at) > 0

    def test_subclass_validation_rejects_non_primitives(self) -> None:
        class WithUuid(IntegrationEvent):
            ref: UUID

        with pytest.raises(ValueError, match="primitive"):
            WithUuid(ref=UUID("0194a2b0-1234-7abc-def0-123456789abc"))

    def test_subclass_serialization_round_trip(self) -> None:
        event = self.OrderPlaced(
            order_id="ORD-001",
            total_usd=99.99,
            placed_at="2024-01-15T10:30:00.123456+00:00",
        )
        data = event.model_dump()
        restored = self.OrderPlaced.model_validate(data)
        assert restored == event
        assert restored.order_id == "ORD-001"
        assert restored.total_usd == 99.99
        assert restored.placed_at == "2024-01-15T10:30:00.123456+00:00"

    def test_subclass_serialization_json_round_trip(self) -> None:
        event = self.OrderPlaced(
            order_id="ORD-001",
            total_usd=99.99,
            placed_at="2024-01-15T10:30:00.123456+00:00",
        )
        json_str = event.model_dump_json()
        restored = self.OrderPlaced.model_validate_json(json_str)
        assert restored == event

    def test_subclass_is_frozen(self) -> None:
        event = self.OrderPlaced(
            order_id="ORD-001",
            total_usd=99.99,
            placed_at="2024-01-15T10:30:00.123456+00:00",
        )
        with pytest.raises(ValidationError):
            event.order_id = "ORD-002"  # type: ignore[misc]

    def test_subclass_rejects_delete(self) -> None:
        event = self.OrderPlaced(
            order_id="ORD-001",
            total_usd=99.99,
            placed_at="2024-01-15T10:30:00.123456+00:00",
        )
        with pytest.raises(ValidationError):
            del event.order_id

    def test_subclass_passes_explicit_event_id(self) -> None:
        eid = "0194a2b0-1234-7abc-def0-123456789abc"
        event = self.OrderPlaced(
            event_id=eid,
            order_id="ORD-001",
            total_usd=99.99,
            placed_at="2024-01-15T10:30:00.123456+00:00",
        )
        assert event.event_id == eid


class TestTracing:
    """Tests for correlation_id / causation_id stamping on IntegrationEvent."""

    def test_tracing_fields_default_to_none(self) -> None:
        event = IntegrationEvent()
        assert event.correlation_id is None
        assert event.causation_id is None

    def test_stamp_sets_both_ids(self) -> None:
        event = IntegrationEvent()
        stamped = event.stamp(
            correlation_id="0195e000-0000-7000-8000-000000000aaa",
            causation_id="0195e000-0000-7000-8000-000000000bbb",
        )
        assert stamped.correlation_id == "0195e000-0000-7000-8000-000000000aaa"
        assert stamped.causation_id == "0195e000-0000-7000-8000-000000000bbb"

    def test_stamp_returns_new_instance(self) -> None:
        event = IntegrationEvent()
        stamped = event.stamp(
            correlation_id="0195e000-0000-7000-8000-000000000aaa",
            causation_id="0195e000-0000-7000-8000-000000000bbb",
        )
        assert stamped is not event

    def test_stamp_preserves_original_event(self) -> None:
        event = IntegrationEvent()
        event.stamp(
            correlation_id="0195e000-0000-7000-8000-000000000aaa",
            causation_id="0195e000-0000-7000-8000-000000000bbb",
        )
        # Original unchanged
        assert event.correlation_id is None
        assert event.causation_id is None

    def test_stamp_preserves_event_id_and_occurred_at(self) -> None:
        eid = "0194a2b0-1234-7abc-def0-123456789abc"
        ts = "2024-01-15T10:30:00.123456+00:00"
        event = IntegrationEvent(event_id=eid, occurred_at=ts)
        stamped = event.stamp(
            correlation_id="0195e000-0000-7000-8000-000000000aaa",
            causation_id="0195e000-0000-7000-8000-000000000bbb",
        )
        assert stamped.event_id == eid
        assert stamped.occurred_at == ts

    def test_stamp_preserves_business_fields(self) -> None:
        class OrderPlaced(IntegrationEvent):
            order_id: str

        event = OrderPlaced(order_id="ORD-001")
        stamped = event.stamp(
            correlation_id="0195e000-0000-7000-8000-000000000aaa",
            causation_id="0195e000-0000-7000-8000-000000000bbb",
        )
        assert stamped.order_id == "ORD-001"

    def test_stamped_event_serialization_round_trip(self) -> None:
        cid = "0195e000-0000-7000-8000-000000000aaa"
        caid = "0195e000-0000-7000-8000-000000000bbb"

        event = IntegrationEvent()
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        data = stamped.model_dump()
        restored = IntegrationEvent.model_validate(data)
        assert restored == stamped
        assert restored.correlation_id == cid
        assert restored.causation_id == caid

    def test_stamped_subclass_serialization_json_round_trip(self) -> None:
        cid = "0195e000-0000-7000-8000-000000000aaa"
        caid = "0195e000-0000-7000-8000-000000000bbb"

        class OrderPlaced(IntegrationEvent):
            order_id: str

        event = OrderPlaced(order_id="ORD-001")
        stamped = event.stamp(correlation_id=cid, causation_id=caid)
        json_str = stamped.model_dump_json()
        restored = OrderPlaced.model_validate_json(json_str)
        assert restored == stamped
        assert restored.correlation_id == cid
        assert restored.causation_id == caid

    def test_explicit_tracing_ids_accepted(self) -> None:
        cid = "0195e000-0000-7000-8000-000000000aaa"
        caid = "0195e000-0000-7000-8000-000000000bbb"

        event = IntegrationEvent(
            correlation_id=cid,
            causation_id=caid,
        )
        assert event.correlation_id == cid
        assert event.causation_id == caid

    def test_tracing_ids_are_primitives_pass_validation(self) -> None:
        """str | None is in the allowed primitives tuple — must not raise."""
        event = IntegrationEvent(
            correlation_id="0195e000-0000-7000-8000-000000000aaa",
            causation_id="0195e000-0000-7000-8000-000000000bbb",
        )
        # If validation failed, construction would have raised
        assert event.correlation_id is not None

    def test_stamp_from_extracts_tracing_from_domain_event(self) -> None:
        from pydomain.ddd.domain_event import DomainEvent

        class OrderPlaced(IntegrationEvent):
            order_id: str

        domain_event = DomainEvent(
            correlation_id=UUID("0195e000-0000-7000-8000-000000000aaa"),
        )
        integration = OrderPlaced(order_id="ORD-001")
        stamped = integration.stamp_from(domain_event)

        # correlation_id copied from the domain event
        assert stamped.correlation_id == str(domain_event.correlation_id)
        # causation_id = domain_event.event_id (the direct cause)
        assert stamped.causation_id == str(domain_event.event_id)
        assert stamped.order_id == "ORD-001"

    def test_stamp_from_raises_on_none_correlation_id(self) -> None:
        from pydomain.ddd.domain_event import DomainEvent

        domain_event = DomainEvent()  # correlation_id is None
        integration = IntegrationEvent()

        with pytest.raises(
            ValueError,
            match=(
                r"^Cannot stamp from DomainEvent with None correlation_id\. "
                r"Ensure the event has been stamped by the UnitOfWork\.$"
            ),
        ):
            integration.stamp_from(domain_event)


class TestExports:
    def test_integration_event_importable_from_cqrs(self) -> None:
        from pydomain.cqrs import IntegrationEvent

        assert IntegrationEvent is not None
