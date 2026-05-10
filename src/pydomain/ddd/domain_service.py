"""Domain Service marker base class.

A DomainService holds business logic that does not naturally belong to
any single Entity or Value Object.  It is stateless, lives in the domain
layer, and coordinates operations spanning multiple aggregates.

In Python, many domain operations are better expressed as standalone
functions rather than classes — see the module examples below.  The
``DomainService`` base class exists as a lightweight architectural
marker: it carries no state or behaviour of its own, but signals that
the class belongs to the domain layer, has no infrastructure imports,
and holds no mutable instance data.

See the `KB article DCE-A-9
<https://mgourlis.youtrack.cloud/articles/DCE-A-9>`_ for design rationale
and guidance on when to use a service vs a standalone function.
"""


class DomainService:
    """Marker base class for domain services.

    A domain service encapsulates domain logic that does not naturally
    belong to a single Entity or Value Object.  Subclasses may accept
    injected dependencies via ``__init__`` (e.g. rate providers, pricing
    engines) and expose their operation through methods named from the
    Ubiquitous Language.

    This class carries no mutable state — concrete implementations should
    also be stateless.  Its sole value is architectural: it signals that
    this component lives in the domain layer.

    When NOT to use
    ----------------
    - When the operation naturally fits on an Entity or Value Object.
    - When a standalone function would be clearer.  Python favours
      functions over classes where no persistent state is needed.
    """

    __slots__ = ()
