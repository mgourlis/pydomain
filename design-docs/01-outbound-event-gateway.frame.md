# Frame: Outbound Event Gateway and Transactional Outbox

- **Date:** 2026-09-29
- **Alignment:** `design-docs/01-outbound-event-gateway.alignment.md`
- **Research:** `design-docs/01-outbound-event-gateway.research.md`
- **ADR:** `docs/adr/ADR-063-outbound-event-gateway-and-outbox.md`

---

## Phase 1: Tracer Bullet — one event, committed to outbox, published by relay

**Components:**

- `src/pydomain/cqrs/outbox.py` *(new)* — `OutboxEntry` (frozen model), `OutboxStore` (4-method Protocol), `OutboundEventRegistry` (sync `register_translation`, two-way lookup).
- `src/pydomain/infrastructure/message_broker.py` — `OutboundEventGateway` with `run_once()` only, performing a resolve → publish → mark pass.
- `src/pydomain/testing/fake_outbox_store.py` *(new)* — `FakeOutboxStore` with injectable failures.
- `src/pydomain/infrastructure/bootstrap.py` — accept and start the gateway.
- Exports: three `__init__.py` files + `tests/test_package_exports.py` expected lists.

**Testing strategy:**

- Unit: `OutboundEventRegistry.register_translation` — overwrite semantics, `None` translator produces no entry, unregistered domain type produces no entry.
- Unit: `OutboundEventGateway.run_once()` against `FakeOutboxStore` + `InMemoryMessageBroker` — asserts published topic, rehydrated class, and `mark_published` called.
- Integration: command → `FakeUnitOfWork`-style commit → outbox row written → `run_once()` → assert on `InMemoryMessageBroker.published`.

**Verification gate:**

- One command produces exactly one outbox row and exactly one broker publish, with the aggregate state and the row committed together.

**Acceptance criteria:**

- [ ] `OutboxEntry`, `OutboxStore`, `OutboundEventRegistry`, `OutboundEventGateway`, `FakeOutboxStore` exist and are exported.
- [ ] A pure translator registered for one domain type produces one outbox row on commit.
- [ ] A translator returning `None` produces no row.
- [ ] `run_once()` rehydrates by topic and publishes; `mark_published` is called only on success.
- [ ] `make check` green; architecture tests pass.

---

## Phase 2: Durability — at-least-once under failure

**Components:**

- `src/pydomain/infrastructure/message_broker.py` — failure classification: broker error → `mark_failed` + backoff; unknown topic / validation failure → log, **no** `mark_published`.
- `src/pydomain/cqrs/outbox.py` — `event_version` on `OutboxEntry`; document `fetch_unpublished` atomicity and abandoned-row reclamation on the port.
- `src/pydomain/cqrs/integration_events.py` — add `event_version: int = 1`.
- `src/pydomain/testing/fake_outbox_store.py` — reclaim-after-timeout simulation.

**Testing strategy:**

- Property: for any exception raised between `fetch_unpublished` and `mark_published`, the row is still delivered on a later pass (at-least-once).
- Broker raises → row not marked published → re-fetched and re-published on the next pass.
- Unknown topic → row remains unpublished and consumes one attempt from the shared retry budget.
- Retry budget exhausted → terminal `mark_dead_lettered`; never fetched again, logged at `ERROR`.

**Verification gate:**

- Killing the gateway between publish and mark produces a duplicate delivery, never a lost one.

**Acceptance criteria:**

- [ ] Broker failure leaves the row unpublished and increments attempts.
- [ ] Unknown topic and validation failure never mark a row published.
- [ ] `event_version` is persisted and surfaced on rehydration.
- [ ] At-least-once property test passes.
- [ ] An entry that exhausts `max_attempts` is dead-lettered, never re-fetched, never reported as published.

---

## Phase 3: Hardening — loop lifecycle, ordering, and duplication measurement

**Components:**

- `src/pydomain/infrastructure/message_broker.py` — `run()`, `stop()` with graceful drain, `poll_interval_seconds`, `failure_backoff_seconds`, `batch_size`.
- `src/pydomain/infrastructure/bootstrap.py` — start after `broker.start()`; `Application.shutdown()` drains before `broker.stop()`.
- **Measurement:** compare `OutboundEventGateway`'s loop against `SubscriptionRunner` (`src/pydomain/infrastructure/subscription.py`). If near-identical blocks exceed 3 lines, extract a helper that both **compose** — do not change `SubscriptionRunner`'s public shape (ADR-048, ADR-049).
- Deferred decision: typed `PublishOptions` replacing `**kwargs: Any` on `MessageBroker.publish`.

**Testing strategy:**

- Lifecycle: `run()` exits when `stop()` is called; in-flight batch completes; no row claimed after stop.
- Ordering: rows for the same topic are published in insertion order.
- Shutdown ordering test: broker is not stopped before the gateway drains.

**Verification gate:**

- A graceful shutdown during active work loses no claimed row.

**Acceptance criteria:**

- [ ] `run()`/`stop()` drain correctly under load.
- [ ] Per-topic ordering holds.
- [ ] Duplication against `SubscriptionRunner` is measured and the extract/keep decision is recorded in the ADR's Consequences or a follow-up note.

---

## Phase 4: Documentation

Ordered per `documentation-writer`'s sequencer: ADR → Diátaxis → diagram → lint.

**Components:**

