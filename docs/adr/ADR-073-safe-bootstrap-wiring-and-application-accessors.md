# ADR-073: Safe Bootstrap Wiring and the Application's Public Accessors

## Status

Proposed. Amends [ADR-047](ADR-047-bootstrap-composition-root.md).

## Date

2026-10-21

## Context

ADR-047 makes `bootstrap()` the composition root: it builds the `Application`, starts the infrastructure it
is given, and leaves handler registration to the caller ("`bootstrap()` creates the bus; handlers are
registered after bootstrap"). The 0.3.0 audit (`docs/others/bug-audit-findings.md`,
`docs/others/bug-audit-plan.md` §12) found four places where the wiring it produces is unsafe or unusable:

1. **An inbound gateway can be wired to a bus the application does not use** (INF-16, A27).
   `bootstrap(inbound_gateways=[gw])` without `message_bus=` builds a new `MessageBus`, which is not the
   bus the gateway dispatches into. Handlers registered on the application's bus never run, and inbound
   events are lost without an error. `how-to/infrastructure/configure-inbound-event-gateway.md` taught
   exactly this call, and used `app.message_bus` to build the gateway before `app` existed.
2. **The `Application` has no public `message_bus` or `event_registry`** (INF-17, A26). ADR-047 says
   handlers are registered on the bus after bootstrap, and 69 lines on 15 Diataxis pages call
   `app.message_bus` or `app.event_registry`, but neither attribute existed: both raised `AttributeError`.
3. **A start that fails part-way leaks what it started** (INF-06). `bootstrap()` starts the inbound
   gateways, then the broker, then the outbound gateway. When a later start raised, the earlier parts kept
   running, and the caller got no `Application` on which to call `shutdown()`.
4. **One failing stop skips the rest of the shutdown** (INF-07). `Application.shutdown()` stopped each part
   in turn without isolation, so a gateway whose `stop()` raised left the later gateways, the outbound
   drain and the broker running. Shutdown had no tests.

The order in which consumers start relative to handler registration (INF-05) is not part of this decision;
it belongs to the application lifecycle cluster (W2-4), which supersedes the ordering in ADR-047.

## Decision

