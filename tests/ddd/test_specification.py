from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
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

    def test_or_specification_subsumes_none_subsumes(self) -> None:
        """OrSpecification: no component subsumes the target."""
        spec = AlwaysFalse().or_(GreaterThan(threshold=20))
        stricter = GreaterThan(threshold=10)
        # GreaterThan(20) does not subsume GreaterThan(10): 20 <= 10 is False
        assert not spec.subsumes(stricter)

    def test_subsumption_transitivity(self) -> None:
        """If A subsumes B and B subsumes C, then A subsumes C."""
        a = GreaterThan(threshold=5)
        b = GreaterThan(threshold=10)
        c = GreaterThan(threshold=15)
        assert a.subsumes(b)
        assert b.subsumes(c)
        assert a.subsumes(c)


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

    def test_and_empty_raises_validation_error(self) -> None:
        """Constructing an AndSpecification with no specs raises ValidationError."""
        with pytest.raises(ValidationError):
            AndSpecification(specifications=())

    def test_or_empty_raises_validation_error(self) -> None:
        """Constructing an OrSpecification with no specs raises ValidationError."""
        with pytest.raises(ValidationError):
            OrSpecification(specifications=())

    def test_not_specification_invalid_type_raises_validation_error(self) -> None:
        """Passing a non-Specification to NotSpecification raises ValidationError."""
        with pytest.raises(ValidationError):
            NotSpecification(specification="nonsense")  # type: ignore[arg-type]


class TestBooleanAlgebra:
    """Boolean algebra invariants for composite specifications.

    Verifies that the AND/OR/NOT composite specifications obey the
    standard laws of Boolean algebra.
    """

    # ------------------------------------------------------------------
    # Identity:  A AND True == A     and     A OR False == A
    # ------------------------------------------------------------------

    def test_identity_and(self) -> None:
        """A AND AlwaysTrue is equivalent to A (identity for AND)."""
        a = GreaterThan(threshold=10)
        composed = a.and_(AlwaysTrue())
        for value in (-5, 0, 5, 10, 15, 25):
            assert composed.is_satisfied_by(value) == a.is_satisfied_by(value)

    def test_identity_or(self) -> None:
        """A OR AlwaysFalse is equivalent to A (identity for OR)."""
        a = GreaterThan(threshold=10)
        composed = a.or_(AlwaysFalse())
        for value in (-5, 0, 5, 10, 15, 25):
            assert composed.is_satisfied_by(value) == a.is_satisfied_by(value)

    # ------------------------------------------------------------------
    # Annihilation:  A AND False == False    and     A OR True == True
    # ------------------------------------------------------------------

    def test_annihilation_and(self) -> None:
        """A AND AlwaysFalse is AlwaysFalse (annihilation for AND)."""
        a = GreaterThan(threshold=10)
        composed = a.and_(AlwaysFalse())
        for value in (-5, 0, 5, 10, 15, 25):
            assert not composed.is_satisfied_by(value)

    def test_annihilation_or(self) -> None:
        """A OR AlwaysTrue is AlwaysTrue (annihilation for OR)."""
        a = GreaterThan(threshold=10)
        composed = a.or_(AlwaysTrue())
        for value in (-5, 0, 5, 10, 15, 25):
            assert composed.is_satisfied_by(value)

    # ------------------------------------------------------------------
    # Complement:  A AND NOT A == False    and    A OR NOT A == True
    # ------------------------------------------------------------------

    def test_complement_and(self) -> None:
        """A AND NOT(A) is AlwaysFalse (law of non-contradiction)."""
        a = GreaterThan(threshold=10)
        composed = a.and_(a.not_())
        for value in (-5, 0, 5, 10, 15, 25):
            assert not composed.is_satisfied_by(value)

    def test_complement_or(self) -> None:
        """A OR NOT(A) is AlwaysTrue (law of excluded middle)."""
        a = GreaterThan(threshold=10)
        composed = a.or_(a.not_())
        for value in (-5, 0, 5, 10, 15, 25):
            assert composed.is_satisfied_by(value)

    # ------------------------------------------------------------------
    # De Morgan's laws
    # ------------------------------------------------------------------

    def test_de_morgan_and(self) -> None:
        """NOT(A AND B) is equivalent to NOT(A) OR NOT(B)."""
        a = GreaterThan(threshold=5)
        b = IsPositive()
        lhs = a.and_(b).not_()
        rhs = a.not_().or_(b.not_())
        for value in (-5, 0, 3, 7, 15):
            assert lhs.is_satisfied_by(value) == rhs.is_satisfied_by(value)

    def test_de_morgan_or(self) -> None:
        """NOT(A OR B) is equivalent to NOT(A) AND NOT(B)."""
        a = GreaterThan(threshold=5)
        b = IsPositive()
        lhs = a.or_(b).not_()
        rhs = a.not_().and_(b.not_())
        for value in (-5, 0, 3, 7, 15):
            assert lhs.is_satisfied_by(value) == rhs.is_satisfied_by(value)


