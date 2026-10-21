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
   events are lost without an error. `how-to/infrastructure/configure-inbound-event-gateway.md` teaches
   exactly this call.
2. **The `Application` has no public `message_bus` or `event_registry`** (INF-17, A26). ADR-047 says
   handlers are registered on the bus after bootstrap, and 69 lines on 15 Diataxis pages call
   `app.message_bus` or `app.event_registry`, but neither attribute exists: both raise `AttributeError`.
3. **A start that fails part-way leaks what it started** (INF-06). `bootstrap()` starts the inbound
   gateways, then the broker, then the outbound gateway. When a later start raises, the earlier parts keep
   running, and the caller gets no `Application` on which to call `shutdown()`.
4. **One failing stop skips the rest of the shutdown** (INF-07). `Application.shutdown()` stops each part in
   turn without isolation, so a gateway whose `stop()` raises leaves the later gateways, the outbound drain
   and the broker running. Shutdown had no tests.

The order in which consumers start relative to handler registration (INF-05) is not part of this decision;
it belongs to the application lifecycle cluster (W2-4), which supersedes the ordering in ADR-047.

## Decision

We will make `bootstrap()` refuse an inconsistent wiring, expose what it wired, and clean up after itself.
This amends ADR-047 on its public surface and its lifecycle guarantees.

1. **`Application` exposes read-only `message_bus` and `event_registry` properties**, returning the objects
   it was built with, next to the existing `snapshot_store`, `message_broker`, `inbound_gateways` and
   `outbound_gateway` properties. No existing attribute is renamed or removed.
2. **`bootstrap()` raises when an inbound gateway's bus is not the application's bus.**
   _Open: the exception type, when it is detected, and what counts as a mismatch when `message_bus=` is
   omitted (see the decision log once answered)._
3. **A start that fails part-way stops every part that had started, then re-raises.**
   _Open: the order of the clean-up stops and what happens when one of them raises too._
4. **`Application.shutdown()` runs every stop even when an earlier one raises.** The order stays ADR-047's:
   inbound gateways, then the outbound gateway, then the broker.
   _Open: what `shutdown()` does with the errors it collected._

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Fix the docs instead of adding the accessors (INF-17, "or fix docs") | ADR-047 already tells users to register handlers on the bus after bootstrap; without an accessor they must keep their own reference, and 15 pages would need a second variable |
| Leave the wiring check to the user | The failure is silent: events are dropped with no error and no log, so users cannot find it |

## Consequences

### Positive

- An application whose inbound events would be lost fails at bootstrap, not in production.
- The docs' `app.message_bus.register_*` and `app.event_registry.register` calls work as written.
- A failed bootstrap or shutdown no longer leaves consumers or the outbox relay running.

### Negative

- Code that calls `bootstrap(inbound_gateways=[...])` without passing the gateway's bus now fails at
  startup instead of losing events silently.

### Neutral

- Start order and auto-start (INF-05) are unchanged; W2-4 decides them.

## References

- `src/pydomain/infrastructure/bootstrap.py` — `bootstrap()`, `Application`
- `src/pydomain/infrastructure/message_subscriber.py` — `InboundEventGateway`
- `docs/others/bug-fix-plan.md` §8 W1-6; `docs/others/bug-audit-plan.md` rows INF-06, INF-07, INF-16,
  INF-17, A26, A27
- arc42 §9.24
