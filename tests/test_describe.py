"""Tests for ``pydomain._describe``: value-free descriptions (ADR-071) and
stacks (ADR-081 point 4)."""

from __future__ import annotations

import enum
import importlib.util
import re
import traceback
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

from pydomain import _describe
from pydomain._describe import describe
from pydomain.cqrs.event_bus import EventBus
from pydomain.cqrs.exceptions import EventHandlersFailed
from pydomain.ddd.domain_event import DomainEvent
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


# ── describe_stack: a value-free stack for failure logs (ADR-081 point 4) ──


def _stack(exc: BaseException) -> str:
    return _describe.describe_stack(exc)


def _raise_in_inner() -> None:
    raise RuntimeError(f"inner {CONTENT_SENTINEL}")


def _caught(fail: Any) -> BaseException:
    try:
        fail()
    except BaseException as exc:  # noqa: BLE001 - the test inspects any failure
        return exc
    raise AssertionError("expected a failure")


def test_describe_stack_names_types_and_frames_only() -> None:
    error = _caught(_raise_in_inner)

    lines = _stack(error).splitlines()

    assert lines[0] == "RuntimeError"
    assert lines[1:] == [
        f'  File "{_caught.__code__.co_filename}", line {line}, in {function}'
        for line, function in (
            (_caught.__code__.co_firstlineno + 2, "_caught"),
            (_raise_in_inner.__code__.co_firstlineno + 1, "_raise_in_inner"),
        )
    ]
    assert CONTENT_SENTINEL not in "\n".join(lines)


def test_describe_stack_follows_cause_context_and_group_members_once() -> None:
    shared = KeyError(CONTENT_SENTINEL)
    first = ValueError(CONTENT_SENTINEL)
    first.__cause__ = shared
    second = TypeError(CONTENT_SENTINEL)
    second.__cause__ = shared
    group = ExceptionGroup(CONTENT_SENTINEL, [first, second])
    group.__context__ = OSError(CONTENT_SENTINEL)

    assert _stack(group).splitlines() == [
        "ExceptionGroup",
        "  Member 1 of 2: ValueError",
        "  Caused by: KeyError",
        "  Member 2 of 2: TypeError",
        "During handling of: OSError",
    ]


def test_describe_stack_respects_suppressed_context() -> None:
    def fail() -> None:
        try:
            raise KeyError(CONTENT_SENTINEL)
        except KeyError:
            raise ValueError(CONTENT_SENTINEL) from None

    stack = _stack(_caught(fail))

    assert stack.splitlines()[0] == "ValueError"
    assert "KeyError" not in stack


def test_describe_stack_drops_messages_and_foreign_notes() -> None:
    error = ValueError(f"message {CONTENT_SENTINEL}")
    error.add_note(f"note {CONTENT_SENTINEL}")
    error.add_note("a value-free note of the application")
    error.__cause__ = KeyError(CONTENT_SENTINEL)

    assert _stack(error).splitlines() == ["ValueError", "Caused by: KeyError"]


def test_describe_stack_keeps_pydomain_clean_up_notes() -> None:
    """B-196: only an exact full match of pydomain's own patterns is kept."""
    error = ValueError(CONTENT_SENTINEL)
    for note in (
        "clean-up also failed: RuntimeError",
        f"clean-up also failed: {CONTENT_SENTINEL}",
        "clean-up also failed: RuntimeError and more text",
        "clean-up also failed: RuntimeError\n",
        "the clean-up also failed: RuntimeError",
        "draining the event buffers also failed: OSError",
        "draining the event buffers also failed: ",
    ):
        error.add_note(note)
    error.__notes__.append(42)  # not a string: dropped, no failure

    assert _stack(error).splitlines() == [
        "ValueError",
        "  Note: clean-up also failed: RuntimeError",
        "  Note: draining the event buffers also failed: OSError",
    ]


def test_describe_stack_ignores_notes_that_are_not_a_list() -> None:
    error = ValueError(CONTENT_SENTINEL)
    error.__notes__ = "clean-up also failed: RuntimeError"  # type: ignore[assignment]

    assert _stack(error) == "ValueError"


def test_describe_stack_caps_the_number_of_exceptions() -> None:
    chain = [ValueError(CONTENT_SENTINEL) for _ in range(101)]
    for error, cause in zip(chain, chain[1:], strict=False):
        error.__cause__ = cause

    lines = _stack(chain[0]).splitlines()

    assert lines == ["ValueError"] + ["Caused by: ValueError"] * 99 + [
        "… 1 more exception(s)"
    ]


def test_describe_stack_counts_every_exception_left_after_the_cap() -> None:
    chain = [ValueError() for _ in range(100)]
    for error, cause in zip(chain, chain[1:], strict=False):
        error.__cause__ = cause
    chain[-1].__cause__ = KeyError()
    chain[-1].__context__ = OSError()
    chain[-1].__suppress_context__ = False

    assert _stack(chain[0]).splitlines()[-1] == "… 2 more exception(s)"


