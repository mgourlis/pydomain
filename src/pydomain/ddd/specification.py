"""Specification pattern implementation.

A Specification is a predicate-like value object that determines whether
an object satisfies some criteria. Specifications are composable using
logical AND, OR, and NOT operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny


class Specification(BaseModel, ABC):
    """Base class for specifications.

    A Specification is a specialized Value Object that encapsulates a
    business rule. It determines whether a candidate object satisfies
    some criteria.

    Specifications are:

    * **Immutable** (frozen) — once created, their state never changes.
    * **Composable** via ``and_()``, ``or_()``, ``not_()``.
    * **Subsumption-aware** — a specification can check whether it
      subsumes (is a superset of) another specification.
    """

    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def is_satisfied_by(self, obj: Any) -> bool:
        """Check whether the given object satisfies this specification.

        Args:
            obj: The candidate object to evaluate.

        Returns:
            ``True`` if the object satisfies the specification,
            ``False`` otherwise.
        """
        ...

    def and_(self, other: Specification) -> AndSpecification:
        """Return the logical AND of this specification and *other*.

        The returned ``AndSpecification`` is satisfied when **both**
        this specification **and** *other* are satisfied.

        Args:
            other: The specification to AND with.

        Returns:
            A new ``AndSpecification`` instance.
        """
        return AndSpecification(specifications=(self, other))

    def or_(self, other: Specification) -> OrSpecification:
        """Return the logical OR of this specification and *other*.

        The returned ``OrSpecification`` is satisfied when **either**
        this specification **or** *other* (or both) is satisfied.

        Args:
            other: The specification to OR with.

        Returns:
            A new ``OrSpecification`` instance.
        """
        return OrSpecification(specifications=(self, other))

    def not_(self) -> NotSpecification:
        """Return the logical negation of this specification.

        The returned ``NotSpecification`` is satisfied when this
        specification is **not** satisfied.

        Returns:
            A new ``NotSpecification`` instance.
        """
        return NotSpecification(specification=self)

    def subsumes(self, other: Specification) -> bool:
        """Check whether this specification subsumes another.

        A specification *subsumes* another if every object that satisfies
        the other also satisfies this specification (i.e. this spec is
        a superset of the other).

        The default implementation returns ``False``. Subclasses may
        override with domain-specific subsumption logic.

        Args:
            other: The specification to check against.

        Returns:
            ``True`` if this specification subsumes *other*,
            ``False`` otherwise.
        """
        return False


type _Operator = Literal["all", "any", "not"]
type _Branch = tuple[_Operator, tuple[Specification, ...]] | None


def _satisfaction_branch(spec: Specification) -> _Branch:
    """How *spec* combines its children in ``is_satisfied_by``, if it does.

    A composite subclass that overrides ``is_satisfied_by`` is a leaf here,
    so its override is called as written.
    """
    method = type(spec).is_satisfied_by
    if (
        isinstance(spec, AndSpecification)
        and method is AndSpecification.is_satisfied_by
    ):
        return "all", spec.specifications
    if isinstance(spec, OrSpecification) and method is OrSpecification.is_satisfied_by:
        return "any", spec.specifications
    if (
        isinstance(spec, NotSpecification)
        and method is NotSpecification.is_satisfied_by
    ):
        return "not", (spec.specification,)
    return None


def _subsumption_branch(spec: Specification) -> _Branch:
    """How *spec* combines its children in ``subsumes``, if it does."""
    method = type(spec).subsumes
    if isinstance(spec, AndSpecification) and method is AndSpecification.subsumes:
        return "all", spec.specifications
    if isinstance(spec, OrSpecification) and method is OrSpecification.subsumes:
        return "any", spec.specifications
    return None


def _evaluate(
    root: Specification,
    branch: Callable[[Specification], _Branch],
    leaf: Callable[[Specification], bool],
) -> bool:
    """Evaluate a composite tree with an explicit stack, not recursion.

    ``all`` and ``any`` short-circuit left to right, as the built-ins do,
    so the depth of a composite is bounded by memory rather than by the
    interpreter's recursion limit (ADR-070).
    """
    stack: list[tuple[_Operator, Iterator[Specification]]] = []
    node = root
    while True:
        while (found := branch(node)) is not None:
            operator, children = found
            rest = iter(children)
            stack.append((operator, rest))
            node = next(rest)
        value = bool(leaf(node))
        while stack:
            operator, rest = stack[-1]
            if operator == "not":
                value = not value
            elif value is (operator == "all"):
                following = next(rest, None)
                if following is not None:
                    node = following
                    break
            stack.pop()
        else:
            return value


class AndSpecification(Specification):
    """Logical AND of one or more specifications.

    Satisfied when **all** contained specifications are satisfied.
    Composites are evaluated iteratively, so chains of any depth work.
    ``model_dump()`` includes each child's own fields; there is no round
    trip back to the leaf types (ADR-070).
    """

    specifications: tuple[SerializeAsAny[Specification], ...] = Field(min_length=1)

    def is_satisfied_by(self, obj: Any) -> bool:
        return _evaluate(self, _satisfaction_branch, lambda s: s.is_satisfied_by(obj))

    def subsumes(self, other: Specification) -> bool:
        return _evaluate(self, _subsumption_branch, lambda s: s.subsumes(other))


class OrSpecification(Specification):
    """Logical OR of one or more specifications.

    Satisfied when **any** contained specification is satisfied.
    Composites are evaluated iteratively, so chains of any depth work.
    ``model_dump()`` includes each child's own fields; there is no round
    trip back to the leaf types (ADR-070).
    """

    specifications: tuple[SerializeAsAny[Specification], ...] = Field(min_length=1)

    def is_satisfied_by(self, obj: Any) -> bool:
        return _evaluate(self, _satisfaction_branch, lambda s: s.is_satisfied_by(obj))

    def subsumes(self, other: Specification) -> bool:
        return _evaluate(self, _subsumption_branch, lambda s: s.subsumes(other))


class NotSpecification(Specification):
    """Logical NOT of a specification.

    Satisfied when the contained specification is **not** satisfied.
    """

    specification: SerializeAsAny[Specification]

    def is_satisfied_by(self, obj: Any) -> bool:
        return _evaluate(self, _satisfaction_branch, lambda s: s.is_satisfied_by(obj))

    def subsumes(self, other: Specification) -> bool:
        return False
