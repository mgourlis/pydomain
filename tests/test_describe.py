"""Tests for ``pydomain._describe.describe``: value-free descriptions (ADR-071)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from pydomain._describe import describe
from tests.support.content_sentinel import CONTENT_SENTINEL


class Line(BaseModel):
    sku: str
    price: int


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: str
    lines: list[Line] = []
    tags: dict[str, int] = {}
    note: str = ""

    @field_validator("note")
    @classmethod
    def _no_shouting(cls, value: str) -> str:
        if value.isupper():
            msg = f"note is shouting: {value}"
            raise ValueError(msg)
        return value


class Period(BaseModel):
    start: int
    end: int

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.end < self.start:
            msg = f"end {self.end} before start {self.start}"
            raise ValueError(msg)
        return self


def _error(model: type[BaseModel], data: dict[str, Any]) -> ValidationError:
    with pytest.raises(ValidationError) as raised:
        model.model_validate(data)
    return raised.value


def test_other_exceptions_are_described_by_type_name_only() -> None:
    assert describe(RuntimeError(f"refused {CONTENT_SENTINEL}")) == "RuntimeError"
    assert describe(KeyError(CONTENT_SENTINEL)) == "KeyError"


def test_one_error_names_the_model_location_and_code() -> None:
    error = _error(Invoice, {})

    assert describe(error) == (
        "ValidationError: 1 error for Invoice at invoice_id (missing)"
    )


def test_several_errors_are_listed_in_order() -> None:
    error = _error(Invoice, {"lines": [{"sku": "a", "price": "many"}], "note": 5})

    assert describe(error) == (
        "ValidationError: 3 errors for Invoice at invoice_id (missing), "
        "lines.0.price (int_parsing), note (string_type)"
    )


def test_a_validator_message_is_left_out() -> None:
    error = _error(Invoice, {"invoice_id": "i1", "note": CONTENT_SENTINEL.upper()})

    assert (
        describe(error) == "ValidationError: 1 error for Invoice at note (value_error)"
    )


def test_an_error_on_the_whole_model_has_the_model_location() -> None:
    error = _error(Period, {"start": 2, "end": 1})

    assert (
        describe(error)
        == "ValidationError: 1 error for Period at <model> (value_error)"
    )


def test_dict_keys_and_unknown_keys_appear_in_locations() -> None:
    """Locations are copied as Pydantic reports them (ADR-071, B-30)."""
    error = _error(
        Invoice, {"invoice_id": "i1", "tags": {"priority": "high"}, "extra": 1}
    )

    assert describe(error) == (
        "ValidationError: 2 errors for Invoice at tags.priority (int_parsing), "
        "extra (extra_forbidden)"
    )


class Dosage(BaseModel):
    model_config = ConfigDict(title="Prescription dosage")

    milligrams: int

    @field_validator("milligrams")
    @classmethod
    def _in_range(cls, value: int) -> int:
        if value > 100:
            raise PydanticCustomError(f"above_{value}", "too high")
        return value


def test_a_custom_error_type_and_a_configured_title_are_copied() -> None:
    """Application-chosen strings reach the description unchanged (ADR-071)."""
    error = _error(Dosage, {"milligrams": 250})

    assert describe(error) == (
        "ValidationError: 1 error for Prescription dosage at milligrams (above_250)"
    )