def test_describe_stack_caps_the_width_of_a_group() -> None:
    group = ExceptionGroup("g", [ValueError(str(i)) for i in range(16)])

    lines = _stack(group).splitlines()

    assert lines[1] == "  Member 1 of 16: ValueError"
    assert lines[15] == "  Member 15 of 16: ValueError"
    assert lines[16:] == ["  … 1 more member(s)"]


def test_describe_stack_keeps_a_group_of_fifteen_whole_and_goes_on_after_a_marker() -> (
    None
):
    whole = ExceptionGroup("g", [ValueError(str(i)) for i in range(15)])
    wide = ExceptionGroup("g", [ValueError(str(i)) for i in range(16)])
    wide.__cause__ = KeyError()

    assert _stack(whole).splitlines()[-1] == "  Member 15 of 15: ValueError"
    assert _stack(wide).splitlines()[-2:] == [
        "  … 1 more member(s)",
        "Caused by: KeyError",
    ]


def test_describe_stack_counts_neither_markers_nor_shown_exceptions_after_the_cap() -> (
    None
):
    """At the cap, "… N more" counts the exceptions still to render only."""
    chain = [ValueError() for _ in range(98)]
    for error, cause in zip(chain, chain[1:], strict=False):
        error.__cause__ = cause
    members = [chain[0], *(TypeError() for _ in range(15))]
    wide = ExceptionGroup("g", members)
    shown_twice = KeyError()
    big = [OSError() for _ in range(99)]
    for error, cause in zip(big, big[1:], strict=False):
        error.__cause__ = cause
    repeated = ExceptionGroup("g", [shown_twice, big[0], shown_twice])

    # wide: the group, member 1 with its chain (98) and member 2 make 100;
    # members 3 to 15 count, the "… 1 more member(s)" line does not.
    assert _stack(wide).splitlines()[-1] == "  … 13 more exception(s)"
    # repeated: the third member was shown as the first, so it does not count.
    assert _stack(repeated).splitlines()[-1] == "  … 1 more exception(s)"


def _recurse_then_fail(depth: int) -> None:
    if depth == 0:
        raise ValueError(CONTENT_SENTINEL)
    _recurse_then_fail(depth - 1)


def test_describe_stack_collapses_repeated_frames() -> None:
    lines = _stack(_caught(lambda: _recurse_then_fail(5))).splitlines()
    first = _recurse_then_fail.__code__.co_firstlineno
    path = _recurse_then_fail.__code__.co_filename

    assert lines[3:] == [
        f'  File "{path}", line {first + 3}, in _recurse_then_fail',
        "  … 4 more of the frame above",
        f'  File "{path}", line {first + 2}, in _recurse_then_fail',
    ]


