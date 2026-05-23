class DomainError(Exception):
    """Base class for all domain-layer errors."""


class ConcurrencyError(DomainError):
    """Optimistic concurrency conflict — the aggregate version changed."""


class SpecificationError(DomainError):
    """A specification-based validation rule failed."""
