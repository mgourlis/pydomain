from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pydomain.ddd.specification import (
    AndSpecification,
    NotSpecification,
    OrSpecification,
    Specification,
)

# ---------------------------------------------------------------------------
# Concrete specifications used across tests
# ---------------------------------------------------------------------------


class AlwaysTrue(Specification):
    """A specification that is always satisfied."""

    def is_satisfied_by(self, obj: Any) -> bool:
        return True


class AlwaysFalse(Specification):
    """A specification that is never satisfied."""

    def is_satisfied_by(self, obj: Any) -> bool:
        return False


class GreaterThan(Specification):
    """A specification that is satisfied when *obj* > *threshold*."""

    threshold: int

    def is_satisfied_by(self, obj: Any) -> bool:
        return isinstance(obj, int | float) and obj > self.threshold

    def subsumes(self, other: Specification) -> bool:
        if isinstance(other, GreaterThan):
            return self.threshold <= other.threshold
        return False


class IsPositive(Specification):
    """A specification that is satisfied by positive integers."""

    def is_satisfied_by(self, obj: Any) -> bool:
        return isinstance(obj, int | float) and obj > 0


# ---------------------------------------------------------------------------
# Domain-like object for specification evaluation tests
# ---------------------------------------------------------------------------


class Order:
    """Simplified domain object for testing specifications."""

    def __init__(self, total: float, country: str) -> None:
        self.total = total
        self.country = country


class TotalAbove(Specification):
    """Satisfied when Order.total exceeds a threshold."""

    threshold: float

    def is_satisfied_by(self, obj: Any) -> bool:
        return isinstance(obj, Order) and obj.total > self.threshold


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConcreteSpecifications:
    """Verify that concrete specification classes evaluate correctly."""

    def test_always_true_is_satisfied(self) -> None:
        spec = AlwaysTrue()
        assert spec.is_satisfied_by(None)
        assert spec.is_satisfied_by(42)
        assert spec.is_satisfied_by("anything")

    def test_always_false_is_not_satisfied(self) -> None:
        spec = AlwaysFalse()
        assert not spec.is_satisfied_by(None)
        assert not spec.is_satisfied_by(42)
        assert not spec.is_satisfied_by("anything")

    def test_greater_than_satisfied_when_above_threshold(self) -> None:
        spec = GreaterThan(threshold=10)
        assert spec.is_satisfied_by(15)
        assert spec.is_satisfied_by(10.1)

    def test_greater_than_not_satisfied_when_at_or_below_threshold(self) -> None:
        spec = GreaterThan(threshold=10)
        assert not spec.is_satisfied_by(10)
        assert not spec.is_satisfied_by(5)
        assert not spec.is_satisfied_by(-1)

    def test_greater_than_not_satisfied_by_non_numeric(self) -> None:
        spec = GreaterThan(threshold=10)
        assert not spec.is_satisfied_by("fifteen")
        assert not spec.is_satisfied_by(None)
        assert not spec.is_satisfied_by([20])


class TestAndSpecification:
    """Logical AND composition."""

    def test_both_true_returns_true(self) -> None:
        spec = AndSpecification(specifications=(AlwaysTrue(), AlwaysTrue()))
        assert spec.is_satisfied_by(None)

    def test_one_false_returns_false(self) -> None:
        spec = AndSpecification(specifications=(AlwaysTrue(), AlwaysFalse()))
        assert not spec.is_satisfied_by(None)

    def test_both_false_returns_false(self) -> None:
        spec = AndSpecification(specifications=(AlwaysFalse(), AlwaysFalse()))
        assert not spec.is_satisfied_by(None)

    def test_multiple_specifications(self) -> None:
        spec = AndSpecification(
            specifications=(
                GreaterThan(threshold=5),
                GreaterThan(threshold=10),
                GreaterThan(threshold=15),
            )
        )
        assert spec.is_satisfied_by(20)
        assert not spec.is_satisfied_by(12)
        assert not spec.is_satisfied_by(3)

    def test_short_circuit_on_first_false(self) -> None:
        """Boolean short-circuit: all() stops at first False."""
        call_log: list[int] = []

        class TrackingSpec(Specification):
            index: int
            result: bool

            def is_satisfied_by(self, obj: Any) -> bool:
                call_log.append(self.index)
                return self.result

        spec = AndSpecification(
            specifications=(
                TrackingSpec(index=1, result=True),
                TrackingSpec(index=2, result=False),
                TrackingSpec(index=3, result=True),
            )
        )
        assert not spec.is_satisfied_by(None)
        assert call_log == [1, 2]  # third spec never called


