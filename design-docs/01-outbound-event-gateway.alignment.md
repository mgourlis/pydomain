# Alignment: Outbound Event Flow

- **Date:** 2026-09-29
- **Brief:** In conversation context (Discovery stage intentionally skipped — requirement stated directly by maintainer: *"take events from the Outbox, transform them to IntegrationEvents and then publish them"*, with translation confirmed to occur at **write** time).
- **Research:** `design-docs/01-outbound-event-gateway.research.md`
- **Decision:** Design **B′** — outbox stores serialized `IntegrationEvent`s; `DomainEvent → IntegrationEvent` translation happens inside `_write_outbox()`, within the transaction.

---

## Phase 1 — Patterns I intend to follow

- **Pattern:** Gateway is a concrete class with injected collaborators and a synchronous registration method
- **Found in:** `InboundEventGateway` (`infrastructure/message_subscriber.py`) — 1/1 existing gateways
- **Intend to follow:** yes
- **Reason:** It is the only gateway in the library; symmetry makes the outbound path learnable from the inbound one.

- **Pattern:** Behavioural ports are `@runtime_checkable` Protocols located in `cqrs/`
- **Found in:** `LockProvider`, `LockKeyResolver`, `ProcessedMessageStore`, `UnitOfWork`, `CommandHandler` — the whole `cqrs/` layer; charter stated in `infrastructure/__init__.py` docstring
- **Intend to follow:** yes
- **Reason:** Documented layer charter: *"Interfaces and abstract base classes live in the CQRS layer as Clean Architecture ports."*

- **Pattern:** Persistence ports are deliberately minimal (2–3 methods)
- **Found in:** `CheckpointStore` (2), `SnapshotStore` (2), `EventStore` (3) — 3/3
- **Intend to follow:** yes
- **Reason:** Keeps adapters cheap to implement and the port easy to fake.

- **Pattern:** Concurrency requirements are documented on the *implementation*, not expressed as extra port methods
- **Found in:** `ProcessedMessageStore` docstring — *"Implementations should provide atomic check-and-set semantics to prevent races"*
- **Intend to follow:** yes
- **Reason:** 4-method port instead of 6; `SKIP LOCKED` is a Postgres concern that should not leak into the contract.

- **Pattern:** Composition over inheritance; no forced extra base classes
- **Found in:** ADR-001 (*"Forcing it to also inherit from a library ABC creates coupling and MRO complications"*), `InboundEventGateway`, `SagaManager`, `DictLockKeyResolver`
- **Intend to follow:** yes
- **Reason:** A second library base on top of `AbstractUnitOfWork` is exactly the hazard ADR-001 names.

- **Pattern:** Every Protocol ships a `Fake*` double in `pydomain/testing/`
- **Found in:** 12 fakes for 12 ports
- **Intend to follow:** yes
- **Reason:** Users must be able to test without Postgres or RabbitMQ.

- **Pattern:** Durable polling loops expose `run()` / `run_once()` / `stop()` with `poll_interval_seconds` and `failure_backoff_seconds`
- **Found in:** `SubscriptionRunner` (`infrastructure/subscription.py`) — 1/1 loops
- **Intend to follow:** yes
- **Reason:** Established operational shape; `run_once()` is what makes loops testable deterministically.

- **Pattern:** Type resolution is by **topic**, not by a serialized-payload registry
- **Found in:** ADR-060 Alternatives — *"Introduces infrastructure with no benefit"* for `EventRegistry`
- **Intend to follow:** yes
- **Reason:** Decisive for B′. The outbox row carries `topic`; the gateway resolves the integration class from it.

- **Pattern:** `**kwargs: Any` on transport methods
- **Found in:** `MessageBroker.publish`, `MessageSubscriber.subscribe`
- **Intend to follow:** **partially** — P1 passes only `headers`; typed `PublishOptions` deferred to P3
- **Reason:** Typing it properly is a breaking change to a protocol with existing implementers; not required to unblock the tracer bullet.

- **Pattern:** `EventBus` handlers fail soft (exceptions logged and swallowed)
- **Found in:** `cqrs/event_bus.py` `_execute()`; deliberate per ADR-046
- **Intend to follow:** **no** — the gateway must not be an `EventBus` handler
- **Reason:** A swallowed broker failure is a silent message loss. The gateway is an independent pump.

**Corrections received:** gateway placed in `message_broker.py` for symmetry; no `OutboxUnitOfWork` ABC; `OutboxStore` trimmed to 4 methods.

