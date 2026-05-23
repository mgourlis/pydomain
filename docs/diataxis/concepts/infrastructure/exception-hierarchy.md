# Exception Hierarchy

> **Adoption Level:** 1 — Tactical DDD
> **Module:** `pydomain.ddd.exceptions`

## Overview

pydomain provides a small, focused exception hierarchy rooted in `DomainError`. All domain-layer exceptions inherit from this base, making it easy to catch domain failures uniformly while distinguishing specific error types when needed.

## The Hierarchy

```
DomainError                      # Base for all domain errors
├── ConcurrencyError             # Optimistic concurrency conflict
└── SpecificationError           # A specification validation rule failed
```

### `DomainError`

The base exception for all domain-layer failures:

```python
class DomainError(Exception):
    """Base class for all domain-layer errors."""
```

Catch this when you want to handle any domain failure uniformly:

```python
try:
    order.place()
except DomainError as e:
    # Handle any domain-level failure
    logger.warning(f"Domain error: {e}")
```

### `ConcurrencyError`

Raised when an optimistic concurrency check fails — the aggregate's `version` doesn't match the expected version in the store:

```python
class ConcurrencyError(DomainError):
    """Optimistic concurrency conflict — the aggregate version changed."""
```

This typically happens when two concurrent requests try to modify the same aggregate. The [Repository](repositories.md) checks the version during `save()` and raises `ConcurrencyError` if they don't match.

**Handling strategy:** Retry the operation by re-loading the aggregate, reapplying the command, and saving again.

### `SpecificationError`

Raised when a [Specification](specifications.md)-based validation rule fails:

```python
class SpecificationError(DomainError):
    """A specification-based validation rule failed."""
```

## Design Principles

### Domain Errors, Not Technical Errors

`DomainError` and its subclasses represent **business rule violations**, not infrastructure failures. They encode concepts from the Ubiquitous Language:

- `ConcurrencyError` → "someone else modified this aggregate while you were working"
- `SpecificationError` → "this object doesn't satisfy the required business rule"

Technical failures (database connection drops, network timeouts) should use standard Python exceptions or infrastructure-specific error types — they don't belong in the domain exception hierarchy.

### Keep It Flat

The hierarchy is intentionally flat and small. Only add new exception types when:

1. The error represents a **distinct domain concept**
2. Callers need to **handle it differently** from other domain errors
3. The distinction has **business meaning**, not just technical convenience

### Don't Raise Exceptions for Domain Events

If a domain concept is expressed as a [Domain Event](domain-events.md), don't also raise an exception for it. Events and exceptions serve different purposes:

- **Events** → something happened that other parts of the system should react to
- **Exceptions** → something went wrong that the current operation cannot continue past

## Next Steps

- **[Handle Domain Errors →](../../how-to/ddd/handle-domain-errors.md)** — patterns for error handling
- **[Specifications →](specifications.md)** — where `SpecificationError` comes from
- **[Repositories →](repositories.md)** — where `ConcurrencyError` originates
