# ADR-000: Index of Architecture Decision Records

This index lists all Architecture Decision Records (ADRs) for the `pydomain` library.
ADRs capture **why** a design choice was made, not just **what** was decided.

## Base / Foundational (ADR-001 – ADR-005)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-001](ADR-001-protocol-over-abc-for-interfaces.md) | Protocol over ABC for interfaces | Accepted | Retroactive |
| [ADR-002](ADR-002-pydantic-v2-only.md) | Pydantic v2 only — no v1 compatibility shims | Accepted | Retroactive |
| [ADR-003](ADR-003-async-only-public-api.md) | Async-only public API | Accepted | Retroactive |
| [ADR-004](ADR-004-exception-hierarchy-by-layer.md) | Exception hierarchy by layer | Accepted | Retroactive |
| [ADR-005](ADR-005-publish-after-commit.md) | Publish events after commit, never before | Accepted | Retroactive |

## DDD Module (ADR-006 – ADR-013)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-006](ADR-006-entity-identity-semantics.md) | Entity identity semantics — `id: TId` with structural equality | Accepted | Retroactive |
| [ADR-007](ADR-007-entity-auto-id-with-runtime-guard.md) | Entity auto-ID with runtime type guard | Accepted | Retroactive |
| [ADR-008](ADR-008-uuidv7-time-ordered-identity.md) | UUIDv7 time-ordered identity generation | Accepted | Retroactive |
| [ADR-009](ADR-009-domain-service-marker.md) | DomainService as a marker class, not a base class | Accepted | Retroactive |
| [ADR-010](ADR-010-specification-abc-basemodel-hybrid.md) | Specification as ABC + BaseModel hybrid | Accepted | Retroactive |
| [ADR-011](ADR-011-domainevent-stamp-immutability.md) | DomainEvent `stamp()` preserves immutability | Accepted | Retroactive |
| [ADR-012](ADR-012-isinstance-dispatch-in-aggregates.md) | isinstance dispatch in aggregate `_when()` | Accepted | Retroactive |
| [ADR-013](ADR-013-factory-vs-reconstitution-factory.md) | Factory vs ReconstitutionFactory separation | Accepted | Retroactive |

## CQRS Module (ADR-014 – ADR-026)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-014](ADR-014-frozen-commands-queries.md) | `frozen=True` and `extra="forbid"` on commands and queries | Accepted | Retroactive |
| [ADR-015](ADR-015-typed-command-query-generics.md) | Typed `Command[TResult]` / `Query[TResult]` with generic result binding | Accepted | Retroactive |
| [ADR-016](ADR-016-handler-signature-asymmetry.md) | Handler signature asymmetry — CommandHandler gets UoW, others don't | Accepted | Retroactive |
| [ADR-017](ADR-017-onion-style-pipeline-behaviors.md) | Onion-style pipeline behaviors | Accepted | Retroactive |
| [ADR-018](ADR-018-missing-sentinel-idempotency.md) | MISSING sentinel for idempotency | Accepted | Retroactive |
| [ADR-019](ADR-019-sorted-lock-keys-deadlock-prevention.md) | Sorted lock keys for deadlock prevention | Accepted | Retroactive |
| [ADR-020](ADR-020-commandbus-owns-uow-lifecycle.md) | CommandBus owns UoW lifecycle | Accepted | Retroactive |
| [ADR-021](ADR-021-correlation-causation-propagation.md) | Correlation/Causation propagation via UoW stamping | Accepted | Retroactive |
| [ADR-022](ADR-022-integration-events-primitive-payloads.md) | Integration events — primitive-only payloads | Accepted | Retroactive |
| [ADR-023](ADR-023-integration-event-bypasses-id-generator.md) | IntegrationEvent bypasses IdGenerator — uses `uuid7` directly | Accepted | Retroactive |
| [ADR-024](ADR-024-two-projection-types.md) | Two separate projection types by naming convention | Accepted | Retroactive |
| [ADR-025](ADR-025-projection-split-across-layers.md) | Projection split across layers — CQRS Protocol, ES implementation | Accepted | Retroactive |
| [ADR-026](ADR-026-no-generic-readstore.md) | No generic ReadStore protocol — user-defined read contracts | Accepted | Retroactive |

## SAGA Subsystem (ADR-027 – ADR-036)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-027](ADR-027-saga-state-as-aggregate-root.md) | Saga state as AggregateRoot | Accepted | Retroactive |
| [ADR-028](ADR-028-saga-on-dsl.md) | Saga `on()` DSL for unified command and compensation | Accepted | Retroactive |
| [ADR-029](ADR-029-generic-saga-parameterized-by-state.md) | Generic `Saga[S: SagaState]` parameterized by state type | Accepted | Retroactive |
| [ADR-030](ADR-030-saga-registry-auto-binding.md) | SagaRegistry auto-binding via `listens_to` | Accepted | Retroactive |
| [ADR-031](ADR-031-saga-manager-separate-orchestrator.md) | SagaManager as separate orchestrator | Accepted | Retroactive |
| [ADR-032](ADR-032-saga-correlation-propagation.md) | Saga correlation via `event.correlation_id` | Accepted | Retroactive |
| [ADR-033](ADR-033-lifo-compensation-stack.md) | LIFO compensation stack via serialized `CompensationRecord` | Accepted | Retroactive |
| [ADR-034](ADR-034-saga-suspension-with-timeout.md) | Saga suspension with timeout (human-in-the-loop) | Accepted | Retroactive |
| [ADR-035](ADR-035-crash-recovery-pending-commands.md) | Crash recovery via `pending_commands` per-command tracking | Accepted | Retroactive |
| [ADR-036](ADR-036-saga-idempotency-processed-events.md) | Saga idempotency via `processed_event_ids` set | Accepted | Retroactive |

