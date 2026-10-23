from __future__ import annotations

from typing import Any

import pytest

from pydomain.ddd import exceptions as exceptions_module
from pydomain.ddd.exceptions import (
    ConcurrencyError,
    DomainError,
    SpecificationError,
)


class TestDomainError:
    def test_domain_error_is_exception(self) -> None:
        with pytest.raises(DomainError):
            raise DomainError("something went wrong")

    def test_domain_error_message(self) -> None:
        try:
            raise DomainError("invariant violated")
        except DomainError as e:
            assert str(e) == "invariant violated"

    def test_domain_error_catches_all_subtypes(self) -> None:
        errors: list[type[DomainError]] = [
            ConcurrencyError,
            SpecificationError,
        ]
        for error_cls in errors:
            with pytest.raises(DomainError):
                raise error_cls()

    def test_domain_error_is_not_caught_by_exception_subset(self) -> None:
        with pytest.raises(ConcurrencyError):
            try:
                raise ConcurrencyError()
            except SpecificationError:
                pytest.fail(
                    "ConcurrencyError should not be caught by SpecificationError"
                )


class TestConcurrencyError:
    def test_is_domain_error(self) -> None:
        assert issubclass(ConcurrencyError, DomainError)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(ConcurrencyError) as exc_info:
            raise ConcurrencyError("version mismatch: expected 3, got 2")
        assert "version mismatch" in str(exc_info.value)

    def test_caught_by_domain_error(self) -> None:
        with pytest.raises(DomainError):
            raise ConcurrencyError()


class TestSpecificationError:
    def test_is_domain_error(self) -> None:
        assert issubclass(SpecificationError, DomainError)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(SpecificationError) as exc_info:
            raise SpecificationError("Order must have at least one line item")
        assert "line item" in str(exc_info.value)

    def test_caught_by_domain_error(self) -> None:
        with pytest.raises(DomainError):
            raise SpecificationError()


class TestIsinstanceChecks:
    def test_concurrency_error_isinstance_all_levels(self) -> None:
        err = ConcurrencyError()
        assert isinstance(err, ConcurrencyError)
        assert isinstance(err, DomainError)
        assert isinstance(err, Exception)

    def test_specification_error_isinstance_all_levels(self) -> None:
        err = SpecificationError()
        assert isinstance(err, SpecificationError)
        assert isinstance(err, DomainError)
        assert isinstance(err, Exception)

    def test_cross_type_isinstance(self) -> None:
        err = ConcurrencyError()
        assert not isinstance(err, SpecificationError)


# DCE-152 (review 9 of PR #134): the pickle restorer refuses a class that is
# not an exception before any of that class's code runs.

_calls: list[str] = []


class _Recorder:
    """Not an exception; records every ``__new__`` and ``__setattr__``."""

    def __new__(cls, *args: Any, **kwargs: Any) -> _Recorder:
        _calls.append("__new__")
        return super().__new__(cls)

    def __setattr__(self, name: str, value: Any) -> None:
        _calls.append("__setattr__")
        super().__setattr__(name, value)


class TestRestoreException:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="DCE-152 review 9: _restore_exception runs any class's __new__",
    )
    @pytest.mark.parametrize("cls", [object, _Recorder], ids=["object", "recorder"])
    def test_restorer_refuses_a_class_that_is_not_an_exception(
        self, cls: type[Any]
    ) -> None:
        _calls.clear()
        restore = exceptions_module._restore_exception  # pyright: ignore[reportPrivateUsage]

        error: Exception | None = None
        try:
            restore(cls, ("synthetic",), {"note": "synthetic"})
        except Exception as exc:  # what it raises is the assertion
            error = exc

        assert isinstance(error, TypeError)
        assert str(error) == "Only an exception class can be restored"
        assert _calls == []
