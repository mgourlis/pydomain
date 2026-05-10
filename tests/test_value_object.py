from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import Field, ValidationError

from pydomain.ddd import ValueObject


class Color(ValueObject):
    name: str
    hex_code: str


class Money(ValueObject):
    amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot add {self.currency} to {other.currency}"
            )
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __mul__(self, multiplier: int) -> Money:
        return Money(amount=self.amount * multiplier, currency=self.currency)


class TestFrozenImmutability:
    def test_cannot_set_attribute(self):
        c = Color(name="red", hex_code="#FF0000")
        with pytest.raises(ValidationError):
            c.name = "blue"  # type: ignore[misc]

    def test_cannot_set_attribute_via_model_config(self):
        c = Color(name="red", hex_code="#FF0000")
        with pytest.raises(ValidationError):
            c.hex_code = "#00FF00"  # type: ignore[misc]


class TestValueEquality:
    def test_equal_when_all_fields_match(self):
        a = Color(name="red", hex_code="#FF0000")
        b = Color(name="red", hex_code="#FF0000")
        assert a == b

    def test_not_equal_when_any_field_differs(self):
        a = Color(name="red", hex_code="#FF0000")
        b = Color(name="blue", hex_code="#0000FF")
        assert a != b

    def test_equal_with_different_types_not_equal(self):
        c = Color(name="red", hex_code="#FF0000")
        assert c != "red"

    def test_identity_not_used_for_equality(self):
        a = Color(name="red", hex_code="#FF0000")
        b = Color(name="red", hex_code="#FF0000")
        assert a is not b
        assert a == b


class TestHashability:
    def test_equal_objects_have_equal_hashes(self):
        a = Color(name="red", hex_code="#FF0000")
        b = Color(name="red", hex_code="#FF0000")
        assert hash(a) == hash(b)

    def test_can_be_used_in_set(self):
        colors = {
            Color(name="red", hex_code="#FF0000"),
            Color(name="red", hex_code="#FF0000"),
            Color(name="blue", hex_code="#0000FF"),
        }
        assert len(colors) == 2

    def test_can_be_used_as_dict_key(self):
        palette: dict[Any, str] = {
            Color(name="red", hex_code="#FF0000"): "warm",
        }
        assert palette[Color(name="red", hex_code="#FF0000")] == "warm"

    def test_not_equal_objects_have_different_hashes(self):
        a = Color(name="red", hex_code="#FF0000")
        b = Color(name="blue", hex_code="#0000FF")
        # Not strictly guaranteed but very likely for simple fields
        assert a != b


class TestSerialization:
    def test_model_dump_round_trip(self):
        original = Color(name="red", hex_code="#FF0000")
        data = original.model_dump()
        restored = Color.model_validate(data)
        assert restored == original
        assert restored is not original

    def test_model_dump_json_round_trip(self):
        original = Color(name="red", hex_code="#FF0000")
        json_str = original.model_dump_json()
        restored = Color.model_validate_json(json_str)
        assert restored == original

    def test_model_dump_includes_all_fields(self):
        c = Color(name="red", hex_code="#FF0000")
        data = c.model_dump()
        assert data == {"name": "red", "hex_code": "#FF0000"}

    def test_model_dump_with_decimal_round_trip(self):
        original = Money(amount=Decimal("10.50"), currency="USD")
        data = original.model_dump()
        restored = Money.model_validate(data)
        assert restored == original

    def test_model_dump_json_with_decimal_round_trip(self):
        original = Money(amount=Decimal("10.50"), currency="USD")
        json_str = original.model_dump_json()
        restored = Money.model_validate_json(json_str)
        assert restored == original


class TestClosureOfOperations:
    def test_model_copy_creates_new_instance_with_updates(self):
        red = Color(name="red", hex_code="#FF0000")
        crimson = red.model_copy(update={"hex_code": "#DC143C"})
        assert crimson.hex_code == "#DC143C"
        assert red.hex_code == "#FF0000"
        assert crimson is not red

    def test_subclass_add_returns_new_instance(self):
        a = Money(amount=Decimal("10.00"), currency="USD")
        b = Money(amount=Decimal("5.00"), currency="USD")
        result = a + b
        assert result.amount == Decimal("15.00")
        assert result.currency == "USD"

    def test_add_does_not_mutate_operands(self):
        a = Money(amount=Decimal("10.00"), currency="USD")
        b = Money(amount=Decimal("5.00"), currency="USD")
        _ = a + b
        assert a.amount == Decimal("10.00")
        assert b.amount == Decimal("5.00")

    def test_mul_returns_new_instance(self):
        m = Money(amount=Decimal("10.00"), currency="USD")
        result = m * 3
        assert result.amount == Decimal("30.00")
        assert result.currency == "USD"

    def test_add_with_different_currency_raises(self):
        a = Money(amount=Decimal("10.00"), currency="USD")
        b = Money(amount=Decimal("5.00"), currency="EUR")
        with pytest.raises(ValueError, match="Cannot add USD to EUR"):
            _ = a + b


class TestValueObjectInheritance:
    def test_subclass_preserves_frozen(self):
        class Point(ValueObject):
            x: float
            y: float

        p = Point(x=1.0, y=2.0)
        with pytest.raises(ValidationError):
            p.x = 3.0  # type: ignore[misc]

    def test_subclass_with_validators(self):
        class NonEmptyString(ValueObject):
            value: str

            @classmethod
            def validate_non_empty(cls, v: str) -> str:
                if not v.strip():
                    raise ValueError("value must not be empty")
                return v

        obj = NonEmptyString(value="hello")
        assert obj.value == "hello"

    def test_value_object_repr(self):
        c = Color(name="red", hex_code="#FF0000")
        r = repr(c)
        assert "name='red'" in r
        assert "hex_code='#FF0000'" in r
