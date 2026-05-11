"""Factory protocols for domain object creation and reconstitution.

This module defines the ``Factory[T]`` and ``ReconstitutionFactory[T]``
protocols — lightweight role interfaces for encapsulating complex creation
and rebuilding of domain objects (entities, value objects, aggregate roots).

Patterns covered
================

Factory protocol
    A structural protocol (``typing.Protocol``) that any class with a
    ``create`` method returning ``T`` automatically conforms to.  No base
    class inheritance is required.

Standalone Factory
    A concrete class implementing ``Factory[T]`` that centralises creation
    logic and can accept injected dependencies (repositories, APIs, pricing
    engines, ...).

Factory Method (on Aggregate Root)
    The aggregate root itself acts as a factory for its child entities
    (e.g. ``Order.add_line_item()`` returns or appends a ``LineItem``).
    No separate factory class is needed.

Reconstitution
    A dedicated ``ReconstitutionFactory`` protocol for **rebuilding**
    domain objects from persisted state (event-sourced aggregates, read
    models).  Reconstitution must **not** assign a new tracking identity;
    it preserves the existing one.

See the `KB article DCE-A-8 <https://mgourlis.youtrack.cloud/articles/DCE-A-8>`_
for detailed design rationale.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Factory[T](Protocol):
    """Protocol for domain object factories.

    A ``Factory[T]`` encapsulates complex creation and returns a fully
    constructed domain object of type ``T``.  Any class with a ``create``
    method that returns ``T`` structurally conforms to this protocol —
    no explicit inheritance is needed.

    Concrete factories typically receive injected dependencies via their
    ``__init__`` and expose a ``create`` with domain-specific parameters::

        class OrderFactory:
            def __init__(self, pricing: PricingService) -> None: ...

            def create(self, customer_id: UUID, *,
                       items: list[OrderItem]) -> Order: ...

    Two invariants hold for every factory:

    1. **Atomicity** — each ``create`` call is a single, all-or-nothing
       operation that either returns a valid object or raises.
    2. **Abstracted type** — the caller asks for the desired domain type
       (``Order``, ``Invoice``, ...), never for internal details.
    """

    def create(self, *args: Any, **kwargs: Any) -> T: ...


@runtime_checkable
class ReconstitutionFactory[T](Protocol):
    """Protocol for factories that rebuild domain objects from persisted state.

    Unlike ``Factory[T]`` which is used for **creation** (and may assign a
    new tracking identity), ``ReconstitutionFactory[T]`` is used during
    event-sourcing replay or repository read operations to **rebuild** an
    object from previously persisted data.  It must **never** generate a
    new tracking ID — the identity comes from the persisted data::

        class OrderReconstitutor:
            def reconstitute(
                self,
                id: UUID,
                customer_id: UUID,
                line_items: list[LineItem],
            ) -> Order: ...

    The ``reconstitute`` method is intentionally separate from ``create``
    to avoid accidentally mixing creation and rebuilding logic.
    """

    def reconstitute(self, *args: Any, **kwargs: Any) -> T: ...