---

## Phase 2 — Current state

```
Command → CommandBus → UoW.commit()
    _flush → _collect_and_stamp → _write_outbox(no-op) → _commit
                          ↓
                  collect_events(): list[DomainEvent]
                          ↓
                  EventBus.dispatch_many()  ← terminal, in-process

MessageBroker: protocol defined, start()/stop() wired in bootstrap,
               publish() called from nowhere
```

Outbound delivery today requires hand-writing an `EventHandler` that constructs an `IntegrationEvent` and calls `broker.publish()`. That handler runs after commit, outside the transaction, and — because `EventBus` swallows exceptions — loses messages silently if the broker is down.

## Phase 2 — Desired end state

```
Command → CommandBus → UoW.commit()
    _flush → _collect_and_stamp → _write_outbox()   ← writes outbox rows HERE
             registry.to_entries(domain_events)         (same transaction)
                          ↓
                  collect_events() → EventBus.dispatch_many()   (unchanged)

[separate pump]
OutboundEventGateway.run():
    store.fetch_unpublished(limit)
      → for each row: registry.resolve(topic) → model_validate(payload)
      → broker.publish(topic, event)
      → store.mark_published([message_id])
    on failure → mark_failed + backoff   (row never marked published on error)
```

Invariants:

1. An outbox row exists **only if** the aggregate state that produced it was committed.
2. A row is marked published **only after** the broker accepted it.
3. A translator returning `None` produces no row (internal-only events stay internal).
4. The gateway is domain-ignorant: it knows topics and `IntegrationEvent` classes, never domain types.

---

## Phase 3 — Resolved open questions

| # | Question | Resolution |
|---|---|---|
| 1 | Gateway placement | `infrastructure/message_broker.py` — mirrors `message_subscriber.py` holding a protocol + its gateway |
| 2 | Registry ownership | **One** registry shared by write and read paths — `topic` is derived from the same registration, so two maps could disagree |
| 3 | Loop duplication | Accepted in P1; **measured** in P3. If duplicated, extract a helper both **compose** |
| 4 | ADR-051 handling | **Not superseded.** Its boundary decision stands; only its "fire-and-forget" negative consequence is resolved, noted in ADR-063's Context |

---

## ADR Section — Outbound delivery model

**Status:** Proposed · **Full record:** `docs/adr/ADR-063-outbound-event-gateway-and-outbox.md`

**Context.** pydomain implements the inbound path completely (`MessageSubscriber` → `InboundEventGateway` → `MessageBus`) but the outbound path has a protocol and no caller. ADR-051 records the gap as a known negative: *"The broker does not participate in the Unit of Work — publishing is fire-and-forget."*

**Decision.** The outbox persists serialized `IntegrationEvent`s. Translation from `DomainEvent` happens at **write** time inside `_write_outbox()`, inside the transaction, through a registered **pure** translator. `OutboundEventGateway`, a polling relay, fetches unpublished rows, rehydrates them by topic, publishes via `MessageBroker`, and marks them published.

**Alternatives considered.** (a) Outbox stores `DomainEvent`s and the gateway translates at read time — rejected: the library's own ADR-060 rejects `EventRegistry`-based type resolution for gateways, and read-time translation means rows are re-interpreted by later code, breaking replay stability. (b) Handlers call `broker.publish()` directly from an `EventBus` handler — rejected: post-commit, outside the transaction, and exceptions are swallowed by `EventBus`, producing silent loss. (c) Publish inside `commit()` without an outbox — rejected: makes the commit dependent on broker availability.

**Rationale.** `IntegrationEvent` is primitives-only by its own validator, so `model_dump()` is JSON-safe with no conversion layer, and the outbox row *is* the wire format — replay-stable, and dismissible by a relay that never imports a domain type.

**Consequences.** Rows survive broker outages; at-least-once delivery; the outbox doubles as an audit record of everything published. Cost: a second storage table and a background pump; translation must remain pure (it runs inside a transaction).

---

## Phase 4 — Scope boundaries

**In scope:** `OutboxEntry`, `OutboxStore` port, `OutboundEventRegistry`, `OutboundEventGateway`, `FakeOutboxStore`, bootstrap wiring, ADR-063, Diátaxis docs, exports.

**Out of scope:** `EventRegistry` involvement, `OutboxUnitOfWork` ABC, scheduler/due-item polling, satellite packages, `EventBus` fail-soft change, typed `PublishOptions` (deferred to P3), `docs/diagrams/`.