## Event Sourcing Module (ADR-037 – ADR-043)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-037](ADR-037-eventstream-frozen-value-object.md) | EventStream as frozen value object | Accepted | Retroactive |
| [ADR-038](ADR-038-dual-mode-event-application.md) | Dual-mode event application — `_apply()` records, `_replay()` reconstitutes | Accepted | Retroactive |
| [ADR-039](ADR-039-convention-dispatch-projections.md) | Convention dispatch in projections — `_when_{TypeName}` methods | Accepted | Retroactive |
| [ADR-040](ADR-040-event-sourced-repository-concrete-base.md) | EventSourcedRepository as concrete base class | Accepted | Retroactive |
| [ADR-041](ADR-041-optimistic-concurrency-command-idempotency.md) | Optimistic concurrency via `expected_version` + `command_id` idempotency | Accepted | Retroactive |
| [ADR-042](ADR-042-event-upcaster-chain-cycle-detection.md) | EventUpcaster chain with cycle detection | Accepted | Retroactive |
| [ADR-043](ADR-043-snapshot-policy-pluggable-protocol.md) | Snapshot policy as pluggable protocol | Accepted | Retroactive |

## Infrastructure (ADR-044 – ADR-049)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-044](ADR-044-dynamic-event-registry-generic-fallback.md) | Dynamic event registry with `GenericDomainEvent` fallback | Accepted | Retroactive |
| [ADR-045](ADR-045-messagebus-level3-facade.md) | MessageBus as Level 3 facade | Accepted | Retroactive |
| [ADR-046](ADR-046-event-handlers-fail-independently.md) | Event handlers fail independently — per-handler try/except | Accepted | Retroactive |
| [ADR-047](ADR-047-bootstrap-composition-root.md) | `bootstrap()` composition root | Accepted | Retroactive |
| [ADR-048](ADR-048-subscription-runner-at-least-once.md) | SubscriptionRunner at-least-once delivery | Accepted | Retroactive |
| [ADR-049](ADR-049-catch-up-subscriptions-polling.md) | Catch-up subscriptions via polling SubscriptionRunner | Accepted | Retroactive |

## Cross-Cutting (ADR-050 – ADR-052)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-050](ADR-050-aggregate-pending-events-private-attr.md) | AggregateRoot `_pending_events` as `PrivateAttr` | Accepted | Retroactive |
| [ADR-051](ADR-051-message-broker-separate-boundary.md) | `MessageBroker` Protocol — separate boundary from MessageBus | Accepted | Retroactive |
| [ADR-052](ADR-052-checkpoint-store-vs-snapshot-store.md) | `CheckpointStore` vs `SnapshotStore` — two separate persistence concerns | Accepted | Retroactive |

## Event Sourcing — Snapshot Schema (ADR-053)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-053](ADR-053-snapshot-schema-version-policy.md) | Snapshot schema version policy for stale snapshot detection | Accepted | 2026-05-22 |

## SAGA Subsystem — Pruning Policy (ADR-054)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-054](ADR-054-saga-pruning-policy-pluggable-protocol.md) | Saga pruning policy as pluggable protocol | Accepted | 2026-05-22 |

## SAGA Subsystem — Declarative Failure (ADR-055)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-055](ADR-055-declarative-saga-failure-fail-true.md) | Declarative saga failure via `fail=True` and callable reason parameters | Accepted | 2026-05-22 |

## SAGA Subsystem — Resume Authorization (ADR-056)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-056](ADR-056-declarative-resume-authorization-resumes-from.md) | Declarative resume authorization via `resumes_from` and `should_resume` | Accepted | 2026-05-22 |

## SAGA Subsystem — Default Timeout Sentinel (ADR-057)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-057](ADR-057-default-timeout-sentinel-step-override.md) | Class-level default timeout with sentinel and step overrides | Accepted | 2026-05-22 |

## Infrastructure — DomainEvent Dispatch (ADR-058)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-058](ADR-058-messagebus-dispatch-domain-event.md) | MessageBus dispatch extended for DomainEvent | Accepted | 2026-05-22 |

## Infrastructure — MessageSubscriber Protocol (ADR-059)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-059](ADR-059-message-subscriber-protocol.md) | MessageSubscriber Protocol — subscriber-side counterpart to MessageBroker | Accepted | 2026-05-22 |

## Infrastructure — InboundEventGateway (ADR-060)

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-060](ADR-060-inbound-event-gateway.md) | InboundEventGateway — bridging external brokers to the internal MessageBus | Accepted | 2026-05-22 |

## Superseded

_None._

## Deprecated

_None._

## Conventions

- **Numbering**: Sequential (`ADR-001`, `ADR-002`, ...). Never reuse a number.
- **Naming**: `ADR-NNN-kebab-case-title.md`.
- **Status**: `Proposed` → `Accepted` → `Deprecated` or `Superseded by ADR-NN`.
- **Immutability**: Once `Accepted`, an ADR is not edited. If the decision changes, write a new ADR that supersedes it.
- **Template**: See [TEMPLATE.md](TEMPLATE.md).