class TestOrSpecification:
    """Logical OR composition."""

    def test_both_false_returns_false(self) -> None:
        spec = OrSpecification(specifications=(AlwaysFalse(), AlwaysFalse()))
        assert not spec.is_satisfied_by(None)

    def test_one_true_returns_true(self) -> None:
        spec = OrSpecification(specifications=(AlwaysFalse(), AlwaysTrue()))
        assert spec.is_satisfied_by(None)

    def test_both_true_returns_true(self) -> None:
        spec = OrSpecification(specifications=(AlwaysTrue(), AlwaysTrue()))
        assert spec.is_satisfied_by(None)

    def test_multiple_specifications(self) -> None:
        spec = OrSpecification(
            specifications=(
                AlwaysFalse(),
                AlwaysFalse(),
                AlwaysTrue(),
                AlwaysFalse(),
            )
        )
        assert spec.is_satisfied_by(None)

    def test_short_circuit_on_first_true(self) -> None:
        """Boolean short-circuit: any() stops at first True."""
        call_log: list[int] = []

        class TrackingSpec(Specification):
            index: int
            result: bool

            def is_satisfied_by(self, obj: Any) -> bool:
                call_log.append(self.index)
                return self.result

        spec = OrSpecification(
            specifications=(
                TrackingSpec(index=1, result=False),
                TrackingSpec(index=2, result=True),
                TrackingSpec(index=3, result=False),
            )
        )
        assert spec.is_satisfied_by(None)
        assert call_log == [1, 2]  # third spec never called


class TestNotSpecification:
    """Logical NOT composition."""

    def test_negates_true_to_false(self) -> None:
        spec = NotSpecification(specification=AlwaysTrue())
        assert not spec.is_satisfied_by(None)

    def test_negates_false_to_true(self) -> None:
        spec = NotSpecification(specification=AlwaysFalse())
        assert spec.is_satisfied_by(None)

    def test_double_negation(self) -> None:
        spec = NotSpecification(
            specification=NotSpecification(specification=AlwaysTrue())
        )
        assert spec.is_satisfied_by(None)


class TestChaining:
    """Chaining via the ``and_()``, ``or_()``, ``not_()`` named methods."""

    def test_and_chaining(self) -> None:
        spec = GreaterThan(threshold=5).and_(GreaterThan(threshold=10))
        assert spec.is_satisfied_by(15)
        assert not spec.is_satisfied_by(7)

    def test_or_chaining(self) -> None:
        spec = GreaterThan(threshold=10).or_(GreaterThan(threshold=20))
        assert spec.is_satisfied_by(15)
        assert spec.is_satisfied_by(25)
        assert not spec.is_satisfied_by(5)

    def test_not_chaining(self) -> None:
        spec = GreaterThan(threshold=10).not_()
        assert spec.is_satisfied_by(5)
        assert not spec.is_satisfied_by(15)

    def test_and_then_or(self) -> None:
        """Chain AND then OR: (x > 5 AND x > 10) OR (x > 0)."""
        spec = (
            GreaterThan(threshold=5)
            .and_(GreaterThan(threshold=10))
            .or_(GreaterThan(threshold=0))
        )
        # > 0 is satisfied for all positive numbers
        assert spec.is_satisfied_by(3)
        # > 5 AND > 10 is also satisfied
        assert spec.is_satisfied_by(15)

    def test_or_then_and(self) -> None:
        """Chain OR then AND: (x > 10 OR x < 0) AND (x > 5)."""
        spec = (
            GreaterThan(threshold=10)
            .or_(GreaterThan(threshold=-1))
            .and_(GreaterThan(threshold=5))
        )
        # 7: > 10? No. > -1? Yes. AND > 5? Yes → True
        assert spec.is_satisfied_by(7)
        # 3: > 10? No. > -1? Yes. AND > 5? No → False
        assert not spec.is_satisfied_by(3)

    def test_not_of_and(self) -> None:
        """NOT(AlwaysTrue AND AlwaysFalse) → NOT(False) → True."""
        spec = AlwaysTrue().and_(AlwaysFalse()).not_()
        assert spec.is_satisfied_by(None)


