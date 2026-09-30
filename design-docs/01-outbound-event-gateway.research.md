# Research: Outbound Event Flow

- **Date:** 2026-09-29
- **Scope:** How outbound (domain → broker) event flow works in pydomain today; what exists, what is missing, and which conventions constrain a fix.
- **Method:** Direct source reading at branch `feature/outbound-event-gateway` (parent `origin/dev`, base tag `v0.2.3`) plus ADR review.

---

## Findings

### F1 — There is no outbound pipeline

The `MessageBroker` protocol exists in `src/pydomain/infrastructure/message_broker.py`:

```python
async def publish(self, topic: str, event: IntegrationEvent, **kwargs: Any) -> None
async def start(self) -> None
async def stop(self) -> None
```

But a search for `\.publish\(` across `src/pydomain/` returns **only docstring references** in `src/pydomain/cqrs/integration_events.py` (lines 38, 80). There is no call site anywhere in the library.

`_write_outbox()` in `src/pydomain/cqrs/unit_of_work.py` is documented as an extension point and is a no-op:

> "Extension point for outbox writes in state-based CQRS. Default no-op. Subclasses can access `self._events` to write stamped domain events to an outbox table within the same transaction."

It is **overridden nowhere** in `src/` or `src/pydomain/testing/`.

`bootstrap()` in `src/pydomain/infrastructure/bootstrap.py` accepts `message_broker` and calls only `start()` and `stop()`. The broker is lifecycle-managed but not connected to the event flow.

### F2 — The implemented path terminates at the in-process EventBus

```
MessageBus.dispatch(command)
 └─ CommandBus.dispatch()
     ├─ uow.commit() → _flush → _collect_and_stamp → _write_outbox(no-op) → _commit
     └─ return (result, uow.collect_events())      # list[DomainEvent]
 └─ EventBus.dispatch_many(events)                  # in-process, terminal
```

`MessageBus.dispatch()` is the only consumer of collected events.

### F3 — UoW carries DomainEvents; broker carries IntegrationEvents

| Component | Type | Transport |
|---|---|---|
| UoW (`_events`, `collect_events()`) | `DomainEvent` | in-process |
| `MessageBroker` / `MessageSubscriber` / `InboundEventGateway` | `IntegrationEvent` | external |

`_collect_and_stamp()` pulls from repositories via `pull_events()` and applies `DomainEvent.stamp()`. The two worlds meet only through explicit translator functions.

### F4 — Inbound is a complete, symmetric reference implementation

`InboundEventGateway` in `src/pydomain/infrastructure/message_subscriber.py`:

- `register_translation[T: IntegrationEvent](topic, integration_class, translator: Callable[[T], DomainEvent])` — **synchronous**, performs no I/O
- `self._registry: dict[str, tuple[type[IntegrationEvent], Callable]]`
- Pipeline: lookup → hydrate (`model_validate`) → translate (ACL) → dispatch
- Documented ACK/NACK convention table mapping failure mode → behaviour → recovery
- Re-registration **overwrites**, enabling hot-swap of event versions without restart

### F5 — ADR-060 explicitly rejects `EventRegistry` for gateways

`docs/adr/ADR-060-inbound-event-gateway.md`, Alternatives Considered:

> **EventRegistry for type resolution** — "Introduces infrastructure with no benefit — type resolution via `model_validate` is immediate and unambiguous when the topic implies the type."

And under Decision: *"**Flat payload pattern**: The gateway uses no `EventRegistry` or envelope wrapper. The type is implied by the topic."*

This is decisive for the outbound design: type resolution comes from the topic, not from a registry of serialized payloads.

### F6 — `EventBus` swallows handler exceptions

`src/pydomain/cqrs/event_bus.py`, `_execute()`:

```python
try:
    await pipeline.execute(ctx, event)
except Exception:
    logger.exception("Event handler %s failed for %s", ...)
```

Correct for in-process reactions (ADR-046), but it means outbound publishing implemented as an `EventHandler` would silently drop messages on broker failure. The outbound pump must **not** be an `EventBus` handler.

### F7 — `SubscriptionRunner` is the existing durable-loop template

`src/pydomain/infrastructure/subscription.py` provides:

- `run()` — polling loop with `poll_interval_seconds`
- `run_once()` — single pass
- `stop()` — graceful ("current batch completes before the loop exits")
- `failure_backoff_seconds` with retry-on-exception
- At-least-once: "If `process_batch` raises, the checkpoint is **not** updated"

It is an ABC with abstract `process_batch`, sourced from `EventStore` + `Subscription` — not directly reusable for a broker relay, but the loop discipline transfers.

### F8 — Store protocol conventions

| Protocol | Methods | Location |
|---|---|---|
| `CheckpointStore` | 2 (`load`, `save`) | `es/checkpoint_store.py` |
| `SnapshotStore` | 2 (`save`, `get`) | `es/snapshot.py` |
| `EventStore` | 3 (`append_to_stream`, `read_stream`, `read_all`) | `es/event_store.py` |

All are `@runtime_checkable` Protocols with NumPy-style docstrings documenting return semantics (e.g. "or `0` if none saved"). `ProcessedMessageStore` documents concurrency on the **implementation**: *"Implementations should provide atomic check-and-set semantics to prevent races."*

### F9 — Public exports are test-enforced

`tests/test_package_exports.py` asserts both "has all expected names" and "has no extra names" per module, with a failure message instructing the developer to update the `EXPECTED_*` list.

### F10 — ADR practice

- ADR-001 … ADR-052 carry `Date: Retroactive — documented from existing implementation.`
- ADR-053 onward carry real dates (ADR-060: `Status: Accepted`, `Date: 2026-05-22`)
- Highest existing ADR = **062**; next free = **063**

### F11 — Layer charter is documented

`src/pydomain/infrastructure/__init__.py`:

> "Interfaces and abstract base classes live in the CQRS layer as Clean Architecture ports."

So outbound **ports** belong in `cqrs/`; **implementations** in `infrastructure/`.

---

## Constraints

| Constraint | Source |
|---|---|
| `cqrs/` must not import `infrastructure/` | `tests/test_architecture.py` (pytest-archon) |
| PEP 695 generics for parameterised registration | `InboundEventGateway.register_translation[T: IntegrationEvent]` |
| Strict mypy: `disallow_untyped_defs`, `warn_return_any` | `pyproject.toml` |
| No forced extra base classes | ADR-001 ("MRO complications") |
| `**kwargs: Any` on `publish()` is untyped | `message_broker.py` |

---

## Open Questions

1. **Gateway placement** — `message_broker.py` (symmetry with `message_subscriber.py`) or its own module?
2. **Registry ownership** — one registry shared by write and read paths, or two independent maps?
3. **Loop duplication** — accept `SubscriptionRunner`'s loop duplication, or extract a shared helper?
4. **ADR-051 handling** — supersede, or note the resolved consequence?
