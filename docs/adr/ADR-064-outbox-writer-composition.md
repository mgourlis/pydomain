# ADR-064: `OutboxWriter` — Library-Owned Write-Side Join by Composition

## Status

Accepted

## Date

2026-09-29

## Context

ADR-063 introduced the transactional outbox: integration events are translated at **write** time inside `AbstractUnitOfWork._write_outbox()` and persisted atomically with the aggregate state change, then delivered by `OutboundEventGateway`.

That ADR defined the delivery side completely and deliberately left the write side as an extension point. `_write_outbox()` is a documented no-op, and an adopting project supplies the body. Three forces now argue for the library owning that body:

1. **The write side is the only join in the library with no owner.** Every other place where two components meet has a named class: `InboundEventGateway` joins `MessageSubscriber` + registry + `MessageBus`; `SagaManager` joins `SagaRepository` + `SagaRegistry` + `CommandBus`; `SubscriptionRunner` joins `EventStore` + `CheckpointStore`; `OutboundEventGateway` joins `OutboxStore` + `OutboundEventRegistry` + `MessageBroker`. The registry-to-store join has none.

2. **Its behaviour is not uniform, and the edge cases carry real consequences.** A naive implementation — translate, append — gets four situations wrong or undefined:

   - A domain event with no registration must produce no entry (registration *is* the declaration of intent to publish).
   - A translator returning `None` must produce no entry.
   - A translator that **raises** must abort the transaction. Committing aggregate state stripped of its outbound event is silent, permanent data loss.
   - An empty translation result should skip the store call rather than issue a no-op round trip on every command that publishes nothing.

   Hand-rolled implementations will get the first two right by accident and the third by omission.

3. **The failure mode of forgetting the override is invisible.** `bootstrap()` wires `OutboundEventGateway` into the lifecycle. A project that configures the gateway and never overrides `_write_outbox()` gets a relay that polls an empty outbox forever — no exception, no warning, no failing test. The cheaper the correct override is to write, the smaller that trap.

ADR-001 constrains the shape of the answer: *"Forcing it to also inherit from a library ABC creates coupling and MRO complications."* An `OutboxUnitOfWork` base class was already rejected on those grounds in ADR-063, and remains rejected here.

This ADR also corrects a documentation defect: the `_write_outbox()` docstring stated that subclasses write *"stamped **domain events** to an outbox table"*. Under ADR-063 the outbox holds **integration events**. A developer following the old docstring would write domain-event payloads, and every row would dead-letter at the gateway because no topic resolves for it.

## Decision

We will provide `OutboxWriter` in `pydomain.cqrs.outbox` — a composable collaborator, not a base class.

```python
class OutboxWriter:
    def __init__(self, store: OutboxStore, registry: OutboundEventRegistry) -> None: ...

    async def write(self, events: Sequence[DomainEvent]) -> int:
        """Translate *events* and append the resulting entries."""
```

It is wired by composition, with no override required: `AbstractUnitOfWork` accepts an optional writer and implements `_write_outbox()` in terms of it.

```python
class AppUnitOfWork(AbstractUnitOfWork):
    def __init__(self, session: AsyncSession, writer: OutboxWriter) -> None:
        super().__init__(outbox_writer=writer)
        self._session = session
```

Without a writer the hook stays a no-op, so the change is additive and no existing UoW is affected. Subclasses may still override `_write_outbox()` for custom behaviour, and an override can delegate with `await super()._write_outbox()`.

**The contract it owns:**

| Situation | Behaviour |
|---|---|
| Domain event with no registered translation | No entry, silently — internal by default |
| Translator returns `None` | No entry, silently |
| **Translator raises** | **Propagates; the surrounding transaction aborts** |
| Translated result is empty | `append` is not called at all |
| `append` fails | Propagates; the transaction aborts |

`write()` returns the number of entries appended, so callers and tests can observe the outcome without inspecting the store.

The `_write_outbox()` docstring is corrected in the same change to describe integration events and show the delegation.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| `OutboxUnitOfWork` ABC providing `_write_outbox()` | ADR-001: forcing a second library base onto user UoWs reintroduces the coupling and MRO complications that Protocols exist to avoid. Already rejected in ADR-063. |
| Ship nothing; document a three-line inline body | Leaves the write contract ownerless. The translator-failure semantics stay an accident of where the exception happens to land rather than a decision, and the empty-result guard is easy to omit. This is the option ADR-063 implicitly chose, and it has no test surface. |
| Discharge the obligation with a manual `_write_outbox()` override | Leaves the common configuration — writer supplied, nothing customised — as boilerplate that is silently skippable. It also keeps `AbstractUnitOfWork` unaware of a writer the project already holds, so the trap this ADR exists to close survives in the most common case. |
| A `_write_outbox()` method on `OutboundEventGateway` | Wrong lifecycle. The gateway is a separate pump running outside the transaction; the write must happen inside it. Merging them couples delivery to persistence and makes the gateway transaction-aware. |
| An `append_to(store, events)` method on `OutboundEventRegistry` | Couples a pure, I/O-free mapper to a persistence port. The registry's value is that it does one thing and can be tested without a store. |
| A module-level `append_entries(registry, store, events)` function | The library's convention is that a named component with a lifecycle or a contract is a class; free functions are reserved for pure transforms (e.g. `hydrate_command`). A writer has two collaborators and an awaitable contract. |

## Consequences

### Positive

- The write contract is implemented and tested once, in the library, rather than reinvented per project.
- The four edge cases — and especially the translator-failure abort — become explicit, documented, and covered by tests.
- A correct `_write_outbox()` is not an override at all: a configured writer is wired automatically, which removes the silent-empty-outbox trap for the common case.
- The outbound path is now symmetric: `OutboxWriter` owns the write side, `OutboundEventGateway` the delivery side.
- Composition preserves ADR-001: no user UoW gains a second library base class.

### Negative

- One more public concept to learn alongside `OutboxEntry`, `OutboxStore`, `OutboundEventRegistry`, and `OutboundEventGateway`.
- Users can still bypass it — by overriding `_write_outbox()`, or by omitting the writer, which leaves the hook a no-op. The docstring and how-to must actively steer toward it.
- The registry and the writer are both registered at composition time, so bootstrap wiring grows by one object.

### Neutral

- Entirely optional: a system that does not use the outbox is unaffected, and `_write_outbox()` remains a no-op.
- Re-registering a translation continues to overwrite, so the writer picks up hot-swapped versions with no change.

## References

- `src/pydomain/cqrs/outbox.py` — `OutboxWriter`, `OutboundEventRegistry`, `OutboxEntry`, `OutboxStore`
- `src/pydomain/cqrs/unit_of_work.py` — `_write_outbox()` extension point and its corrected docstring
- `tests/cqrs/test_outbox.py` — writer contract tests
- ADR-001: Protocol over ABC for Interfaces
- ADR-005: Publish-After-Commit
- ADR-063: Outbound Event Gateway and Transactional Outbox