- `docs/diataxis/concepts/infrastructure/outbound-event-gateway.md` — Explanation page. Follow the header convention of `inbound-event-gateway.md` (adoption level + module).
- `docs/diataxis/how-to/infrastructure/configure-outbound-event-gateway.md` — How-To. Includes the two-line `_write_outbox()` recipe.
- Register both in their `_index.md`; update `api-reference/_index.md`.
- Trace and update existing pages: `concepts/cqrs/unit-of-work.md` (documents `_write_outbox`), `concepts/infrastructure/message-broker.md`, `concepts/cqrs/integration-events.md` — cross-links and "Next steps".
- Inline Mermaid diagram in the concept page (do not create `docs/diagrams/`).
- ADR-063 status → `Accepted`.
- ADR-065 (retry budget + dead letter) once the policy is final.

**Testing strategy:**

- `python .claude/skills/diataxis-writer/scripts/lint_docs.py --docs-dir docs`; fix order: broken links → missing `_index.md` → empty sections → orphans → mode warnings.
- Manual: concept/how-to sync, valid ADR links.

**Verification gate:**

- Lint passes and every new page is reachable from an `_index.md`.

**Acceptance criteria:**

- [ ] Concept and how-to pages exist, in the correct mode, one mode per page.
- [ ] All three `_index.md` files updated.
- [ ] Existing pages cross-linked.
- [ ] Lint clean; ADR-063 `Accepted`.

---

## Learning Tests

- **`MessageBroker.publish` `**kwargs` honouring** — before relying on `headers` propagation in P2, verify what the concrete broker adapter must do with unrecognised keyword arguments. The docstring says implementations should *"extract and propagate recognised keyword arguments, ignoring those they do not support"* — confirm `headers` is the only key P1 needs.

---

## Phase Sequence

```
Phase 1 (tracer bullet, no deps)
    ↓
Phase 2 (depends on 1)
    ↓
Phase 3 (depends on 2)   ──┐
Phase 4 (parallel with 3, depends on 1)  ──┘  docs can start once the API is frozen at end of P1
```

---

## Scope Boundaries

**In scope:** `OutboxEntry`, `OutboxStore`, `OutboundEventRegistry`, `OutboundEventGateway`, `FakeOutboxStore`, bootstrap wiring, `event_version`, ADR-063, Diátaxis pages, exports.

**Out of scope:** `EventRegistry` involvement; `OutboxUnitOfWork` ABC; scheduler / due-item polling; satellite packages (`pydomain-sqlalchemy` etc.); `EventBus` fail-soft change; typed `PublishOptions` (deferred decision, P3); `docs/diagrams/`; `C901` complexity enforcement.

---

## P3 Measurement — Loop duplication vs `SubscriptionRunner`

**Measured.** The shared control flow between `OutboundEventGateway.run()` and `SubscriptionRunner.run()` is:

```
while not <stop flag>:
    <one pass>
    if <nothing done> and not <stop flag>:
        sleep(<interval>)
```

That is four lines of scaffolding. Everything else differs: the pass body (outbox fetch/publish vs checkpoint read/stream dispatch), failure handling (`SubscriptionRunner` isolates per-subscription inside `_process_cycle` and sleeps a fixed backoff; the gateway wraps the whole pass in catch-log-backoff), and shutdown (`SubscriptionRunner.stop()` is a synchronous flag setter; the gateway's `stop()` is async and drains the in-flight pass).

**Decision: keep the duplication.** A shared helper would need a parameter selecting between the two failure-handling behaviours — a flag-driven abstraction that is harder to read than four lines of loop. Extracting would also change a published, ADR-covered class (`SubscriptionRunner`, ADR-048/049). `python-refactoring-expert`'s DRY target is a general guideline, not a rule that outranks readability. Recorded here so the metric is not silently ignored; revisit if a third polling loop appears.

## Phase status

| Phase | Status |
|---|---|
| P1 Tracer bullet | ✅ complete |
| P2 Durability (attempts, backoff, `event_version`, retry budget + dead letter) | ✅ complete |
| P3 Hardening (loop lifecycle, backoff, duplication measurement) | ✅ complete — except typed `PublishOptions` (deferred decision) and per-topic ordering (not claimed) |
| P4 Documentation (concept + how-to + indexes + lint) | ✅ complete — recipes deferred |

**Post-review amendment.** `AbstractUnitOfWork` now accepts an optional `outbox_writer` and implements `_write_outbox()` in terms of it, so a configured outbox needs **no override**. The `OutboxWriter`-as-collaborator decision (ADR-064) was refined after review: the original plan treated the write seam as a one-line override the user supplies, which left the silent-empty-outbox trap intact in the most common configuration. P1's "wire the writer by composition" step is now a constructor argument. The hook remains overridable, and an override can delegate via `await super()._write_outbox()`.

**Post-review amendment — retry budget and dead letter (ADR-065).** ADR-063 fixed the retry policy as *unbounded*: every failure requeued at capped backoff, never dead-lettered, so a later deploy could still deliver a row whose translation was missing. P2 initially implemented that literally. Review found three problems: unbounded retry has **no terminal state** (nothing to alert on, and "will succeed next pass" is indistinguishable from "never"); a permanently-broken row consumes a `batch_size` slot on **every** pass, starving newer rows; and the recoverable/terminal distinction the policy relies on is not derivable from the failure, because the exception type is a property of the adapter. The delivered policy bounds retry with `max_attempts` (default `10`, `None` = forever), adds `OutboxStore.mark_dead_lettered` as a fifth port method, and keeps the budget **uniform** across failure kinds rather than classifying them. This is a narrowing of ADR-063's decision, not a reversal of P2: unconditional *permanent* dead-lettering stays rejected, and the entry remains replayable after a fix. `mark_dead_lettered` makes `OutboxStore` the largest storage port in the library (5 methods); accepted because dead-lettering is a genuine second terminal outcome, and the alternative is two ports for one feature.

**Not delivered:** `design-docs/01-….tasks.md` and the `DCE` issue breakdown (`task-creation`), because no YouTrack issue exists and no tracker integration is available in this session.