We will make `bootstrap()` refuse an inconsistent wiring, expose what it wired, and clean up after itself.
This amends ADR-047 on its public surface and its lifecycle guarantees. The author decided each open point
on 2026-10-21 ([decision log](../others/decision-log.md#12-library-behaviour) B-22 to B-28, and B-40).

1. **`Application` exposes read-only `message_bus` and `event_registry` properties**, returning the objects
   it was built with, next to the existing `snapshot_store`, `message_broker`, `inbound_gateways` and
   `outbound_gateway` properties. No existing attribute is renamed or removed. `Application.__init__`
   creates an empty `EventRegistry` when given `None`, as `bootstrap()` already did, so `event_registry`
   is typed `EventRegistry`, never `None` (B-28).
2. **`InboundEventGateway` exposes its bus** as a read-only `message_bus` property (B-25).
3. **`bootstrap()` raises `ValueError` when an inbound gateway's bus is not the application's bus**
   (B-22). The check runs at the top of `bootstrap()`, before anything starts, so there is nothing to clean
   up (B-23). A mismatch is any gateway whose `message_bus` is not the application's bus; omitting
   `message_bus=` while passing gateways is therefore always a mismatch, because `bootstrap()` would build a
   new bus (B-24). The message names the gateway's position and type, e.g. `inbound_gateways[1]
   (InboundEventGateway) dispatches into a different MessageBus than the application's`, and no payload.
4. **`Application.shutdown()` runs every stop** in ADR-047's order: the inbound gateways, then the outbound
   gateway, then the broker (B-26). A stop that raises an `Exception` does not skip the later stops. After
   the last stop, the first error is re-raised; each later failure adds a note to it,
   `clean-up also failed: <type>`, naming the exception type only, never its message, which may carry
   content (the same rule as ADR-068's clean-up note). A `BaseException` that is not an `Exception`, such
   as `asyncio.CancelledError`, propagates at once and skips the remaining stops.
5. **A start that fails part-way stops the parts that started, then re-raises the start error** (B-27).
   The parts are stopped in shutdown order; the part whose `start()` raised is not stopped. A clean-up stop
   that raises adds the same type-only note to the start error; a cancelled clean-up stop propagates at
   once.
6. **A `bootstrap()` cancelled while starting cleans up too, then re-raises the cancellation** (B-40).
   When a `start()` is cancelled (`asyncio.CancelledError`), the parts started before it are stopped by
   the same rules as point 5, in shutdown order, without the part whose `start()` was cancelled, and with
   a type-only note on the cancellation for each failing clean-up stop. The clean-up runs inside
   `asyncio.shield`, so the cancellation that interrupted the start does not interrupt it; anyio is not a
   runtime dependency, so its shielded cancel scopes are not used. A second cancellation while the
   clean-up runs stops `bootstrap()` from waiting for it, but the shielded clean-up task keeps running to
   the end. `shutdown()` keeps point 4: a cancellation there propagates at once.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Fix the docs instead of adding the accessors (INF-17, "or fix docs") | ADR-047 already tells users to register handlers on the bus after bootstrap; without an accessor they must keep their own reference, and 15 pages would need a second variable |
| Leave the wiring check to the user | The failure is silent: events are dropped with no error and no log, so users cannot find it |
| A new `BootstrapError` or `WiringError` for the mismatch (B-22) | Rejected by the author. It adds a public class with no infrastructure error root to sit under while W3-1 re-roots `CQRSError`; infrastructure already reports bad arguments with `ValueError` |
| `TypeError` for the mismatch (B-22) | Rejected by the author: the types are right, the objects are wrong |
| Check each gateway's bus when it starts (B-23) | Rejected by the author: the earlier gateways would already be consuming, and the check would go through the partial-start clean-up |
| Adopt the gateways' common bus when `message_bus=` is omitted (B-24) | Rejected by the author: implicit, and an application whose gateways disagree would still need the check |
| Read the gateway's private `_message_bus` from `bootstrap.py` (B-25) | Rejected by the author: couples the composition root to another class's private state |
| Raise an `ExceptionGroup` of every stop error (B-26, B-27) | Rejected by the author: `except RuntimeError` would stop matching a single failure, and the shape differs from ADR-068's clean-up note |
| Log stop errors and never raise (B-26) | Rejected by the author: a failed shutdown would look like a clean one |
| Clean up in reverse start order (B-27) | Rejected by the author: shutdown order already stops the outbound gateway before the broker it publishes through, and reusing it keeps one order to reason about |
| Let a failing clean-up stop's error replace the start error (B-27) | Rejected by the author: the caller would see the secondary failure, not why the start failed |
| Let a cancellation of `bootstrap()` propagate at once, without clean-up (B-40) | Rejected by the author: the consumers already started would keep running, with no `Application` to stop them |
| Also stop the part whose `start()` raised (B-27) | Rejected by the author: its state is unknown, and a stop on a part that never started may raise |
| Keep `Application.event_registry` typed `EventRegistry \| None` (B-28) | Rejected by the author: every caller of `app.event_registry.register(...)` would need a `None` check under `mypy --strict` |

## Consequences

### Positive

- An application whose inbound events would be lost fails at bootstrap, not in production.
- The docs' `app.message_bus.register_*` and `app.event_registry.register` calls work as written.
- A failed bootstrap or shutdown no longer leaves consumers, the outbox relay or the broker running.

### Negative

- Code that calls `bootstrap(inbound_gateways=[...])` without passing the gateways' bus as `message_bus=`
  now fails at startup with `ValueError`. Tests that built a gateway on a throwaway bus must pass that bus.
- Later stop errors appear only as notes on the first error, by type; their messages are not kept.
- A stop that hangs still blocks `shutdown()`: there is no timeout (INF-07 notes this; not decided here).
- A clean-up stop that hangs after a cancelled start blocks `bootstrap()` until a second cancellation;
  the shielded clean-up task then keeps running in the background. There is no timeout.
- Under a structured-concurrency runner that cancels again at every await (anyio cancel scopes), the
  second cancellation comes at once: `bootstrap()` re-raises without waiting, and the clean-up finishes
  as a background task.

### Neutral

- Start order and auto-start (INF-05) are unchanged; W2-4 decides them.
- `Application(message_bus=bus)` built directly now has an empty `EventRegistry` instead of `None`.
- `shutdown()` called twice stops every part twice; idempotency is left to each part.

## References

- `src/pydomain/infrastructure/bootstrap.py` — `bootstrap()`, `Application`
- `src/pydomain/infrastructure/message_subscriber.py` — `InboundEventGateway.message_bus`
- `tests/infrastructure/test_bootstrap.py` — wiring check, partial start, shutdown and accessor tests
- [ADR-068](ADR-068-rollback-drains-event-buffers.md) — the type-only clean-up note this ADR reuses
- [Decision log](../others/decision-log.md#12-library-behaviour) B-22 to B-28 and B-40
- `docs/others/bug-fix-plan.md` §8 W1-6; `docs/others/bug-audit-plan.md` rows INF-06, INF-07, INF-16,
  INF-17, A26, A27
- arc42 [§9.24](../arch42/09-design-decisions.md#924-safe-bootstrap-wiring-and-the-applications-public-accessors)
