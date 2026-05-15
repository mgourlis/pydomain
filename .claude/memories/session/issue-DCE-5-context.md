# Issue Context: DCE-5

## Summary
- **Title:** Level 5 — Advanced Event Sourcing
- **Type:** Epic
- **State:** Open
- **Priority:** Normal
- **Assignee:** Unassigned
- **Tags:** None

## Classification
**Artifact type:** Epic/Container — not directly actionable; needs subtask decomposition
**Clarity:** Well-specified via KB articles (DCE-A-19, DCE-A-20, DCE-A-25, DCE-A-5, DCE-A-26)

## Description (verbatim)
Production-grade event sourcing with operational maturity: snapshots, schema evolution, idempotency, and distributed tracing.

Four scope areas:
1. **Snapshot Store** (DCE-A-19) — Aggregate state caching for hydration optimization
2. **Upcasting** (DCE-A-20) — Schema evolution without modifying stored events
3. **Idempotency** (DCE-A-25) — command_id field, transactional enforcement
4. **Causation & Correlation** (DCE-A-5, DCE-A-26) — Distributed tracing

## Link Graph
### Hierarchy
- **Parent:** None (top-level Epic)
- **Children:** None (no subtasks created yet)
- **Siblings:** DCE-1 through DCE-4 (all Done)

### Dependencies
- **Depends on:** DCE-4 (Level 4 — Event Sourcing) → **Done** ✓
- **Required for:** Nothing (terminal epic)

## Implementation Status

| Component | KB Spec | Code Status | Location |
|-----------|---------|-------------|----------|
| **Snapshot Store** | DCE-A-19 (full) | ❌ Missing | `es/snapshot.py` |
| **Upcasting** | DCE-A-20 (full) | ❌ Missing | `es/upcasting.py` |
| **Idempotency** | DCE-A-25 (full) | ✅ Core done (CQRS layer) | `cqrs/idempotency.py`, `cqrs/behaviors.py` |
| **Causation/Correlation** | DCE-A-5, DCE-A-26 (full) | ⚠️ Partial (in DomainEvent + UoW) | Missing: `es/causation.py` |
| **ES Module (existing)** | DCE-A-17, DCE-A-18 | ✅ Done | `es/aggregate.py`, `es/event_store.py`, etc. |

### Resolved References
#### KB Articles
- **DCE-A-19 — Snapshots** — `Snapshot` model, `SnapshotStore` protocol, threshold/time/manual strategies, GDPR considerations
- **DCE-A-20 — Upcasting & Event Versioning** — `EventUpcaster` base class, weak-schema, type-based, copy-replace strategies
- **DCE-A-25 — Command Idempotency** — Already implemented: `command_id` field, `IdempotencyBehavior`, `ProcessedCommandStore` Protocol, `MISSING` sentinel, `IdempotentCommandIgnored` exception. Missing: ES-specific hard enforcement (unique index on causation_id in event store)
- **DCE-A-5 — Domain Events** — `correlation_id`/`causation_id` already on DomainEvent, `stamp()` method implemented
- **DCE-A-26 — Design Decisions** — Library structure shows planned `es/causation.py` with `CausationId`/`CorrelationId` types

## Existing Code That Already Implements Parts of DCE-5
- `ddd/domain_event.py:40-55` — `correlation_id`, `causation_id` fields + `stamp()` method
- `cqrs/unit_of_work.py:189-198` — UoW stamps events with correlation/causation IDs on commit
- `infrastructure/message_bus.py:235-236` — Propagates tracing IDs to event handlers
- `cqrs/behaviors.py:44-45` — MessageContext has correlation_id/causation_id
- `cqrs/behaviors.py:154-169` — LoggingBehavior logs tracing IDs
- `cqrs/behaviors.py:277-310` — IdempotencyBehavior already implemented
- `cqrs/idempotency.py` — ProcessedCommandStore protocol, MISSING sentinel
- `cqrs/exceptions.py:36-47` — IdempotentCommandIgnored exception

## What Needs to Be Built

### 1. Snapshot Store (`es/snapshot.py`)
- `Snapshot` Pydantic model (aggregate_id, version, state, created_at)
- `SnapshotStore` Protocol (save, get)
- In-memory implementation for testing
- Integration with `EventSourcedAggregateRoot` hydration flow

### 2. Upcasting (`es/upcasting.py`)
- `EventUpcaster` base class (source_type, source_version, target_version, upcast())
- Upcaster registry + chained pipeline
- Integration with event store deserialization

### 3. Causation/Correlation Types (`es/causation.py`)
- `CausationId` / `CorrelationId` type aliases or wrappers (optional — already in DomainEvent)
- Tracing utilities for cross-aggregate workflows

### 4. ES-Specific Idempotency
- Hard enforcement via event store's unique index on `(aggregate_id, causation_id)` — user-provided but needs Protocol/extension point

## Ambiguity Assessment
**Is this issue actionable without clarification?** Yes — KB articles provide detailed specs. However, as an Epic it needs decomposition into subtasks before implementation.

**Recommended decomposition:**
1. Task: Snapshot Store implementation (DCE-A-19)
2. Task: Upcasting pipeline implementation (DCE-A-20)
3. Task: Causation/correlation types and tracing utilities
4. Task: ES-specific idempotency hard enforcement integration