# ---------------------------------------------------------------------------
# Laws, depth and overrides (DCE-125, DDD-15, ADR-070)
# ---------------------------------------------------------------------------


class IsEven(Specification):
    """Satisfied by even integers."""

    def is_satisfied_by(self, obj: Any) -> bool:
        return obj % 2 == 0


_leaves: st.SearchStrategy[Specification] = st.one_of(
    st.builds(GreaterThan, threshold=st.integers(-10, 10)),
    st.just(IsPositive()),
    st.just(IsEven()),
    st.just(AlwaysTrue()),
    st.just(AlwaysFalse()),
)


def _composites(
    children: st.SearchStrategy[Specification],
) -> st.SearchStrategy[Specification]:
    parts = st.lists(children, min_size=1, max_size=3).map(tuple)
    return st.one_of(
        st.tuples(children, children).map(lambda p: p[0].and_(p[1])),
        st.tuples(children, children).map(lambda p: p[0].or_(p[1])),
        children.map(lambda c: c.not_()),
        parts.map(lambda cs: AndSpecification(specifications=cs)),
        parts.map(lambda cs: OrSpecification(specifications=cs)),
    )


specifications = st.recursive(_leaves, _composites, max_leaves=12)
candidates = st.integers(-20, 20)
DOMAIN = range(-20, 21)


def reference_satisfied(spec: Specification, obj: Any) -> bool:
    """Evaluate *spec* by its structure: the meaning of and, or and not."""
    if isinstance(spec, AndSpecification):
        return all(reference_satisfied(s, obj) for s in spec.specifications)
    if isinstance(spec, OrSpecification):
        return any(reference_satisfied(s, obj) for s in spec.specifications)
    if isinstance(spec, NotSpecification):
        return not reference_satisfied(spec.specification, obj)
    return spec.is_satisfied_by(obj)


class TestSpecificationLaws:
    """Property tests: composites follow Boolean algebra over random trees."""

    @given(spec=specifications, obj=candidates)
    def test_composites_evaluate_by_their_structure(
        self, spec: Specification, obj: int
    ) -> None:
        assert spec.is_satisfied_by(obj) is reference_satisfied(spec, obj)

    @given(a=specifications, b=specifications, obj=candidates)
    def test_and_or_not_follow_boolean_algebra(
        self, a: Specification, b: Specification, obj: int
    ) -> None:
        sat_a, sat_b = a.is_satisfied_by(obj), b.is_satisfied_by(obj)
        assert a.and_(b).is_satisfied_by(obj) is (sat_a and sat_b)
        assert a.or_(b).is_satisfied_by(obj) is (sat_a or sat_b)
        assert a.not_().is_satisfied_by(obj) is (not sat_a)
        assert a.not_().not_().is_satisfied_by(obj) is sat_a
        assert a.and_(b).not_().is_satisfied_by(obj) is a.not_().or_(
            b.not_()
        ).is_satisfied_by(obj)
        assert a.or_(b).not_().is_satisfied_by(obj) is a.not_().and_(
            b.not_()
        ).is_satisfied_by(obj)

    @given(a=specifications, b=specifications)
    def test_subsumption_is_sound(self, a: Specification, b: Specification) -> None:
        """If a subsumes b, every candidate that satisfies b satisfies a."""
        if a.subsumes(b):
            assert all(a.is_satisfied_by(x) for x in DOMAIN if b.is_satisfied_by(x))


