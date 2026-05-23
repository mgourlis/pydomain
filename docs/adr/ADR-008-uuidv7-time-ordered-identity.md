# ADR-008: UUIDv7 for Time-Ordered Identity Generation

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Entities, domain events, and commands all need unique identifiers. The choice of ID format affects:

1. **Database performance**: Sequential IDs provide better B-tree index locality than random UUIDs.
2. **Chronological sorting**: IDs that sort by creation time are useful for debugging and log correlation.
3. **Uniqueness guarantees**: IDs must be globally unique without coordination.
4. **External representation**: IDs appear in URLs, logs, and APIs.

UUIDv4 (random) is the most common Python UUID format. However, its randomness causes poor B-tree index locality in databases (random inserts across the entire key space) and provides no temporal ordering.

## Decision

Use **UUIDv7** (RFC 9562) as the default identifier format via `Uuid7Generator`:

```python
@runtime_checkable
class IdGenerator[TId](Protocol):
    def generate(self) -> TId: ...

class Uuid7Generator:
    def generate(self) -> UUID:
        return UUID(int=uuid7().int)
```

UUIDv7 combines a Unix timestamp (millisecond precision) with random bits, producing identifiers that are:
- **Time-ordered**: Monotonically increasing within the same millisecond.
- **Globally unique**: No coordination required.
- **Index-friendly**: Sequential inserts cluster in B-tree leaf pages.

The `IdGenerator[TId]` Protocol allows alternative implementations (Snowflake IDs, ULID, custom schemes) via `Entity.configure(id_generator=...)`.

The library uses `uuid_utils` package for UUIDv7 generation (Python stdlib `uuid` module added v7 support in 3.13 but `uuid_utils` provides broader compatibility).

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| UUIDv4 | Random — poor B-tree index locality; no temporal ordering |
| Sequential integers | Requires coordination (sequence/serial); not unique across services |
| ULID | Similar to UUIDv7 but not a standard UUID format; limited ecosystem support |
| Snowflake IDs | Requires a coordinator service; more complex to set up; `int` type less universally supported than `UUID` |
| UUIDv1 | Includes MAC address (privacy concern); not monotonic across machines |

## Consequences

### Positive

- Better database insert performance due to B-tree locality.
- Natural chronological sorting — IDs can be used as approximate timestamps for debugging.
- Drop-in replacement: `UUID` type is already widely supported by ORMs, serializers, and databases.
- The `IdGenerator` Protocol allows users to swap implementations without changing entity code.

### Negative

- UUIDv7 reveals creation timestamp (acceptable for this library; users can choose alternative generators if needed).
- Requires `uuid_utils` dependency (or Python 3.13+ stdlib).

### Neutral

- `DomainEvent`, `Command`, and `Entity` each have their own `_id_generator` ClassVar, configured independently via `configure()`.

## References

- `src/pydomain/ddd/id_generator.py` — `IdGenerator[TId]` Protocol, `Uuid7Generator`
- `src/pydomain/ddd/entity.py` — `Entity._id_generator` ClassVar
- `src/pydomain/ddd/domain_event.py` — `DomainEvent._id_generator` ClassVar
- `src/pydomain/cqrs/commands.py` — `Command._id_generator` ClassVar
