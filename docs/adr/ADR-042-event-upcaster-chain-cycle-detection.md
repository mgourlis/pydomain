# ADR-042: EventUpcaster Chain with Cycle Detection

## Status

Accepted

## Date

Retroactive — documented from existing implementation.

## Context

Event schemas evolve over time. Events are immutable once persisted, so old-format events must be transformed ("upcasted") to the current schema when read. An event may need to pass through multiple upcasting steps (v1 → v2 → v3).

The registry must:
1. Find the correct upcaster chain for a given event type and source version.
2. Apply upcasters in order (v1 → v2, then v2 → v3).
3. Detect cycles (v1 → v2 → v1) to prevent infinite loops.

## Decision

`UpcasterRegistry` resolves a chain of upcasters by following `source_version → target_version` hops:

```python
class EventUpcaster:
    source_type: ClassVar[str]
    source_version: ClassVar[int]
    target_version: ClassVar[int]

    def upcast(self, event: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._transform(event)
        except Exception as exc:
            raise UpcastError(...) from exc
```

```python
class UpcasterRegistry:
    def resolve(self, source_type: str, source_version: int) -> list[type[EventUpcaster]]:
        chain = []
        version = source_version
        visited: set[int] = set()

        while True:
            if version in visited:
                raise UpcastError(f"Cycle detected at version {version}")
            visited.add(version)

            upcaster = self._upcasters.get((source_type, version))
            if upcaster is None:
                break
            chain.append(upcaster)
            version = upcaster.target_version

        return chain
```

**Cycle detection**: `visited` set tracks seen versions. If a version is revisited, an `UpcastError` is raised.

**Error handling**: `_transform()` exceptions are wrapped in `UpcastError` with context (event type, source/target versions).

**Registration**: Upcasters declare their source and target via ClassVars:

```python
class OrderPlacedV1ToV2(EventUpcaster):
    source_type = "OrderPlaced"
    source_version = 1
    target_version = 2

    def _transform(self, event):
        event["discount"] = 0.0  # Add new field with default
        return event
```

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Single upcaster per event type (no chaining) | Cannot handle multi-version gaps; requires upcaster for every (v1, v3) pair |
| No cycle detection | Infinite loop on misconfigured registry; crashes at runtime |
| Auto-discovery via subclass inspection | Fragile; depends on import order; explicit registration is clearer |

## Consequences

### Positive

- Multi-step upcasting (v1 → v2 → v3) is automatic — register each step, the chain resolves.
- Cycle detection prevents infinite loops from misconfigured upcasters.
- `UpcastError` provides clear context (event type, versions) for debugging.
- Upcasters are plain classes — easy to test in isolation.

### Negative

- Cycle detection adds a `set[int]` per resolve call (negligible cost).
- Registration is manual — must register each upcaster explicitly.

### Neutral

- Upcasters transform `dict[str, Any]` → `dict[str, Any]` — they operate on raw payloads, not typed events.

## References

- `src/pydomain/es/upcasting.py` — `EventUpcaster`, `UpcasterRegistry`
- `src/pydomain/es/exceptions.py` — `UpcastError`