def test_describe_stack_reads_no_source_lines(tmp_path: Any) -> None:
    """A frame's source line holds the sentinel; the stack never prints it."""
    source = tmp_path / "synthetic_source_module.py"
    source.write_text(
        f"def explode():\n    raise ValueError()  # {CONTENT_SENTINEL}\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("synthetic_source_module", source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    stack = _stack(_caught(module.explode))

    assert f'File "{source}", line 2, in explode' in stack
    assert CONTENT_SENTINEL not in stack


def test_describe_stack_terminates_on_cycles() -> None:
    first = ValueError(CONTENT_SENTINEL)
    second = KeyError(CONTENT_SENTINEL)
    first.__context__ = second
    second.__context__ = first

    assert _stack(first).splitlines() == [
        "ValueError",
        "During handling of: KeyError",
    ]


def test_describe_stack_caps_a_recursion_error() -> None:
    def recurse(depth: int) -> int:
        return recurse(depth + 1) + 1

    error = _caught(lambda: recurse(0))
    assert isinstance(error, RecursionError)

    lines = _stack(error).splitlines()

    assert len(lines) <= 6
    assert lines[0] == "RecursionError"
    assert lines[-2].endswith(", in recurse")
    assert re.fullmatch(r"  … \d+ more of the frame above", lines[-1])


def _ping(depth: int) -> None:
    if depth == 0:
        raise ValueError(CONTENT_SENTINEL)
    _pong(depth - 1)


def _pong(depth: int) -> None:
    _ping(depth)


def _ping_forever(depth: int) -> None:
    _pong_forever(depth + 1)


def _pong_forever(depth: int) -> None:
    _ping_forever(depth + 1)


def _frame_lines(error: BaseException) -> list[str]:
    """Every frame of *error*, uncapped, as describe_stack renders one."""
    return [
        f'  File "{frame.f_code.co_filename}", line {line}, in {frame.f_code.co_name}'
        for frame, line in traceback.walk_tb(error.__traceback__)
    ]


def test_describe_stack_keeps_fifty_frames_whole() -> None:
    """B-212: a stack of 50 frames, none repeated, is rendered whole."""
    error = _caught(lambda: _pong(23))  # _caught, <lambda> and 48 ping-pongs
    frames = _frame_lines(error)
    assert len(frames) == 50

    assert _stack(error).splitlines() == ["ValueError", *frames]


def test_describe_stack_keeps_the_first_and_last_25_frames() -> None:
    """B-212: past 50 frames, the first 25 and the last 25 are kept."""
    error = _caught(lambda: _ping(24))
    frames = _frame_lines(error)
    assert len(frames) == 51

    assert _stack(error).splitlines() == [
        "ValueError",
        *frames[:25],
        "  … 1 more frame(s)",
        *frames[-25:],
    ]


def test_describe_stack_caps_a_mutual_recursion_error() -> None:
    """B-212: an a <-> b RecursionError renders at most 52 lines (the type,
    25 frames, the marker, 25 frames), not its ~1000 frames."""
    error = _caught(lambda: _ping_forever(0))
    assert isinstance(error, RecursionError)

    lines = _stack(error).splitlines()

    assert len(lines) <= 52
    assert re.fullmatch(r"  … \d+ more frame\(s\)", lines[26])


def test_describe_stack_bounds_a_hundred_deep_exceptions() -> None:
    """B-212: the worst case, 100 chained exceptions each on a ~1000-frame
    mutual recursion, stays under 5,300 lines (100 x 52, the cause labels
    included, plus the cap marker) and 1 MB."""
    deep = _caught(lambda: _ping_forever(0)).__traceback__
    chain = [RuntimeError().with_traceback(deep) for _ in range(101)]
    for error, cause in zip(chain, chain[1:], strict=False):
        error.__cause__ = cause

    stack = _stack(chain[0])

    assert len(stack.splitlines()) <= 100 * 52 + 1
    assert len(stack.encode()) < 1_000_000


def test_describe_stack_leaves_out_every_kind_of_content() -> None:
    """(guard) The sentinel in a message, in args (a dict too), in a plain and
    a prefix-spoofing note, in a chained cause, in a KeyError's key and in
    the messages of a group and its nested members; ``raise ... from``
    suppresses the KeyError context."""

    def fail() -> None:
        try:
            {}[CONTENT_SENTINEL]
        except KeyError:
            cause = ExceptionGroup(
                f"group {CONTENT_SENTINEL}",
                [
                    ValueError(CONTENT_SENTINEL),
                    ExceptionGroup(CONTENT_SENTINEL, [TypeError(CONTENT_SENTINEL)]),
                ],
            )
            error = RuntimeError(
                f"message {CONTENT_SENTINEL}", {"payload": CONTENT_SENTINEL}
            )
            error.add_note(f"note {CONTENT_SENTINEL}")
            error.add_note(f"clean-up also failed: {CONTENT_SENTINEL}")
            raise error from cause

    stack = _stack(_caught(fail))
    headers = [line for line in stack.splitlines() if "File " not in line]

    assert headers == [
        "RuntimeError",
        "Caused by: ExceptionGroup",
        "  Member 1 of 2: ValueError",
        "  Member 2 of 2: ExceptionGroup",
        "    Member 1 of 1: TypeError",
    ]
    assert CONTENT_SENTINEL not in stack


def test_describe_stack_caps_a_deep_nested_group() -> None:
    error: BaseException = ValueError(CONTENT_SENTINEL)
    for _ in range(2000):
        error = ExceptionGroup(CONTENT_SENTINEL, [error])

    lines = _stack(error).splitlines()

    assert lines[0] == "ExceptionGroup"
    assert lines[1:10] == [
        "  " * level + "Member 1 of 1: ExceptionGroup" for level in range(1, 10)
    ]
    assert lines[10:] == ["  " * 10 + "… 1 more member(s)"]
    assert CONTENT_SENTINEL not in "\n".join(lines)


class _Inner(DomainEvent):
    pass


class _Outer(DomainEvent):
    pass


@pytest.mark.anyio
async def test_describe_stack_renders_both_levels_of_a_nested_handler_group() -> None:
    """A handler that dispatches directly to a failing handler (ADR-081 point 2)."""
    bus = EventBus()

    async def inner_handler(event: _Inner) -> None:
        raise ValueError(CONTENT_SENTINEL)

    async def outer_handler(event: _Outer) -> None:
        await bus.dispatch(_Inner())

    bus.register(_Inner, inner_handler)
    bus.register(_Outer, outer_handler)
    with pytest.raises(EventHandlersFailed) as raised:
        await bus.dispatch(_Outer())

    stack = _stack(raised.value)
    headers = [line for line in stack.splitlines() if "File " not in line]

    assert headers == [
        "EventHandlersFailed",
        "  Member 1 of 1: EventHandlersFailed",
        "    Member 1 of 1: ValueError",
    ]
    assert "in inner_handler" in stack
    assert "in outer_handler" in stack
    assert "handler(s) failed" not in stack
    assert CONTENT_SENTINEL not in stack
