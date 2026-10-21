"""Tests for ``pydomain._describe.describe``: value-free descriptions (ADR-071)."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Literal

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


# ── Review probes (PR #107) ─────────────────────────────────────────────


class Stage(enum.Enum):
    DRAFT = "draft"


class Shipment(BaseModel):
    """Every kind of field a payload commonly has, plus validators that quote values."""

    lines: list[Line] = []
    by_sku: dict[str, Line] = {}
    stage: Stage = Stage.DRAFT
    kind: Literal["parcel", "pallet"] = "parcel"
    weights: list[int] = []
    shipped_at: datetime | None = None
    note: str = ""

    @field_validator("note")
    @classmethod
    def _custom(cls, value: str) -> str:
        if value:
            raise PydanticCustomError(
                "note_rejected", "rejected {note}", {"note": value}
            )
        return value

    @model_validator(mode="after")
    def _not_empty(self) -> Shipment:
        if self.weights == [0]:
            msg = f"empty shipment {self.lines!r}"
            raise ValueError(msg)
        return self


def test_describe_never_copies_values_for_any_field_kind() -> None:
    """Nested, dict, list, enum, Literal, datetime, model-level and custom errors."""
    s = CONTENT_SENTINEL
    errors = [
        _error(
            Shipment,
            {
                "lines": [{"sku": s, "price": s}],
                "by_sku": {"a1": {"sku": "a1", "price": s}},
                "stage": s,
                "kind": s,
                "weights": [1, s],
                "shipped_at": s,
                "note": s,
            },
        ),
        _error(Shipment, {"weights": [0], "lines": [{"sku": s, "price": 1}]}),
    ]
    with pytest.raises(ValidationError) as from_json:
        Shipment.model_validate_json(f'{{"weights": [1, "{s}"], "stage": "{s}"}}')
    errors.append(from_json.value)

    descriptions = [describe(error) for error in errors]

    assert descriptions == [
        "ValidationError: 7 errors for Shipment at lines.0.price (int_parsing), "
        "by_sku.a1.price (int_parsing), stage (enum), kind (literal_error), "
        "weights.1 (int_parsing), shipped_at (datetime_from_date_parsing), "
        "note (note_rejected)",
        "ValidationError: 1 error for Shipment at <model> (value_error)",
        "ValidationError: 2 errors for Shipment at stage (enum), "
        "weights.1 (int_parsing)",
    ]
    assert not any(s in description for description in descriptions)


def test_an_exception_group_is_described_by_its_type_only() -> None:
    try:
        try:
            raise KeyError(CONTENT_SENTINEL)
        except KeyError as cause:
            raise ExceptionGroup(
                CONTENT_SENTINEL, [ValueError(CONTENT_SENTINEL)]
            ) from cause
    except ExceptionGroup as group:
        assert describe(group) == "ExceptionGroup"


class TitledWithContent(BaseModel):
    model_config = ConfigDict(title=f"Title {CONTENT_SENTINEL}")

    count: int


class TypedFromValue(BaseModel):
    note: str

    @field_validator("note")
    @classmethod
    def _typed_from_value(cls, value: str) -> str:
        raise PydanticCustomError(f"bad_{value}", "message {v}", {"v": value})


@pytest.mark.parametrize(
    ("model", "data"),
    [
        pytest.param(TitledWithContent, {"count": "x"}, id="title"),
        pytest.param(TypedFromValue, {"note": CONTENT_SENTINEL}, id="custom-type"),
    ],
)
def test_strings_the_application_chooses_are_copied_as_they_are(
    model: type[BaseModel], data: dict[str, Any]
) -> None:
    """Documented as not protected (ADR-071 decision 1): a title or error type."""
    assert CONTENT_SENTINEL in describe(_error(model, data))
