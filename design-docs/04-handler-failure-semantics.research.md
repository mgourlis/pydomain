# Research: Handler Failure Semantics

- **Date:** 2026-10-23
- **Issue:** DCE-151 (Feature DCE-132, fix plan cluster W2-3)
- **Scope:** What happens to a failing handler's exception on every path that runs user handlers: `EventBus`
  (in-process and inbound), the `InboundEventGateway` and the subscriber behind it, the `SagaManager` and the
  `SubscriptionRunner`; what the subscriber ACKs or NACKs; every failure log line ADR-071 left to this cluster
  and what it contains; how `EventIdempotencyBehavior` (ADR-080) interacts with a failure; the rest of rows
  CQRS-18, CQRS-20 and FAKE-09; what the ADRs and docs promise.
- **Method:** Source reading at `origin/dev` (d4828c0, W2-1 and W2-2 merged), the audit probes in
  `tests/support/probes/`, and two scratch scripts run against this tree (not committed; their output is quoted
  below). Facts only: no options or recommendations.
- **Paths:** source paths are relative to `src/pydomain/`; `l.N` is a line number.
- **Outcome:** pending (the author's decisions, then ADR-081 and `04-handler-failure-semantics.frame.md`).

The rows in scope are INF-01, CQRS-18, CQRS-27, A1, FAKE-09 (rest) and CQRS-20 (rest). Their audit entries:
`docs/others/bug-audit-findings.md` l.127, 200, 214, 215, 265; `docs/others/bug-audit-plan.md` l.375, 377, 384,
552, 596, 630. Decision log P-31 (l.56) assigned FAKE-09 (rest) and CQRS-20 (its cancel and formatter parts) to
this cluster; B-31, B-33 and B-44 (l.107, 109, 120) left it the handler-failure logs; B-153 (l.229) constrains
it; B-177 (l.253) left it one docs passage; backlog F-9 (l.279) is to be decided again here.

---

## 1. How a handler failure travels today

### Files

- `cqrs/event_bus.py`: `dispatch` (l.78-86), `dispatch_many` (l.88-100), `_execute` (l.106-125).
- `infrastructure/message_bus.py`: `dispatch` (l.162-204); the event branch (l.193-195), the command branch
  (l.196-199).
- `infrastructure/message_subscriber.py`: the module docstring's ACK/NACK section (l.11-25), `MessageSubscriber`
  (l.94-129), `register_translation` (l.191-221), `_process_message` (l.231-344).
- `infrastructure/bootstrap.py`: `Application.dispatch` (l.125) forwards to `MessageBus.dispatch`.

### Findings

**F1.1 `EventBus` swallows every `Exception`.** `_execute` runs each registered pipeline in its own `try`, and an
`Exception` is logged with `logger.exception("Event handler %s failed for %s", <handler __name__ or
str(pipeline)>, <event type>)` on `pydomain.event_bus` and dropped (l.118-125). `dispatch` and `dispatch_many`
return `None` whatever the handlers did. The handler name falls back to `str(pipeline)`, the default `object`
repr of a `MessagePipeline`, but `ctx.handler` is set by `MessagePipeline._run` before the chain runs
(`cqrs/behaviors.py` l.141-148), so the fallback applies only to a handler without `__name__` (a callable
instance or a `functools.partial`) — it then names the pipeline object, not the handler.

**F1.2 Handlers run in registration order; a cancellation stops the rest.** `_execute` catches `Exception` only,
so a `CancelledError` (a `BaseException`) leaves `dispatch` at once and the later handlers do not run (S8 below).
A `KeyboardInterrupt` or `SystemExit` does the same.

**F1.3 Two callers, one code path.** `MessageBus.dispatch(DomainEvent)` calls `EventBus.dispatch` (l.193-195),
the path the gateway uses. `MessageBus.dispatch(Command)` calls `CommandBus.dispatch`, then
`EventBus.dispatch_many(events)` with the committed events (l.196-199): an event handler that fails after a
committed command does not change the command's result (S9). Both paths share `_execute`, so nothing in
`EventBus` today knows which caller it serves.

**F1.4 The gateway's four outcomes.** `_process_message`:
1. unknown topic: WARNING `No translation registered for topic '%s' — discarding message`, returns (l.254-259);
2. validation failure: ERROR with `describe(exc)` and no traceback (ADR-071), returns (l.264-273);
3. translator raises any `Exception`: `logger.exception("Translation failed for %s on topic '%s' — discarding",
   …)`, returns (l.276-284);
4. `MessageBus.dispatch` raises any `Exception`: `logger.exception("Dispatch failed for %s (from %s) on '%s' —
   propagating", …)`, then `raise` (l.334-344).

The id derivation (l.290-304) and the tracing stamp (l.313-332) run between 3 and 4 and log WARNINGs only.
"Returns" means the subscriber's handler returns normally; "raise" means it raises. The module docstring
(l.11-25) tells subscriber implementations to ACK the first and NACK the second.

**F1.5 The NACK path is unreachable through a real `MessageBus`** (INF-01). Because of F1.1, step 4 never sees an
exception from a handler. The re-raise works only when `MessageBus.dispatch` itself raises, which a real bus does
for an event only on a `BaseException` from a handler (F1.2) — a `CancelledError` is not an `Exception`, so the
gateway's `except Exception` does not log it either and the subscriber sees the cancellation. The existing
gateway tests reach step 4 by monkeypatching `bus.dispatch` (`tests/infrastructure/test_inbound_event_gateway.py`
l.276-296, l.713-737).

**F1.6 The subscriber decides.** The `MessageSubscriber` Protocol is `subscribe(topic, handler)`, `start()`,
`stop()` (l.94-129); the handler returns `None`. ADR-059 l.69-73 leaves ACK/NACK to the implementation; the how-to
examples ACK on return and NACK on raise (`how-to/infrastructure/configure-message-subscriber.md` l.95-104,
l.140-147).

### Scratch evidence: `outcomes.py`

A subscriber double that conforms to the Protocol, appends handlers per topic and records `ACK` on return and
`NACK(<type>)` on raise, driven through a real `MessageBus` and `InboundEventGateway` (as
`tests/support/probes/inf/inbound_probe.py` does):

```
== S1 failing handler, real MessageBus ==
calls: 1 outcomes: ['ACK']
  log> pydomain.event_bus:ERROR:Event handler failing failed for Shipped +exc_info=RuntimeError
MessageBus.dispatch(event) raised: no
== S2 two handlers, first fails ==
ran: ['a', 'b'] outcomes: ['ACK']
  log> pydomain.event_bus:ERROR:Event handler a failed for Shipped +exc_info=RuntimeError
== S6 translator raises ==
outcomes: ['ACK']
  log> pydomain.message_subscriber:ERROR:Translation failed for ShipmentIE on topic 't' — discarding +exc_info=ValueError
== S7 gateway re-raise path, a bus whose dispatch raises (what the NACK path does) ==
outcomes: ['NACK(RuntimeError)']
  log> pydomain.message_subscriber:ERROR:Dispatch failed for Shipped (from ShipmentIE) on 't' — propagating +exc_info=RuntimeError
== S8 handler cancelled ==
CancelledError propagated; ran: ['a']
== S9 post-command dispatch, event handler fails ==
command result returned: EmptyCommandResult
  log> pydomain.event_bus:ERROR:Event handler failing failed for Placed +exc_info=RuntimeError
== S10 InMemoryMessageSubscriber with a failing handler ==
simulate_message raised: no
  log> pydomain.event_bus:ERROR:Event handler failing failed for Shipped +exc_info=RuntimeError
```

In S7 the gateway's error log and the subscriber's NACK both happen; with a real bus (S1) the only log is
`EventBus`'s, and the message is ACKed.

---

## 2. Failures and event idempotency (ADR-080)

### Files

- `cqrs/behaviors.py`: `EventIdempotencyBehavior.handle` (l.816-845), `_namespace_for` (l.847-859),
  `_mark_after_success` (l.861-869), `_record` (l.882-891); `_reserve` and `_release` (l.633-673).
- `cqrs/exceptions.py`: `EventInProgressError` (l.95-109), `EventIdempotencyMisuseError` (l.112-125).

### Findings

**F2.1 A failed handler is released and re-raised unchanged.** With a reservation store, an exception from
`next()` releases the token under the shield, then re-raises the same object (l.834-843); a failing `release`
adds the note `clean-up also failed: <Type>` (ADR-080 point 6). Without reservations, nothing was marked, and the
error passes through (l.861-869). Either way, the exception that reaches `EventBus._execute` is the handler's
own, after the release: the order ADR-080 point 6 asks W2-3 to keep.

**F2.2 `EventInProgressError` is raised before the handler runs** (l.825-833), and `EventBus` logs it like a
handler failure (S4). Its docstring says "Until W2-3, `EventBus` logs it at ERROR like any handler failure;
afterwards the inbound gateway NACKs it" (`exceptions.py` l.102-104).

**F2.3 `EventIdempotencyMisuseError` is raised at the first dispatch to a second handler, before any store call**
(l.851-856); that handler does not run. Its docstring says "Until W2-3, `EventBus` logs it at ERROR like any
handler failure" (`exceptions.py` l.121-122) and says nothing about the gateway. It is a configuration error: it
raises on every dispatch of that event to that handler.

**F2.4 A failure in the mark after success is not a handler failure.** `_record` turns an `Exception` from
`set` or `check_and_set` into a type-only WARNING and returns normally (l.882-891, B-164, B-165), so the dispatch
succeeds. B-165 rejected raising it because "after W2-3 the gateway would NACK and redeliver an event whose
handler already succeeded".

### Scratch evidence: `outcomes.py` S3 to S5

```
== S3 idempotent failing handler, one payload delivered twice (W2-2 review probe) ==
calls: 2 same event_id: True outcomes: ['ACK', 'ACK']
  log> pydomain.event_bus:ERROR:Event handler failing failed for Shipped +exc_info=RuntimeError
  log> pydomain.event_bus:ERROR:Event handler failing failed for Shipped +exc_info=RuntimeError
== S4 concurrent duplicate -> EventInProgressError ==
calls: 1 outcomes (second, first): ['ACK', 'ACK']
  log> pydomain.event_bus:ERROR:Event handler slow failed for Shipped +exc_info=EventInProgressError
  log> pydomain.event_bus:ERROR:Event handler slow failed for Shipped +exc_info=RuntimeError
== S5 one EventIdempotencyBehavior on two handlers -> EventIdempotencyMisuseError ==
ran: ['h1'] outcomes: ['ACK']
  log> pydomain.event_bus:ERROR:Event handler h2 failed for Shipped +exc_info=EventIdempotencyMisuseError
```

- S3 is the probe the W2-2 review asked for: the failure is not marked (the second delivery runs, with the same
  derived `event_id`), but both deliveries are ACKed. A real broker would not redeliver.
- S4: a second delivery arrives while the first still runs and is refused with `EventInProgressError`; the first
  then fails. Both are ACKed, and the handler never succeeded: the event is lost.
- S5: the misconfigured second handler never runs, and the message is ACKed.

`test_inbound_redelivery_after_a_failure_runs_again` (`tests/cqrs/test_event_idempotency.py` l.1126-1158) drives
the same flow as S3 through `InMemoryMessageSubscriber.simulate_message` three times. It asserts the handler
calls and the shared `event_id`, not the outcome of each delivery, and it relies on the first
`simulate_message` returning normally.

---

## 3. The failure logs ADR-071 left to this cluster

ADR-071 decision 5 (l.112-116), B-31, B-33 and B-44 left these to W2-3; ADR-077 (l.138) adds that the saga
manager's compensation, handler and timeout lines keep their format until W2-3.

### Inventory

| # | Logger | Where | Level | Text | Traceback | Exception text in the message |
|---|---|---|---|---|---|---|
| L1 | `pydomain.event_bus` | `event_bus.py` l.121-125 | ERROR | `Event handler <name> failed for <Event>` | yes (`logger.exception`) | no |
| L2 | `pydomain.message_subscriber` | `message_subscriber.py` l.279-283 | ERROR | `Translation failed for <IE> on topic '<t>' — discarding` | yes | no |
| L3 | `pydomain.message_subscriber` | l.338-343 | ERROR | `Dispatch failed for <Event> (from <IE>) on '<t>' — propagating` | yes | no |
| L4 | `pydomain.saga` | `cqrs/saga/manager.py` l.284-289 | ERROR | `Compensation command <Cmd> failed for saga <id>: %s` with the exception | no | yes, `str(exc)` |
| L5 | `pydomain.saga` | l.493-497 | ERROR | `Saga <SagaClass> failed for event <Event>` | yes | no |
| L6 | `pydomain.saga` | l.737-741 | ERROR | `on_timeout() failed for saga <id>: %s` with the exception | no | yes, `str(exc)` |
| L7 | `pydomain.saga` | l.772-776 | ERROR | `Failed to dispatch timeout command for saga <id>: %s` with the exception | no | yes, `str(exc)` |
| L8 | `pydomain.infrastructure.subscription` | `infrastructure/subscription.py` l.184-189 | WARNING | `Subscription <id> batch processing failed; will retry in <s>s` | yes (`exc_info=True`) | no |

The saga manager's other failure lines already name the type only (ADR-071 B-42, ADR-077): `stalled during
dispatch` (l.363-367), `Forward command … failed during compensation` (l.267-272), `Recovery failed` (l.700-704),
`Could not hydrate command` (l.594-599). `LoggingBehavior`'s failure line is type-only too (`behaviors.py`
l.225-233). The relay's lines are value-free since ADR-071 (B-31).

### Findings

**F3.1 A traceback carries the exception's message and its chain.** `logging.Formatter.formatException` prints
`<Type>: <str(exc)>` for the exception and every `__cause__` and `__context__`, plus any `__notes__`. Through a
`MessageBus`, a command handler's exception is the `__cause__` of `CommandExecutionError`, so L5 prints it.

**F3.2 Which lines carry content depends on the dispatcher for L4 and L7.** Through a `MessageBus` the saga
manager receives `CommandExecutionError`, whose message is `Command <Type> failed` (`exceptions.py` l.28-33), so L4
and L7 print no payload unless a `CommandExecutionError` was built with its own message (ADR-077's
`_QuotingBus` case). A dispatcher that raises the handler's own exception (any `CommandDispatcher`, ADR-077
point 6) puts that exception's text into L4 and L7.

**F3.3 The saga event handler's own failure is not logged.** `_process_saga` catches it (l.448-453), marks the
event processed, and stores `describe(handler_err)` through `_handle_event_error` (l.300-328). Only a failure
that leaves `_process_saga` (a failed forward dispatch re-raised at l.374, a repository error) reaches L5.

### Scratch evidence: `sentinel_logs.py`

Every user exception quotes the synthetic sentinel `SENTINEL-synthetic-content-7f3c9a` (the value of
`tests/support/content_sentinel.py`); the script records every `pydomain` log record at WARNING or above and
checks its message and its formatted traceback:

```
L1 event_bus handler               pydomain.event_bus:ERROR tb=yes sentinel in message=False in traceback=True  | Event handler failing failed for Shipped
L2 gateway translator              pydomain.message_subscriber:ERROR tb=yes sentinel in message=False in traceback=True  | Translation failed for ShipmentIE on topic 't' — discarding
L3 gateway dispatch (re-raise)     pydomain.message_subscriber:ERROR tb=yes sentinel in message=False in traceback=True  | Dispatch failed for Shipped (from ShipmentIE) on 't' — propagating
L4 MessageBus handler+compensation pydomain.saga:ERROR tb=no  sentinel in message=False in traceback=False | Compensation command Refund failed for saga …: Command Refund failed
L5 MessageBus forward dispatch fails pydomain.saga:ERROR tb=no  sentinel in message=False in traceback=False | Saga … stalled during dispatch: RuntimeError
L5 MessageBus forward dispatch fails pydomain.saga:ERROR tb=yes sentinel in message=False in traceback=True  | Saga ReminderSaga failed for event Overdue
L6 MessageBus timeouts             pydomain.saga:ERROR tb=no  sentinel in message=True  in traceback=False | on_timeout() failed for saga …: timeout handler failed SENTINEL-synthetic-c…
L6 MessageBus timeouts             pydomain.saga:ERROR tb=no  sentinel in message=False in traceback=False | Failed to dispatch timeout command for saga …: Command Remind failed
   stored: OrderSaga.error='RuntimeError' failed_compensations.error=['CommandExecutionError']; timed-out error='Timeout handler failed: LookupError'
L4 raw dispatcher handler+compensation pydomain.saga:ERROR tb=no  sentinel in message=True  in traceback=False | Compensation command Refund failed for saga …: raw refusal SENTINEL-synthet…
L6 raw dispatcher timeouts         pydomain.saga:ERROR tb=no  sentinel in message=True  in traceback=False | Failed to dispatch timeout command for saga …: raw refusal SENTINEL-synthet…
   stored: OrderSaga.error='RuntimeError' failed_compensations.error=['RuntimeError']; timed-out error='Timeout handler failed: LookupError'
L7 SubscriptionRunner batch        pydomain.infrastructure.subscription:WARNING tb=yes sentinel in message=False in traceback=True  | Subscription 'p' batch processing failed; will retry in 0.0s
L8 LoggingBehavior failure         pydomain.pipeline:ERROR tb=no  sentinel in message=False in traceback=False | Failed COMMAND Charge after 0.00ms: RuntimeError
```

(The script's labels number its scenarios, not the inventory rows: its "L7" is inventory row L8, its "L6 …
timeout command" lines are row L7, and its "L8" is `LoggingBehavior`.) Every row of the inventory leaked the
sentinel in at least one wiring: L1, L2, L3, L5 and L8 through the traceback; L6 in the message always; L4 and L7
in the message with a dispatcher that raises the handler's own exception.

---

## 4. The saga manager and F-9

**F4.1 The manager swallows saga failures itself.** `bind_to` registers `SagaManager.handle` with the dispatcher
(l.162-177). `handle` runs each saga in its own `try` and logs any `Exception` as L5 (l.490-497), so `EventBus`
never sees a saga failure, and neither would the gateway. The saga's own idempotency marks a failing event as
processed (l.448-453, ADR-036), so a redelivered event that failed in a saga handler is skipped by the saga
(`saga.py` l.104-105). A failed forward dispatch suspends the saga, counts a retry and re-raises (l.360-374);
`handle` then logs L5 and returns.

**F4.2 F-9: the two compensation-time errors differ.** A failed forward command stores
`describe(_failure_of(fwd_err))`, the type of the handler's own exception behind `CommandExecutionError`
(l.255-272); a failed compensation stores `describe(comp_err)`, the dispatcher's exception (l.274-289). Through a
`MessageBus` that is `CommandExecutionError` (sentinel script: `failed_compensations.error=['CommandExecutionError']`);
through a dispatcher that raises the handler's exception it is that type (`['RuntimeError']`). `state.error` for a
handler failure and the timeout error already store the handler's own type. Tests that pin the compensation's
`CommandExecutionError`: `tests/saga/test_flows.py` l.391, l.664; `tests/saga/test_manager.py` l.599, l.1304.
`_failure_of` is at l.58-62.

---

## 5. The `SubscriptionRunner`

`_dispatch_to_subscription` calls `process_batch`; an `Exception` is logged as L8, the runner sleeps
`failure_backoff_seconds`, and the checkpoint is not saved, so the batch runs again on the next cycle
(`subscription.py` l.159-191, ADR-048 at-least-once). The runner does not go through `EventBus` or the gateway;
there is no ACK. Only its log line is in this cluster's scope (B-44); poison events and checkpoint semantics are
W2-7 (fix plan §9.1).

---

## 6. `LoggingBehavior` (CQRS-20, rest)

Files: `cqrs/behaviors.py` l.174-243.

- **A raising `payload_formatter` fails the message.** The formatter runs before `next()`, outside the `try`
  (l.207-209), so its exception is the pipeline's: for a command, the bus wraps it in `CommandExecutionError`
  and the handler never runs. Sentinel script: `L8b raising formatter: command failed with CommandExecutionError
  cause ValueError`.
- **A cancellation logs only the entry line.** The `except` catches `Exception` (l.225), so a cancelled message
  logs `Processing …` and nothing else. Sentinel script: `L8c cancelled command, pydomain.pipeline lines:
  ['Processing COMMAND Charge with slow']`.
- The failure line names the type only (L8 above); the audit's verdict is "the default `LoggingBehavior` logs no
  content" (`bug-audit-findings.md` l.215). The formatter's dict is attached as `extra={"payload": …}` on the
  entry line: what it contains is the application's choice.

---

## 7. A handler registered during dispatch (CQRS-18)

`_execute` iterates the live list `self._handlers.get(type(event), [])` (l.108-109), and `register` appends to
that same list (l.70-72). A handler registered by another handler of the same event, during the dispatch, runs in
it:

```
== S11 handler registered during dispatch (CQRS-18) ==
ran in one dispatch: ['first', 'late']
```

The audit marks the row "partly": cancellation behaves as normal asyncio, and swallowing is ADR-046 (A1).

---

## 8. `InMemoryMessageSubscriber` (FAKE-09, rest)

Files: `testing/in_memory_message_subscriber.py` l.14-61; probe `tests/support/probes/fakes/p08_09_broker_sub.py`.

- `simulate_message` calls the one handler stored for the topic and lets its exception propagate; it records no
  outcome (l.44-53). A handler that raises is the only "NACK" signal a test sees. S10 shows that with a real bus
  it never raises.
- One handler per topic: `subscribe` overwrites (l.42), so the fake hides INF-03 (re-registration appends on a
  real consumer, W2-8).
- The payload is passed by reference, with no JSON round trip; delivery works before `start()` and after
  `stop()`.
- `KeyError` for an unknown topic was fixed by W1-9 (ADR-072 point 1); ADR-072 l.119-120 left the rest.
- `MessageSubscriberContract` (`testing/contracts/message_subscriber.py`) has four cases (protocol, start/stop,
  delivery to its topic, isolation between topics); none covers a raising handler. B-37 decided W1-9 adds no
  case, because `simulate_message` is not in the Protocol (ADR-067 rule 3).

---

## 9. What the ADRs and docs promise

| Source | Statement | Matches the code? |
|---|---|---|
| ADR-046 l.17-41 | Per-handler isolation; "No propagation: handler exceptions are caught, logged, and swallowed"; sequential; no UoW | Yes (F1.1) |
| ADR-046 l.56 | "Errors are logged with full traceback for debugging" | Yes; conflicts with ADR-071's rule for library-built text (CQRS-27, F3.1) |
| ADR-058 l.129-131 | Failure isolation "applies equally" to directly dispatched domain events | Yes (F1.3); the conflict A1 |
| ADR-060 l.26-31, l.68-79 | A handler failure "should be negatively acknowledged for retry"; dispatch errors propagate; table: dispatch failure → "Log error, re-raise" → NACK | No: unreachable through a real bus (F1.5, INF-01) |
| ADR-060 l.100 | "transient failures always propagate" | No (F1.5) |
| ADR-060 l.74-77 | Unknown topic, validation and translation failures → ACK (poison) | Yes (F1.4) |
| ADR-048 | `SubscriptionRunner` is at-least-once | Yes, by checkpoint (§5); the inbound path is not (A1) |
| ADR-059 l.69-73 | ACK/NACK is the subscriber's | Yes (F1.6) |
| ADR-063, arch42 §9.17 l.613; `message_broker.py` l.130-133 | The outbound gateway is not an `EventBus` handler because `EventBus` swallows, "correct for in-process reactions" | Yes; relies on F1.1 for the post-command path |
| ADR-071 decision 5 (l.112-116) | The handler-failure logs stay for W2-3 | Yes (§3) |
| ADR-077 point 1 (l.65-70), l.138 | Forward and compensation errors differ until W2-3 unifies them (F-9); the compensation, handler and timeout lines keep their format | Yes (F4.2, §3) |
| ADR-080 point 6 (l.156-164), Negative l.257 | W2-3 receives the handler's own exception after the release and NACKs `EventInProgressError`; until then it is logged and ACKed | Yes (F2.1, S4) |
| `EventInProgressError`, `EventIdempotencyMisuseError` docstrings (`exceptions.py` l.102-104, l.121-122) | "Until W2-3, `EventBus` logs it"; afterwards the gateway NACKs `EventInProgressError` | Yes (S4, S5) |
| Module docstring `message_subscriber.py` l.22-25 | Dispatch failures propagate so the subscriber can NACK | No (F1.5) |
| `concepts/infrastructure/inbound-event-gateway.md` l.251, l.268-281 | Translation and dispatch failures keep their tracebacks "until a later release"; a dispatch failure propagates and the subscriber NACKs (B-177) | Tracebacks: yes; NACK: no |
| `concepts/infrastructure/message-subscriber.md` l.97-106; `concepts/cqrs/integration-events.md` l.145 | Failure-mode contract: dispatch failure → NACK | No |
| `how-to/infrastructure/configure-inbound-event-gateway.md` l.360-361, l.369-375 | "If the handler fails, the exception propagates and the subscriber NACKs"; "Event handler raises → Logged at ERROR, **propagated**" | No |
| `how-to/infrastructure/configure-message-subscriber.md` l.95-104, l.149, l.330 | The adapter examples NACK on raise; "Dispatch failures propagate (NACKed)" | The examples are right for a raising handler; the gateway never raises |
| `how-to/cqrs/handle-domain-events.md` l.99-100 | The bus swallows, the gateway ACKs; a NACK "is planned for a later release"; `EventInProgressError` is logged | Yes |
| `concepts/infrastructure/message-bus.md` l.52-71 | Post-commit failures are "logged, not propagated"; direct dispatch has "the same per-handler failure isolation" | Yes |
| `concepts/ddd/domain-events.md` l.178-189; `concepts/cqrs/handlers.md` l.81 | Events fail independently; "logged, not raised" | Yes |
| `how-to/infrastructure/handle-sensitive-payloads.md` l.172-174 | Step 6: treat the handler-failure logs as sensitive "for now" | Yes (§3) |
| `how-to/sagas/saga-error-handling.md` l.135-147 | Lists the saga log lines; the handler, compensation and timeout lines "still format the exception's message"; L5's traceback carries it | Yes (§3) |
| `how-to/upgrade/upgrade-to-next-release.md` l.361-363, l.411 | The gateway does not NACK yet ("planned for a later release") | Yes |
| arch42 §5 l.525, l.538; §6.1 l.116, l.167; §8.13 l.763; §11 l.290, l.297, R-8 l.312; §12 l.260, l.551-552; §2 l.131, l.167; §10 l.370; §9.22 l.775; §9.31 l.1035, l.1044 | Swallowing, the gateway ACKing until W2-3, and the handler-failure logs as the open exception to ADR-071 | Yes |

arch42 §6 has no inbound-delivery scenario; fix plan §9.2 asks for one.

---

## 10. Tests that pin today's behaviour

- Swallowing: `tests/cqrs/test_event_bus.py` `test_failure_logged_and_swallowed` (l.216),
  `test_multiple_failures_all_isolated` (l.245), `test_failure_in_first_handler_does_not_block_others` (l.270);
  `tests/infrastructure/test_message_bus.py` `test_event_handler_failure_logged_and_swallowed` (l.503),
  `test_event_handler_failure_does_not_block_other_handlers` (l.541),
  `test_dispatch_domain_event_handler_failure_isolation` (l.930).
- The gateway's re-raise and its log texts, through a monkeypatched `bus.dispatch`:
  `tests/infrastructure/test_inbound_event_gateway.py` `test_process_message_dispatch_failure_propagates`
  (l.276), `test_dispatch_failure_names_event_class_and_topic` (l.713),
  `test_translation_failure_names_class_and_topic_with_traceback` (l.624), `test_process_message_translator_failure_logged` (l.239).
- Inbound redelivery: `tests/cqrs/test_event_idempotency.py` `test_inbound_redelivery_after_a_failure_runs_again`
  (l.1126), which needs the first delivery to return normally.
- F-9: the four `"CommandExecutionError"` assertions of §4.
- No sentinel test covers L1 to L8; `tests/test_sensitive_payloads.py` covers the paths ADR-071 fixed.

---

## 11. Gaps and contradictions

1. ADR-046 and ADR-058 (no propagation, "applies equally") contradict ADR-060 (dispatch failures propagate and
   are NACKed): A1. The code follows ADR-046, so INF-01 holds: every inbound handler failure is ACKed (S1, S2).
2. `EventBus` cannot tell its two callers apart (F1.3): the post-command path, where the command has committed and
   ADR-063 relies on swallowing, and the inbound path, where ADR-060 needs the failure.
3. With several handlers, isolation means the others ran (S2). A NACK makes the broker redeliver to every
   handler, the ones that succeeded included; only handlers wrapped in `EventIdempotencyBehavior` skip a
   redelivery (ADR-080).
4. `EventInProgressError` and a failed first delivery together lose the event (S4); ADR-080 point 6 requires
   the NACK. `EventIdempotencyMisuseError` is a configuration error that fails on every delivery (S5), so a
   NACK would redeliver it until the broker's own limit; no document says what the gateway does with it.
5. A cancellation already propagates out of `EventBus` and the gateway, past later handlers (F1.2, S8).
6. ADR-046's "full traceback" contradicts ADR-071's rule (CQRS-27); every inventoried line leaked the sentinel in
   at least one wiring (§3). The gateway and `EventBus` both log one inbound failure today (S7), and would both
   log it once the failure reaches the gateway.
7. Saga failures never reach `EventBus` (F4.1); the saga manager has its own retry, suspension and recovery.
8. F-9: compensation errors store the bus's wrapper type, forward errors the handler's own type (F4.2).
9. `LoggingBehavior` fails the message on a raising formatter and logs nothing on cancellation (§6).
10. A handler registered during a dispatch runs in it (CQRS-18, §7).
11. `InMemoryMessageSubscriber` records no outcome, so a test sees a NACK only as an exception from
    `simulate_message`; no contract case covers a raising handler (§8).
12. The docs listed in §9 teach a NACK that the code never produces, in four concept pages and two how-tos.
