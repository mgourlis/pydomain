"""Specification pattern implementation.

A Specification is a predicate-like value object that determines whether
an object satisfies some criteria. Specifications are composable using
logical AND, OR, and NOT operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class AndSpecification(Specification):
    """Logical AND of one or more specifications.

    Satisfied when **all** contained specifications are satisfied.
    """

    specifications: tuple[Specification, ...] = Field(min_length=1)

    def is_satisfied_by(self, obj: Any) -> bool:
        return all(s.is_satisfied_by(obj) for s in self.specifications)

    def subsumes(self, other: Specification) -> bool:
        return all(s.subsumes(other) for s in self.specifications)


class OrSpecification(Specification):
    """Logical OR of one or more specifications.

    Satisfied when **any** contained specification is satisfied.
    """

    specifications: tuple[Specification, ...] = Field(min_length=1)

    def is_satisfied_by(self, obj: Any) -> bool:
        return any(s.is_satisfied_by(obj) for s in self.specifications)

    def subsumes(self, other: Specification) -> bool:
        return any(s.subsumes(other) for s in self.specifications)


class NotSpecification(Specification):
    """Logical NOT of a specification.

    Satisfied when the contained specification is **not** satisfied.
    """

    specification: Specification

    def is_satisfied_by(self, obj: Any) -> bool:
        return not self.specification.is_satisfied_by(obj)

    def subsumes(self, other: Specification) -> bool:
        return False
