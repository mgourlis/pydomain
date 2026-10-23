# ADR-081: Handler Failures Reach the Inbound Gateway, and Failure Logs Carry No Content

## Status

Accepted (2026-10-23). Supersedes parts of [ADR-046](ADR-046-event-handlers-fail-independently.md) (no propagation on direct
dispatch; full tracebacks), [ADR-058](ADR-058-messagebus-dispatch-domain-event.md) (failure isolation "applies
equally") and [ADR-060](ADR-060-inbound-event-gateway.md) (the dispatch and translation log lines). Amends
[ADR-071](ADR-071-library-failure-paths-carry-no-payload-values.md) decision 5 (the handler-failure logs it left to
W2-3) and [ADR-077](ADR-077-saga-failure-and-recovery-keep-every-command.md) point 1 (backlog F-9). Constrained by
[ADR-080](ADR-080-identify-delivered-events-for-event-idempotency.md) point 6 (B-153). The commit that accepted it
added notes to ADR-046, 058, 060, 071, 077 and 080, each saying "Takes effect with DCE-132 (W2-3).", and marked the
README rows of the first five (point 10, B-192, B-203, B-204); their text is not edited.
The implementation follows in the three phases of
[04-handler-failure-semantics.frame.md](../../design-docs/04-handler-failure-semantics.frame.md) (B-194).

## Date

2026-10-23

## Context

The 0.3.0 audit found that the inbound path loses every failed delivery (YouTrack DCE-151, fix plan cluster W2-3;
facts and scratch evidence in `design-docs/04-handler-failure-semantics.research.md`):

1. **INF-01 and A1.** `EventBus._execute` catches, logs and swallows each handler's `Exception` (ADR-046). So
   `MessageBus.dispatch(DomainEvent)` always returns, and the `InboundEventGateway` never reaches the re-raise
   that ADR-060 says lets the subscriber NACK. A real bus with one failing handler gives `['ACK']`. With
   `EventIdempotencyBehavior` the failure is not marked (ADR-080), but the broker never redelivers it. A
   concurrent duplicate refused with `EventInProgressError`, followed by a failure of the first delivery, ACKs
   both: the event is lost. ADR-046 and ADR-058 promise no propagation, and ADR-060 promises the NACK. The code
   follows ADR-046. Three concept pages (`inbound-event-gateway.md`, `message-subscriber.md`,
   `integration-events.md`) and two how-tos (`configure-inbound-event-gateway.md`, `configure-message-subscriber.md`)
   teach the NACK.
2. **CQRS-27 and the logs ADR-071 left here.** ADR-046 chose full tracebacks. A content sentinel in a user
   exception reached every handler-failure line in at least one wiring:
   - through the traceback: `EventBus`'s handler line (L1), the gateway's translation and dispatch lines (L2,
     L3), `SagaManager.handle`'s line (L5) and the `SubscriptionRunner`'s batch line (L8);
   - through the message itself: `on_timeout()` (L6);
   - through the message when the dispatcher raises the handler's own exception: the compensation and
     timeout-command lines (L4, L7).
3. **F-9.** A failed forward command during compensation stores its handler's own exception type. A failed
   compensation stores the dispatcher's, `CommandExecutionError` through a bus (ADR-077 point 1).
4. **CQRS-18.** A handler registered during a dispatch runs in that dispatch: `_execute` iterates the live list.
5. **CQRS-20 (rest).** A `LoggingBehavior` whose `payload_formatter` raises fails the message. A cancelled
   message logs only its entry line.
6. **FAKE-09 (rest).** `InMemoryMessageSubscriber` records no outcome, so a test sees a NACK only as an exception
   from `simulate_message`.

ADR-080 point 6 constrains this ADR: the failure W2-3 handles is the handler's own exception, raised after the
idempotency reservation's release, and an `EventInProgressError` is NACKed.

## Decision

The author decided each point on 2026-10-23 ([decision log](../others/decision-log.md#12-library-behaviour) B-180
to B-204; B-195 to B-204 came from the review of PR #133).

**1. Direct dispatch raises once every handler has run (B-180).** `EventBus.dispatch(event)` runs every handler
of the event, in registration order, each in its own `try`, as before:
- each failure is logged (point 4);
- once the last handler has run, it raises `EventHandlersFailed` (point 2) if any of them raised an `Exception`;
- `MessageBus.dispatch(DomainEvent)` and `Application.dispatch(event)` call it, so they raise too, and the
  `InboundEventGateway` relies on that.

`EventBus.dispatch_many(events)` keeps logging and swallowing. It is the post-command path: the command has
committed and its result is returned, so there is no caller that could act on the failure. (ADR-063 point 6 keeps
the outbound gateway out of `EventBus` because `EventBus` swallows; it does not depend on it.) In-process isolation
therefore stays.

Code that dispatches a `DomainEvent` directly from inside a command or event handler now gets
`EventHandlersFailed` when one of that event's handlers fails; inside a command handler, the command then fails
and rolls back (upgrade note 1). One handler's failure never stops the others, on both
paths.

**2. `EventHandlersFailed` (B-181).** A new exported exception:
- it is defined as `class EventHandlersFailed(ExceptionGroup, CQRSError)` in `pydomain.cqrs.exceptions`;
- its message is value-free: `<n> handler(s) failed for <EventType>`;
- `.exceptions` holds each failed handler's own exception object, unchanged, in registration order. When the
  handler is wrapped in `EventIdempotencyBehavior`, the object is the one the behavior re-raised after releasing
  its reservation. The author reads this as satisfying B-153 ("receives the handler's own exception");
- it keeps its event type name in `event_type`; `__new__` and `__init__` take `(event_type, failures)`;
- `derive(excs)` rebuilds it from `self.event_type` and `excs`, so a split by `except*` or `split()` keeps the
  type and recounts (`1 handler(s) failed for …`); under mypy strict it needs `# type: ignore[override]`;
- `__reduce__` returns `(type(self), (self.event_type, list(self.exceptions)), self.__dict__)`, because the
  inherited `BaseException.__reduce__` returns `(cls, self.args)`, which doubles the message on `pickle` and
  `copy.copy` (PR #133, review 9);
- `__repr__` returns `EventHandlersFailed('<message>')` (B-195), without the members, so neither `repr()` nor `str()`
  carries a value. `.message` is the bare text; `str()` adds `(N sub-exceptions)` (B-195);
- `except Exception` catches it, `except* EventInProgressError` matches one member, and W3-1 may place it in the
  hierarchy.

The class is exported from `pydomain` and `pydomain.cqrs`. A scratch model printed `isinstance CQRSError,
ExceptionGroup, Exception: True True True`, `own object kept: True`, and `except*` matched each member type.

**3. What the gateway ACKs and NACKs (B-180, B-182, B-183, B-199, B-200).** The subscriber ACKs when the gateway's handler
returns and NACKs when it raises (ADR-059). The gateway decides as follows:

| What happens | Gateway | Subscriber |
|---|---|---|
| Unknown topic | WARNING, return (unchanged); unreachable through the public API, because the gateway subscribes one closure per registered topic and never drops a registration | ACK (poison) |
| Payload fails validation with a `ValidationError` | ERROR, value-free, return (ADR-071, unchanged) | ACK (poison) |
| Validation raises another `Exception` (a validator's `TypeError`, say), or the translator raises | A translation failure: L2, return (B-200) | ACK (poison) |
| The translator returns something that is not a `DomainEvent` | Checked right after the translator returns, before the `event_id` derivation: a translation failure, L2, return (B-199); W2-8 keeps the wider INF-04 work | ACK (poison) |
| No handler is registered for the event | Nothing runs; return (unchanged) | ACK, silently |
| A handler raises, `EventInProgressError` and `EventIdempotencyMisuseError` included | `EventHandlersFailed` from the bus; one ERROR line (point 5), re-raise | NACK: the broker redelivers; a misconfigured handler ends in the broker's dead-letter queue (B-182) |
| A handler is cancelled, or raises another `BaseException` | Propagates at once; the later handlers do not run; failures before it are already logged | Neither: the subscriber sees the cancellation |

A failing mark after a successful handler stays a type-only WARNING and is not a failure (B-165). A NACK redelivers
the message to every handler of the event, including those that succeeded. The docs therefore say that inbound
handlers should use `EventIdempotencyBehavior` (B-191).

**4. Failure logs carry a type-only message and a value-free stack (B-184).** A private helper next to `describe`
in `pydomain._describe`, `describe_stack(exc)`, renders text containing:
- every exception reachable from `exc`, each once: the `__cause__` chain, the `__context__` chain unless
  `__suppress_context__` is set, and the members of an exception group;
- for each exception, its type name and its frames as `File "<path>", line <n>, in <function>`, read with
  `traceback.walk_tb` from the code objects (`co_filename`, `co_name`) and `tb_lineno`, not with `extract_tb`, which
  reads source lines through `linecache`;
- the notes that match pydomain's own fixed, value-free patterns in full,
  `(clean-up also failed|draining the event buffers also failed): [A-Za-z_]\w*` (ADR-068, ADR-078, ADR-080 point
  6; these are every `add_note` site in pydomain). A user note that starts the same way but continues with other
  text is dropped (B-196).

It never includes an exception's message, any other note, a source line, or a local. It walks iteratively with a
set of the exceptions seen, so a cycle of contexts terminates; it collapses repeated frames; and it caps what it
renders as `traceback.TracebackException` does (`max_group_width=15`, `max_group_depth=10`) and at 100 exceptions,
with a value-free `… N more` marker. A `RecursionError` therefore renders a few lines, not
the ~1000 frames (141 KB) a traceback prints today. The helper imports no other pydomain module, as `describe`
does.

The lines below are logged with it appended after a newline, and none of them sets `exc_info`:

| # | Logger | Line (the description is `describe(...)`) |
|---|---|---|
| L1 | `pydomain.event_bus` | `Event handler <name> failed for <Event>: <description>` |
| L2 | `pydomain.message_subscriber` | `Translation failed for <IE> on topic '<t>': <description> — discarding` |
| L4 | `pydomain.saga` | `Compensation command <Cmd> failed for saga <id>: <description of _failure_of(exc)>` |
| L5 | `pydomain.saga` | `Saga <SagaClass> failed for event <Event>: <description of _failure_of(exc)>` |
| L6 | `pydomain.saga` | `on_timeout() failed for saga <id>: <description of _failure_of(exc)>` |
| L7 | `pydomain.saga` | `Failed to dispatch timeout command for saga <id>: <description of _failure_of(exc)>` |
| L8 | `pydomain.infrastructure.subscription` (WARNING) | `Subscription <id> batch processing failed; will retry in <s>s: <description>` |

> **Note (B-211, DCE-153).** A translator result that is not a `DomainEvent` raises nothing: the gateway checks it without raising and logs a dedicated L2 line at ERROR, without a stack or `exc_info`, `Translation failed for <IE> on topic '<t>': returned <Type>, not a DomainEvent — discarding`, where `<Type>` is `type(result).__name__`. The message is ACKed (B-199), and a coroutine is closed first (B-209). This settles B-207.

L4 to L7 describe the handler's own exception, `_failure_of(exc)` (B-202). `<name>` is the handler's
`__qualname__`, a `functools.partial` unwrapped to its `.func` recursively, else the type's `__qualname__`;
never its `repr()` or `str()`.
`LoggingBehavior`'s entry line names the handler the same way (point 8, B-201). L3 has its own rule (point 5). The
levels are unchanged.

**5. The gateway logs one line for a dispatch failure (B-185).** `EventBus` logs each handler (L1). The gateway's
L3 becomes one ERROR line without a stack: `Dispatch failed for <Event> (from <IE>) on '<t>': <description> —
propagating to the subscriber`. With a real bus the description is `EventHandlersFailed`.

**6. The saga manager keeps its own failure handling (B-183, B-186).**
- `SagaManager.handle` keeps swallowing a saga's failure and logging it (L5). The saga has its own retry,
  suspension and processed-event marks, so a saga failure is never NACKed.
- A failed compensation stores `describe(_failure_of(comp_err))`, its handler's own type, as a failed forward
  command, `state.error` and recovery do. This closes backlog F-9: through a bus, `failed_compensations[...]["error"]`
  holds `RuntimeError` where it held `CommandExecutionError`.

**7. Each dispatch runs the handlers registered when it began (B-187).** `EventBus` takes a snapshot of the
handler list at the start of each event's dispatch, in `dispatch` and `dispatch_many` alike. A handler registered
meanwhile runs from the next dispatch on.

**8. `LoggingBehavior` neither fails on its formatter nor stays silent on a cancellation (B-188, B-189).**
- A `payload_formatter` that raises an `Exception` is logged as a WARNING, `Payload formatter failed for <KIND>
  <Type>: <description>`. The entry line is logged without the `payload` extra, and the message continues.
- A cancellation is logged at INFO, `Cancelled <KIND> <Type> after <ms>ms`, then re-raised. It is recognised with
  `anyio.get_cancelled_exc_class()`, which is right on every anyio backend (B-197). The architecture rule allows
  anyio at runtime in `cqrs.unit_of_work` only, so the call lives in a private helper there, which
  `cqrs.behaviors` imports as it already imports `_shielded`; the rule is unchanged. When no async library is
  running (a coroutine driven by hand, the B-131 case `_shielded` handles), `get_cancelled_exc_class()` raises a
  `RuntimeError` (`anyio.NoEventLoopError`), and the helper returns `asyncio.CancelledError`. Other
  `BaseException`s are not logged.
- The entry line names the handler as point 4 says, never by `str(ctx.handler)` (B-201).

**9. `InMemoryMessageSubscriber` records outcomes (B-190).** Each `simulate_message` appends one entry to a new
public list, `outcomes`:
- `"ack"` when the handler returned;
- `"nack <ExceptionType>"` when it raised an `Exception`. The exception is re-raised, so existing tests that
  expect it still see it.

A `BaseException` records nothing, and an unknown topic still raises `KeyError` with nothing recorded. For a
handler failure behind the gateway, the recorded type is always `EventHandlersFailed`, the exception the gateway
re-raises. The entries stay strings; `(topic, outcome)` tuples are noted for W2-8 (B-198). One handler per topic
stays: that is INF-03, cluster W2-8. `MessageSubscriberContract` gains no case (B-37).

**10. Notes on Accepted ADRs and superseded tests (B-192, B-193, B-203, B-204).** The commit that accepts this ADR
adds a note to each of the following, saying "Takes effect with DCE-132 (W2-3).":
- ADR-046, Decision and Consequences: direct dispatch raises `EventHandlersFailed` after every handler has run;
  the logs follow point 4;
- ADR-058 l.57-59: isolation applies equally, but direct dispatch raises afterwards;
- ADR-060, the processing pipeline and the ACK/NACK table: the table now holds through a real bus; the
  translation and dispatch lines follow points 4 and 5;
- ADR-071 decision 5: the handler-failure logs follow this ADR, and the exception ends;
- ADR-077 point 1 and l.138: F-9 is decided by B-186, and the compensation, handler and timeout lines follow
  point 4;
- ADR-080 l.164 and l.257: "Takes effect with DCE-132 (W2-3). An `EventInProgressError` reaches the gateway
  inside `EventHandlersFailed` and is NACKed." (B-204).

The same commit marks the README rows: ADR-046, 058 and 060 "(partly superseded by ADR-081)", ADR-071 and 077
"(amended by ADR-081)", as ADR-077's acceptance and fix plan §5.1 did (B-203).

Tests whose contract this ADR supersedes are rewritten to the decided behaviour, not weakened. A scratch run of
the suite with point 1 applied found seven:
- `tests/cqrs/test_event_bus.py`: the three tests of `TestFailureIsolation` (DCE-152 renamed
  `test_failure_logged_and_swallowed` to `test_failure_logged_then_raised_after_the_others_ran`, B-206);
- `tests/infrastructure/test_message_bus.py::TestDomainEventDispatch::test_dispatch_domain_event_handler_failure_isolation`;
- `tests/cqrs/test_event_idempotency.py::TestKeysAndConsumers::test_one_instance_on_two_handlers_raises`;
- `tests/cqrs/test_event_idempotency.py::test_inbound_redelivery_after_a_failure_runs_again`, which asserts
  NACK, ACK, ACK (the third skipped);
- the docs example `concepts/cqrs/idempotency-and-locking.md` l.224.

The post-command tests `test_event_handler_failure_logged_and_swallowed` and
`test_event_handler_failure_does_not_block_other_handlers` (`test_message_bus.py` l.503, l.541) keep their
behaviour. Points 4 to 6 rewrite the tests that pin the old log or stored-error text:
- `test_translation_failure_names_class_and_topic_with_traceback` and
  `test_dispatch_failure_names_event_class_and_topic` (`test_inbound_event_gateway.py` l.624, l.713);
- `test_handler_and_compensation_errors_are_stored_by_type` (`tests/saga/test_manager.py` l.1273);
- the `"CommandExecutionError"` assertions at `tests/saga/test_flows.py` l.391, l.664 and `tests/saga/test_manager.py`
  l.599.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| An opt-in keyword on `EventBus.dispatch` that only the gateway passes (B-180) | Two meanings for one call; a consumer written by hand keeps ACKing failures |
| `dispatch` returns a report of the failures (B-180) | Changes the documented `None` return (ADR-058) and is easy to ignore |
| Raise the handler's own exception for one failure and a plain `ExceptionGroup` for several (B-181) | Callers must handle two shapes |
| A `CQRSError` with the first failure as `__cause__` (B-181) | The later failures are lost |
| ACK an `EventIdempotencyMisuseError` with an ERROR log (B-182) | The misconfigured handler silently loses every message |
| NACK translator errors (B-183) | Translators are pure anti-corruption code; a buggy one would loop forever |
| Re-raise saga failures to the gateway (B-183) | The saga has its own retry and suspension, and its processed-event mark skips the redelivery anyway |
| Type-only lines without a stack (B-184) | A post-command handler failure would leave no stack anywhere |
| An opt-in switch for full tracebacks (B-184) | A configuration surface for a leak |
| Drop the gateway's dispatch line (B-185) | Loses the topic and the integration class, which `EventBus` does not know |
| Unify on `CommandExecutionError`, or defer F-9 to W2-5 (B-186) | Reverses ADR-077's forward-command rule; leaves two rules for the same field |
| Document that a handler registered during a dispatch runs in it (B-187) | Surprising, and a snapshot is cheap |
| Keep failing on a raising formatter, documented (B-188) | A logging aid should not fail a command |
| Log a cancellation at WARNING, or document only (B-189) | A cancellation is not an error; silence hides it |
| Also a JSON round trip and refusing delivery outside `start()`/`stop()` in the fake; docs only (B-190) | Breaks tests that pass non-JSON values or never start the fake; docs alone give tests nothing to assert |
| Say nothing about redelivery to handlers that succeeded (B-191) | A NACK makes them run again |
| Each implementation task adds its own ADR notes (B-192) | Accepted ADRs would describe a mix of old and new behaviour for weeks |
| Keep the superseded tests on the `dispatch_many` path and add new ones (B-193) | Moves the old contract to a path it never described |
| Two phases (B-194) | The NACK, the logs and the smaller fixes each have their own review |
| The inherited `repr()` of the group (B-195) | It lists the members, whose messages may carry values |
| Count the notes only, or drop every note (B-196) | Loses pydomain's own clean-up notes, which carry only a type |
| `asyncio.CancelledError` only, documented; widen the anyio rule to `cqrs.behaviors` (B-197) | Wrong on trio; a wider rule for one call |
| `(topic, outcome)` tuples now (B-198) | Belongs with W2-8's per-topic work |
| Defer a non-`DomainEvent` translation result to W2-8, or NACK it on purpose (B-199) | Redelivered for ever, or dispatched as a command |
| NACK a non-`ValidationError` from validation (B-200) | The payload will never pass; it is poison |
| `__name__`, else the type's name (B-201) | Two methods of the same name in different classes read alike, and a `partial` has no `__name__` |
| Describe the wrapper's type in L5 (B-202) | Says `CommandExecutionError` for every saga failure |
| Notes inside the ADRs only, not in the README (B-203) | The index would still list them as unchanged |
| Leave ADR-080's "until W2-3" sentences as they are (B-204) | They would describe the old behaviour |

## Consequences

### Positive

- A failed inbound handler is NACKed, so the broker redelivers it (INF-01, A1). With `EventIdempotencyBehavior`,
  only the failed handler runs again. A concurrent duplicate is redelivered later, not lost.
- In-process isolation stays: every handler runs on both paths, and a committed command's result never depends on
  its event handlers.
- The handler-failure logs follow ADR-071, which closes its last exception (arc42 §8.13, risk R-8). Operators keep
  a stack for every failure.
- Failed compensations and failed forward commands store the same kind of error (F-9).

### Negative

Behaviour changes for 0.4.0, each with an upgrade note:
1. `EventBus.dispatch`, `MessageBus.dispatch(DomainEvent)` and `Application.dispatch(event)` raise
   `EventHandlersFailed` when a handler failed (P1). Code that dispatches a `DomainEvent` directly from inside a
   command or event handler now fails there; a command then fails and rolls back.
2. The inbound gateway propagates handler failures, so subscribers NACK them and brokers redeliver them. Handlers
   that succeeded run again unless they are idempotent (P1). A translator result that is not a `DomainEvent`,
   and a non-`ValidationError` from validation, are ACKed as poison where they were NACKed or dispatched (B-199,
   B-200).
3. `EventInProgressError` and `EventIdempotencyMisuseError` are NACKed (P1).
4. `InMemoryMessageSubscriber.outcomes` is new (P1). Tests that call `simulate_message` with a failing handler
   behind the gateway and expect it to return now get `EventHandlersFailed`.
5. The failure lines L1, L2 and L4 to L8 have a new format: a description and a value-free stack, no `exc_info`
   (P2).
6. The gateway's dispatch line has a new text and no traceback (P2).
7. A failed compensation stores its handler's own error type (P2).
8. A handler registered during a dispatch runs from the next dispatch (P3).
9. A raising `payload_formatter` no longer fails the message, and a cancelled message logs a line (P3).
10. The ADR notes of point 10 (docs only, P3).

Other costs:
- Log handlers and alerting that read `exc_info` from these records get none. Applications that want the full
  exception log it themselves: the subscriber receives it on the inbound path.
- A broker without NACK support (ADR-060 Negative) still loses the message; that is the adapter's choice.

### Neutral

- Cancellation semantics are unchanged.
- `EventBus.dispatch_many`, the outbox relay and the `SubscriptionRunner`'s retry are unchanged; only their log
  lines change.
- No Protocol or signature changes. One exception is added and exported, and one fake attribute is added.

## References

- `design-docs/04-handler-failure-semantics.research.md`: the facts and scratch evidence
- `design-docs/04-handler-failure-semantics.frame.md`: the implementation phases, tests and docs (B-194)
- [Decision log](../others/decision-log.md#12-library-behaviour) B-180 to B-204; backlog F-9
- `docs/others/bug-audit-findings.md`: INF-01, CQRS-18, CQRS-20, CQRS-27, FAKE-09; `docs/others/bug-audit-plan.md`
  §12: A1
- `docs/others/bug-fix-plan.md` §9.1 (W2-3), §9.2
- `src/pydomain/cqrs/event_bus.py`, `src/pydomain/cqrs/exceptions.py`, `src/pydomain/cqrs/behaviors.py`
  (`LoggingBehavior`), `src/pydomain/_describe.py`
- `src/pydomain/infrastructure/message_subscriber.py`, `src/pydomain/infrastructure/subscription.py`,
  `src/pydomain/cqrs/saga/manager.py`, `src/pydomain/testing/in_memory_message_subscriber.py`
- [ADR-046](ADR-046-event-handlers-fail-independently.md), [ADR-058](ADR-058-messagebus-dispatch-domain-event.md),
  [ADR-059](ADR-059-message-subscriber-protocol.md), [ADR-060](ADR-060-inbound-event-gateway.md),
  [ADR-063](ADR-063-outbound-event-gateway-and-outbox.md),
  [ADR-071](ADR-071-library-failure-paths-carry-no-payload-values.md),
  [ADR-077](ADR-077-saga-failure-and-recovery-keep-every-command.md),
  [ADR-080](ADR-080-identify-delivered-events-for-event-idempotency.md)
- YouTrack DCE-132, DCE-151