class TestNestedComposition:
    """Nested composition via method chaining."""

    def test_nested_and_within_or(self) -> None:
        """(x > 5 AND x > 10) → equivalent to x > 10."""
        spec = GreaterThan(threshold=5).and_(GreaterThan(threshold=10))
        assert spec.is_satisfied_by(15)
        assert not spec.is_satisfied_by(7)

    def test_deeply_nested(self) -> None:
        """Construct: (x > 1 AND (x > 5 OR x > 10))."""
        spec = GreaterThan(threshold=1).and_(
            GreaterThan(threshold=5).or_(GreaterThan(threshold=10))
        )
        # 7: > 1 AND (>5 OR >10) → True AND (True OR False) → True
        assert spec.is_satisfied_by(7)
        # 12: > 1 AND (>5 OR >10) → True AND (True OR True) → True
        assert spec.is_satisfied_by(12)
        # 3: > 1 AND (>5 OR >10) → True AND (False OR False) → False
        assert not spec.is_satisfied_by(3)
        # 0: > 1 AND (>5 OR >10) → False AND (...) → False
        assert not spec.is_satisfied_by(0)

    def test_and_within_or_within_not(self) -> None:
        """NOT( AlwaysFalse OR (AlwaysTrue AND AlwaysFalse) )."""
        spec = AlwaysFalse().or_(AlwaysTrue().and_(AlwaysFalse())).not_()
        # Inner OR: False OR (True AND False) = False OR False = False
        # NOT(False) = True
        assert spec.is_satisfied_by(None)


class TestSubsumption:
    """Subsumption: whether one specification is a superset of another."""

    def test_subsumes_default_false_on_base(self) -> None:
        """Base class subsumes() returns False for any argument."""
        spec = AlwaysTrue()
        assert not spec.subsumes(AlwaysTrue())
        assert not spec.subsumes(AlwaysFalse())

    def test_greater_than_subsumes_stricter(self) -> None:
        """GreaterThan(5) subsumes GreaterThan(10): everything >10 is also >5."""
        spec = GreaterThan(threshold=5)
        stricter = GreaterThan(threshold=10)
        assert spec.subsumes(stricter)

    def test_greater_than_not_subsumed_by_stricter(self) -> None:
        """GreaterThan(10) does NOT subsume GreaterThan(5)."""
        spec = GreaterThan(threshold=10)
        looser = GreaterThan(threshold=5)
        assert not spec.subsumes(looser)

    def test_greater_than_equal_thresholds_subsumes(self) -> None:
        """GreaterThan(10) subsumes GreaterThan(10): same threshold."""
        spec = GreaterThan(threshold=10)
        same = GreaterThan(threshold=10)
        assert spec.subsumes(same)

    def test_greater_than_does_not_subsume_different_type(self) -> None:
        """GreaterThan does not subsume a non-GreaterThan spec."""
        spec = GreaterThan(threshold=5)
        assert not spec.subsumes(AlwaysTrue())

    def test_and_specification_subsumes_when_all_subsumes(self) -> None:
        """AndSpecification subsumes when every component subsumes the other."""
        spec = GreaterThan(threshold=5).and_(IsPositive())
        stricter = GreaterThan(threshold=10)
        # IsPositive does not subsume GreaterThan(10)
        assert not spec.subsumes(stricter)

    def test_or_specification_subsumes_when_any_subsumes(self) -> None:
        """OrSpecification subsumes when any component subsumes the other."""
        spec = AlwaysFalse().or_(GreaterThan(threshold=5))
        stricter = GreaterThan(threshold=10)
        # GreaterThan(5) subsumes GreaterThan(10)
        assert spec.subsumes(stricter)

    def test_not_specification_never_subsumes(self) -> None:
        """NotSpecification.subsumes() always returns False."""
        spec = GreaterThan(threshold=5).not_()
        assert not spec.subsumes(GreaterThan(threshold=10))
        assert not spec.subsumes(AlwaysTrue())


