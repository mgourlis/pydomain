class DomainError(Exception):
    """Base class for all domain-layer errors."""


class ConcurrencyError(DomainError):
    """Optimistic concurrency conflict — the aggregate version changed."""


class AggregateNotFoundError(DomainError):
    """Repository cannot find an aggregate by its ID."""


class RepositoryError(DomainError):
    """Base class for repository-layer errors."""


class SpecificationError(DomainError):
    """A specification-based validation rule failed."""