DEPTH = 5_000

_RECURSION = pytest.mark.xfail(
    strict=True,
    raises=RecursionError,
    reason="DCE-125 DDD-15: composites evaluate their children recursively",
)


def _chain(step: Any, start: Specification) -> Specification:
    spec = start
    for i in range(DEPTH):
        spec = step(spec, i)
    return spec


class TestDeepComposites:
    """Composite depth is bounded by memory, not the recursion limit."""

    @_RECURSION
    def test_deep_and_chain_is_evaluated(self) -> None:
        """Given 5,000 chained and_() calls, when evaluated, then no
        RecursionError is raised and the result is correct."""
        spec = _chain(lambda s, _: s.and_(GreaterThan(threshold=0)), IsPositive())
        assert spec.is_satisfied_by(3) is True
        assert spec.is_satisfied_by(-3) is False

    @_RECURSION
    def test_deep_or_chain_is_evaluated(self) -> None:
        spec = _chain(lambda s, _: s.or_(AlwaysFalse()), IsEven())
        assert spec.is_satisfied_by(4) is True
        assert spec.is_satisfied_by(3) is False

    @_RECURSION
    def test_deep_not_chain_is_evaluated(self) -> None:
        spec = _chain(lambda s, _: s.not_(), IsEven())  # an even number of nots
        assert spec.is_satisfied_by(4) is True
        assert spec.is_satisfied_by(3) is False

    @_RECURSION
    def test_deep_mixed_chain_is_evaluated(self) -> None:
        def step(spec: Specification, i: int) -> Specification:
            if i % 3 == 0:
                return spec.and_(AlwaysTrue())
            if i % 3 == 1:
                return spec.or_(AlwaysFalse())
            return spec.not_().not_()

        spec = _chain(step, IsEven())
        assert spec.is_satisfied_by(4) is True
        assert spec.is_satisfied_by(3) is False

    @_RECURSION
    def test_deep_and_chain_subsumes(self) -> None:
        spec = _chain(
            lambda s, _: s.and_(GreaterThan(threshold=0)), GreaterThan(threshold=0)
        )
        assert spec.subsumes(GreaterThan(threshold=5)) is True
        assert spec.subsumes(IsPositive()) is False

    @_RECURSION
    def test_deep_or_chain_subsumes(self) -> None:
        spec = _chain(lambda s, _: s.or_(AlwaysFalse()), GreaterThan(threshold=0))
        assert spec.subsumes(GreaterThan(threshold=5)) is True


class TestCompositeOverrides:
    """A composite subclass that overrides a method is called as written."""

    def test_overridden_is_satisfied_by_on_a_nested_composite_is_called(self) -> None:
        class Vetoed(AndSpecification):
            def is_satisfied_by(self, obj: Any) -> bool:
                return False

        inner = Vetoed(specifications=(AlwaysTrue(),))
        assert AlwaysTrue().and_(inner).is_satisfied_by(1) is False
        assert inner.or_(AlwaysFalse()).is_satisfied_by(1) is False
        assert inner.not_().is_satisfied_by(1) is True

    def test_overridden_subsumes_on_a_nested_composite_is_called(self) -> None:
        class Covers(OrSpecification):
            def subsumes(self, other: Specification) -> bool:
                return True

        inner = Covers(specifications=(AlwaysFalse(),))
        assert inner.and_(inner).subsumes(IsEven()) is True

    def test_nested_short_circuit_skips_later_children(self) -> None:
        calls: list[str] = []

        class Spy(Specification):
            name: str
            result: bool

            def is_satisfied_by(self, obj: Any) -> bool:
                calls.append(self.name)
                return self.result

        spec = (
            Spy(name="a", result=False)
            .and_(Spy(name="b", result=True))
            .or_(Spy(name="c", result=True).or_(Spy(name="d", result=True)))
        )
        assert spec.is_satisfied_by(None) is True
        assert calls == ["a", "c"]
