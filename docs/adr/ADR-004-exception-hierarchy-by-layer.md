# ADR-004: Exception Hierarchy Organized by Layer

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

The library spans multiple architectural layers (DDD, CQRS, SAGA, Event Sourcing, Infrastructure). Each layer has distinct error conditions. Using a flat exception hierarchy or, worse, leaking built-in exceptions makes it impossible for consumers to catch layer-specific errors or distinguish between domain violations and infrastructure failures.

Consumers need to:
- Catch domain errors separately from infrastructure errors.
- Handle optimistic concurrency conflicts specifically (retry logic).
- Distinguish between "no handler found" (misconfiguration) and "handler failed" (runtime error).
- Identify saga-specific failure modes (compensation failures, timeout, handler not found).

## Decision

Each layer defines its own exception hierarchy rooted in a layer-specific base class:

### DDD Layer (`ddd/exceptions.py`)

```
DomainError                          # Base for all domain-layer errors
├── ConcurrencyError                 # Optimistic concurrency conflict
└── SpecificationError               # Specification-based validation failed
```

`DomainError` is also the root for Event Sourcing errors — the ES layer does not define a separate base because ES-specific errors are domain concerns.

### CQRS Layer (`cqrs/exceptions.py`)

```
CQRSError                            # Base for all CQRS-layer errors
├── CommandExecutionError            # Wraps handler failures with the command context
├── HandlerAlreadyRegisteredError    # Duplicate handler registration
├── NoHandlerRegisteredError         # Dispatch with no registered handler
└── QueryExecutionError              # Wraps query handler failures
```

### SAGA Layer (`cqrs/saga/exceptions.py`)

```
SagaError                            # Base for all saga errors
├── SagaConfigurationError           # Invalid saga setup (e.g., both handler and send)
├── SagaHandlerNotFoundError         # No handler registered for event type
├── SagaStateNotFoundError           # Saga state not found in repository
└── SagaAlreadyTerminalError         # Operation on completed/failed saga
```

### Event Sourcing Layer (`es/exceptions.py`)

```
StreamNotFoundError(DomainError)     # Aggregate stream does not exist
├── DuplicateCommandError            # Store-level idempotency conflict
└── EventVersionMismatchError        # Upcasting version mismatch
```

ES exceptions extend `DomainError` because event-sourcing failures are domain-level concerns (stream not found = aggregate not found; version mismatch = concurrency conflict).

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Flat exception hierarchy with `DomainError` for everything | Impossible to catch CQRS-specific vs domain-specific errors; poor separation of concerns |
| Built-in exceptions only (`ValueError`, `RuntimeError`) | No layer discrimination; consumers cannot catch selectively |
| Single `PydomainError` base | Too broad; forces consumers to inspect error messages rather than types |
| ES exceptions extend a separate `ESError` base | ES failures are domain-level concerns (aggregate not found, concurrency) — extending `DomainError` is semantically correct |

## Consequences

### Positive

- Consumers can catch errors at the granularity they need (`DomainError` for all domain issues, `ConcurrencyError` for retry logic, `CommandExecutionError` for handler failures).
- Layer discipline is enforced by the type system — infrastructure code cannot raise `DomainError`, domain code cannot raise `CommandExecutionError`.
- Clear mapping: each exception class lives in the module that raises it.

### Negative

- More exception classes to learn for new consumers.
- Cross-layer error handling requires knowing the hierarchy (e.g., `StreamNotFoundError` is a `DomainError`).

### Neutral

- `CommandExecutionError` wraps the original exception via `raise ... from exc`, preserving the full traceback.

## References

- `src/pydomain/ddd/exceptions.py` — `DomainError`, `ConcurrencyError`, `SpecificationError`
- `src/pydomain/cqrs/exceptions.py` — `CQRSError`, `CommandExecutionError`, `HandlerAlreadyRegisteredError`, `NoHandlerRegisteredError`
- `src/pydomain/cqrs/saga/exceptions.py` — `SagaError`, `SagaConfigurationError`, `SagaHandlerNotFoundError`
- `src/pydomain/es/exceptions.py` — `StreamNotFoundError`, `DuplicateCommandError`
