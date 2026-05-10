from __future__ import annotations

import pytest

from pydomain.ddd.exceptions import (
    AggregateNotFoundError,
    ConcurrencyError,
    DomainError,
    RepositoryError,
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
            AggregateNotFoundError,
            RepositoryError,
            SpecificationError,
        ]
        for error_cls in errors:
            with pytest.raises(DomainError):
                raise error_cls()

    def test_domain_error_is_not_caught_by_exception_subset(self) -> None:
        with pytest.raises(ConcurrencyError):
            try:
                raise ConcurrencyError()
            except AggregateNotFoundError:
                pytest.fail(
                    "ConcurrencyError should not be caught by AggregateNotFoundError"
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


class TestAggregateNotFoundError:
    def test_is_domain_error(self) -> None:
        assert issubclass(AggregateNotFoundError, DomainError)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(AggregateNotFoundError) as exc_info:
            raise AggregateNotFoundError("Order not found: abc-123")
        assert "Order not found" in str(exc_info.value)

    def test_caught_by_domain_error(self) -> None:
        with pytest.raises(DomainError):
            raise AggregateNotFoundError()


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


class TestRepositoryError:
    def test_is_domain_error(self) -> None:
        assert issubclass(RepositoryError, DomainError)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(RepositoryError) as exc_info:
            raise RepositoryError("storage failure")
        assert "storage failure" in str(exc_info.value)

    def test_caught_by_domain_error(self) -> None:
        with pytest.raises(DomainError):
            raise RepositoryError()


class TestIsinstanceChecks:
    def test_concurrency_error_isinstance_all_levels(self) -> None:
        err = ConcurrencyError()
        assert isinstance(err, ConcurrencyError)
        assert isinstance(err, DomainError)
        assert isinstance(err, Exception)

    def test_aggregate_not_found_isinstance_all_levels(self) -> None:
        err = AggregateNotFoundError()
        assert isinstance(err, AggregateNotFoundError)
        assert isinstance(err, DomainError)
        assert isinstance(err, Exception)

    def test_specification_error_isinstance_all_levels(self) -> None:
        err = SpecificationError()
        assert isinstance(err, SpecificationError)
        assert isinstance(err, DomainError)
        assert isinstance(err, Exception)

    def test_cross_type_isinstance(self) -> None:
        err = ConcurrencyError()
        assert not isinstance(err, AggregateNotFoundError)
        assert not isinstance(err, SpecificationError)