class TestWithDomainObjects:
    """Specifications evaluated against domain-like objects."""

    def test_total_above_satisfied(self) -> None:
        order = Order(total=150.0, country="US")
        spec = TotalAbove(threshold=100.0)
        assert spec.is_satisfied_by(order)

    def test_total_above_not_satisfied(self) -> None:
        order = Order(total=50.0, country="US")
        spec = TotalAbove(threshold=100.0)
        assert not spec.is_satisfied_by(order)

    def test_combined_specifications_on_domain_object(self) -> None:
        """A high-value order should satisfy (total > 100)."""
        order = Order(total=250.0, country="DE")
        spec = TotalAbove(threshold=100.0).and_(TotalAbove(threshold=50.0))
        assert spec.is_satisfied_by(order)

    def test_false_for_wrong_object_type(self) -> None:
        """Specifications gracefully handle unexpected object types."""
        spec = TotalAbove(threshold=100.0)
        assert not spec.is_satisfied_by("not an order")
        assert not spec.is_satisfied_by(None)
        assert not spec.is_satisfied_by(42)


class TestSpecificationImmutability:
    """Specifications are value objects — frozen and hashable."""

    def test_specification_is_frozen(self) -> None:
        spec = GreaterThan(threshold=10)
        with pytest.raises(ValidationError):
            spec.threshold = 20  # type: ignore[misc]

    def test_specification_is_hashable(self) -> None:
        spec = GreaterThan(threshold=10)
        # Pydantic frozen models are hashable
        _ = hash(spec)

    def test_specifications_equal_when_fields_equal(self) -> None:
        a = GreaterThan(threshold=10)
        b = GreaterThan(threshold=10)
        assert a == b

    def test_specifications_not_equal_when_fields_differ(self) -> None:
        a = GreaterThan(threshold=10)
        b = GreaterThan(threshold=20)
        assert a != b


class TestAndOrNotDirectConstruction:
    """Direct construction of composite specifications."""

    def test_and_of_single_works(self) -> None:
        spec = AndSpecification(specifications=(AlwaysTrue(),))
        assert spec.is_satisfied_by(None)

    def test_or_of_single_works(self) -> None:
        spec = OrSpecification(specifications=(AlwaysTrue(),))
        assert spec.is_satisfied_by(None)

    def test_and_with_three(self) -> None:
        spec = AndSpecification(
            specifications=(
                GreaterThan(threshold=1),
                GreaterThan(threshold=2),
                GreaterThan(threshold=3),
            )
        )
        assert spec.is_satisfied_by(5)
        assert not spec.is_satisfied_by(2)

    def test_composite_returns_correct_type(self) -> None:
        spec_and: Specification = AlwaysTrue().and_(AlwaysFalse())
        spec_or: Specification = AlwaysTrue().or_(AlwaysFalse())
        spec_not: Specification = AlwaysTrue().not_()
        assert isinstance(spec_and, AndSpecification)
        assert isinstance(spec_or, OrSpecification)
        assert isinstance(spec_not, NotSpecification)

    def test_not_accepts_single_specification(self) -> None:
        spec = NotSpecification(specification=AlwaysTrue())
        assert not spec.is_satisfied_by(None)
