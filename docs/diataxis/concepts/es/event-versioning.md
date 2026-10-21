# Event Versioning

<!-- docs-test: runnable -->

<!-- docs-test: setup
from typing import Any, ClassVar
-->

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
│ (v1, v2, v3 events)  │
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ UpcasterRegistry     │
│ resolve(type, ver)   │
│ → [v1→v2, v2→v3]     │
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ EventRegistry        │
│ apply the chain,     │
│ check it ends at v3, │
│ set event_version=3  │
└────────┬─────────────┘
         ▼
┌──────────────────────┐
│ Application Code     │
│ (only sees v3)       │
└──────────────────────┘
```

## `EventUpcaster` — The Base Class

<!-- docs-test: skip -->
```python
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
from pydomain.es.upcasting import EventUpcaster


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

An upcaster changes the payload's fields only. It does not set `event_version`: the `EventRegistry` writes the version the chain reached ([below](#reading-events-through-the-eventregistry)).

## `UpcasterRegistry` — Chaining Upcasters

The registry resolves the full chain for a given event type and source version:

```python
from pydomain.es.upcasting import UpcasterRegistry


registry = UpcasterRegistry()
registry.register(OrderPlacedV1ToV2)
registry.register(OrderPlacedV2ToV3)

chain = registry.resolve("OrderPlaced", source_version=1)
assert chain == [OrderPlacedV1ToV2, OrderPlacedV2ToV3]  # apply in order
```

The registry follows `source_version → target_version` hops until no upcaster is registered for the next version. It does not know the event class, so it cannot tell a finished chain from one with a step missing; the `EventRegistry` checks that.

`register` checks each upcaster as it arrives:

| Mistake | Raises |
|---------|--------|
| `source_type`, `source_version` or `target_version` missing | `TypeError` |
| `target_version` not greater than `source_version` | `ValueError` |
| A second upcaster for the same `(source_type, source_version)` | `ValueError` |

```python
class OrderPlacedV2ToV3Again(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 2
    target_version: ClassVar[int] = 3

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        return event


try:
    registry.register(OrderPlacedV2ToV3Again)
except ValueError as exc:
    assert "already registered: OrderPlacedV2ToV3" in str(exc)
else:
    raise AssertionError("the duplicate was registered")
```

## Reading Events Through the `EventRegistry`

Upcasting runs when an `EventRegistry` with an attached `UpcasterRegistry` deserializes a stored event. It applies the chain for the stored `"version"`, checks that the chain ends at the class's version (the default of its `event_version` field), and writes that version to `event_version` before validation:

```python
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.event_registry import EventRegistry


class OrderPlaced(DomainEvent):
    event_version: int = 3
    total_amount: int
    currency: str


events = EventRegistry(upcaster_registry=registry)
events.register(OrderPlaced)

stored = {"type": "OrderPlaced", "version": 1, "data": {"event_version": 1, "total": 1000}}
event = events.deserialize(stored)
assert event.event_version == 3
assert (event.total_amount, event.currency) == (1000, "EUR")

# Written back, the event is at v3, so reading it again runs no upcaster.
assert events.deserialize(events.serialize(event)) == event
```

Each stored event is upcast exactly once. Before ADR-069 the event kept `event_version=1`, so writing it back and reading it again ran the chain a second time.

### A missing step raises `UpcastError`

If the chain stops short of the class's version, or goes past it, `deserialize` raises `UpcastError` instead of validating an old payload against the new class:

```python
from pydomain.es.exceptions import UpcastError


partial = UpcasterRegistry()
partial.register(OrderPlacedV1ToV2)  # no v2 → v3 step
gappy = EventRegistry(upcaster_registry=partial)
gappy.register(OrderPlaced)

try:
    gappy.deserialize(stored)
except UpcastError as exc:
    assert str(exc) == "Upcaster chain for 'OrderPlaced' ends at v2, but the class is at v3"
else:
    raise AssertionError("the v2 payload was validated as v3")
```

A version that needs no field changes still needs an upcaster: a `_transform` that returns the payload unchanged is enough.

## Cycle Detection

Because `register` refuses an upcaster whose `target_version` is not greater than its `source_version`, registered upcasters cannot form a cycle. `resolve` still tracks the versions it has visited and raises `UpcastError` ("Upcaster cycle detected for 'OrderPlaced' at version 1") if a class variable is changed after registration and the hops loop back.

## Design decisions

> **📌 ADR-042**: The upcaster registry uses a chain-resolve pattern with cycle detection. Each upcaster declares source and target versions as `ClassVar` attributes, making the chain self-describing and enabling static analysis of the version graph.
>
> **📌 ADR-069** (amends ADR-042): the chain must end at the class's version, the `EventRegistry` sets `event_version` after the chain, and `register` refuses incomplete, downgrading and duplicate upcasters.

## UpcastError

If a `_transform` raises any exception, `upcast()` wraps it in an `UpcastError` that names the event type and the version step; the original exception is the `__cause__`:

```python
class OrderPlacedV1ToV2Strict(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        event["total_amount"] = event.pop("total")  # KeyError if 'total' is missing
        return event


try:
    OrderPlacedV1ToV2Strict().upcast({})
except UpcastError as exc:
    assert isinstance(exc.__cause__, KeyError)
else:
    raise AssertionError("the upcaster did not fail")
```

An upcaster that overrides `upcast()` itself is not wrapped: its exceptions, `KeyError` included, reach the caller of `deserialize` unchanged.

## Relationship to other concepts

- **Domain Events**: carry the `event_version` field; its default is the class's version, which every upcaster chain must reach
- **Event Registry**: applies the chain in `deserialize()` and sets `event_version`
- **Event Store**: stores raw events at various versions; upcasters transform on read
- **Snapshot Schema Versioning**: analogous version tracking for aggregate snapshots

## Common pitfalls

> **⚠️** **One upcaster = one version step.** Prefer an upcaster from vN to vN+1. The registry accepts a jump (v1 → v3), but one step per upcaster keeps the chain composable and each step testable on its own.

> **⚠️** **Upcasters work on dicts, not DomainEvent objects.** The input and output are raw `dict[str, Any]` payloads. This is deliberate — upcasters run at the serialization boundary, before deserialization into typed event objects.

> **⚠️** **Bumping `event_version` needs an upcaster.** With an `UpcasterRegistry` attached, a class whose version has no chain from a stored version raises `UpcastError` on read, even when the change was additive.

## Next steps

- [How to implement an upcaster](../../how-to/event-sourcing/implement-upcaster.md) — step-by-step guide
- [Event Registry](../infrastructure/event-registry.md) — where the chain runs
- [Projections](projections.md) — consumers that benefit from upcasted events
- [Handle ES errors](../../how-to/event-sourcing/handle-es-errors.md) — error handling patterns
