# Event Versioning

> **Adoption Level:** 4 — Event Sourcing
> **Module:** `pydomain.es.upcasting`
> **Prerequisites:** [Event Sourcing](event-sourcing.md), [Domain Events](../ddd/domain-events.md)

## What is Event Versioning?

Events are immutable once persisted, but their schemas evolve over time as the domain changes. **Event Versioning** is the mechanism for handling these changes: old-format events are transformed ("upcasted") to the current schema when read, so that application code only ever sees the current event format.

Without versioning, every handler and projection would need to understand every historical event format — a maintenance nightmare that grows with every schema change.

## The Versioning Pipeline

```
┌──────────────────────┐
│ Event Store          │
│ (v1, v2, v3 events) │
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ UpcasterRegistry     │
│ resolve(type, ver)   │
│ → [v1→v2, v2→v3]    │
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ Chain Application    │
│ v1 event → v2 → v3   │
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ Application Code     │
│ (only sees v3)       │
└──────────────────────┘
```

## `EventUpcaster` — The Base Class

```python
from pydomain.es.upcasting import EventUpcaster


class EventUpcaster:
    source_type: ClassVar[str]       # Event type to upcast FROM
    source_version: ClassVar[int]    # Schema version to upcast FROM
    target_version: ClassVar[int]    # Schema version to upcast TO

    def upcast(self, event: dict[str, Any]) -> dict[str, Any]: ...

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        # Subclass implements the actual transformation
        raise NotImplementedError
```

## Defining an Upcaster

Each upcaster handles one version step for one event type:

```python
class OrderPlacedV1ToV2(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        # v2 renamed 'total' to 'total_amount'
        event["total_amount"] = event.pop("total")
        return event


class OrderPlacedV2ToV3(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 2
    target_version: ClassVar[int] = 3

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        # v3 added a required 'currency' field with a default
        if "currency" not in event:
            event["currency"] = "EUR"
        return event
```

## `UpcasterRegistry` — Chaining Upcasters

The registry resolves the full chain for a given event type and source version:

```python
from pydomain.es.upcasting import UpcasterRegistry


registry = UpcasterRegistry()
registry.register(OrderPlacedV1ToV2)
registry.register(OrderPlacedV2ToV3)

chain = registry.resolve("OrderPlaced", source_version=1)
# Returns [OrderPlacedV1ToV2, OrderPlacedV2ToV3] — apply in order
```

The registry follows `source_version → target_version` hops. If no upcaster is registered for a given version, the chain stops — the event is already at the latest known format.

## Cycle Detection

The registry detects cycles in the upcaster chain:

```python
registry.register(InfiniteLoopV2ToV1)  # v2 → v1

chain = registry.resolve("OrderPlaced", source_version=1)
# Raises UpcastError: "Upcaster cycle detected for 'OrderPlaced' at version 1"
```

## Design decisions

> **📌 ADR-042**: The upcaster registry uses a chain-resolve pattern with cycle detection. Each upcaster declares source and target versions as `ClassVar` attributes, making the chain self-describing and enabling static analysis of the version graph.

## UpcastError

If a `_transform` raises any exception, it is wrapped in an `UpcastError` with context about which transformation failed:

```python
class OrderPlacedV1ToV2(EventUpcaster):
    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        try:
            event["total_amount"] = event["total"]  # KeyError if 'total' missing
            return event
        except KeyError:
            raise  # Wrapped as UpcastError by upcast()
```

## Relationship to other concepts

- **Domain Events**: carry `event_version` field that determines which upcaster chain to apply
- **Event Store**: stores raw events at various versions; upcasters transform on read
- **Snapshot Schema Versioning**: analogous version tracking for aggregate snapshots

## Common pitfalls

> **⚠️** **One upcaster = one version step.** An upcaster goes from vN to vN+1. Never write an upcaster that jumps multiple versions — register one per step to keep the chain composable and reversible.

> **⚠️** **Upcasters work on dicts, not DomainEvent objects.** The input and output are raw `dict[str, Any]` payloads. This is deliberate — upcasters run at the serialization boundary, before deserialization into typed event objects.

## Next steps

- [How to implement an upcaster](../../how-to/event-sourcing/implement-upcaster.md) — step-by-step guide
- [Projections](projections.md) — consumers that benefit from upcasted events
- [Handle ES errors](../../how-to/event-sourcing/handle-es-errors.md) — error handling patterns
