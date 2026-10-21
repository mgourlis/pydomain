# ADR-071: Library-Owned Failure Paths Never Carry Payload Values

## Status

Proposed. Notes [ADR-065](ADR-065-outbox-retry-policy-and-dead-letter.md) (the terminal error its dead-letter
log carries) and [ADR-060](ADR-060-inbound-event-gateway.md) (the validation-failure log). Leaves the
handler tracebacks of [ADR-046](ADR-046-event-handlers-fail-independently.md) to Wave 2 cluster W2-3.

## Date

2026-10-21

## Context

pydomain carries its users' payloads: outbox rows, inbound broker messages, serialized saga commands and
stored events. Those payloads can hold personal or special-category data. Wherever the library itself
builds an error string, a log line or a `repr()`, it decides whether those values leave the payload.

The 0.3.0 audit (`docs/others/bug-audit-findings.md`, rows INF-10 and INF-15; downstream findings PD23 and
PD25; audit plan §4.6) put a synthetic content sentinel into payloads and found it in:

1. **Stored outbox errors.** `OutboundEventGateway._deliver` returned `f"Payload validation failed: {exc}"`.
   The text of a Pydantic `ValidationError` includes every failing `input_value`. The relay stored it
   through `OutboxStore.mark_failed` and `mark_dead_lettered`, so the values were written to the
   outbox table next to the row they came from, and kept there after the payload itself was cleaned up.
2. **The dead-letter log.** ADR-065 logs exhaustion at `ERROR` with the terminal error, so the same text
   reached the logs.
3. **Inbound validation logs.** `InboundEventGateway` logs a payload that fails validation with
   `logger.exception`; the traceback carries the `ValidationError` text.
4. **Saga hydration warnings.** `hydrate_command` logs `Validation failed for …: {exc}`.
5. **`UpcastError`.** `EventUpcaster.upcast` wraps a failing `_transform` as
   `UpcastError(f"Failed to upcast …: {exc}")`, embedding the transform's own error text (for example
   `invalid literal for int() with base 10: '<value>'`). ADR-069 already made the `UpcastError` messages it
   added value-free.
6. **`repr()` of library-owned payload fields.** `OutboxEntry.payload`, `GenericDomainEvent.data` and
   `CompensationRecord.data` print in `repr()` and `str()`, so any log line or assertion message that shows
   one of these objects shows the payload.

Pydantic's `str(ValidationError)` is not the only carrier: its per-error `msg` includes the text of a user
validator's `ValueError` ("Value error, …"), and `errors()` includes `input` and `ctx` unless excluded.

## Decision

We will keep payload values out of every error string, log line and `repr()` that pydomain builds.

**1. One value-free description of an exception.** An internal helper, `_describe(exc)`, describes an
exception by its type and, for a Pydantic `ValidationError`, by the model and the locations of the failing
fields. It never includes an input value, a Pydantic `msg`, `ctx` or `url`, or `str(exc)`.

**2. It is used on every library-owned failure path that reported an exception's text:**

- the relay's stored errors (`mark_failed`, `mark_dead_lettered`) and the relay's log lines, including the
  ADR-065 dead-letter line, which keeps the message id, topic and attempt count;
- the inbound gateway's validation-failure log, which keeps the integration class and topic;
- `hydrate_command`'s warnings, which keep the module and type name;
- the message of the `UpcastError` that wraps a failing `_transform`, which keeps the event type and both
  versions.

**3. Library-owned payload fields are left out of `repr()`.** `OutboxEntry.payload`,
`GenericDomainEvent.data` and `CompensationRecord.data` are declared with `Field(repr=False)`. Pydantic's
`str()` follows the same field list, so `%s` logging hides them too. The attributes, equality and
`model_dump()` are unchanged.

**4. Scope.** The decision covers text that pydomain formats. It does not cover the exceptions that user
code raises and pydomain lets propagate, nor the handler-failure tracebacks of ADR-046, which Wave 2
cluster W2-3 decides together with what any failure log may contain.

### Open questions (to be decided before this ADR is Accepted)

| # | Question | Recommendation |
|---|---|---|
| Q1 | The exact text `_describe` returns, and whether stored errors keep any of the exception's message | One line: `ValidationError: 2 errors for <Model> at amount (int_parsing), invoice_id (missing)`, Pydantic's fixed error-type codes but no `msg`; any other exception by its type name only |
| Q2 | Field locations can carry input: a `dict` field's keys and an unknown key under `extra="forbid"` appear in `loc` | Keep full locations and document that dict keys are not protected |
| Q3 | Whether the relay's broker and store failures (adapter exceptions) are described too, and whether their `logger.exception` tracebacks stay | Describe the stored error; keep the publish-failure traceback in the log until W2-3 decides tracebacks |
| Q4 | Whether `UpcastError` keeps `from exc`, whose cause carries the transform's own text | Keep the chain: the cause is user code's exception, and debugging needs it |
| Q5 | Whether the inbound translator-failure and dispatch-failure tracebacks (user code) are in scope | Leave them to W2-3 with the other handler tracebacks |
| Q6 | Which `SagaState` fields count as library-owned payloads: `pending_commands`, `failed_compensations`, `metadata`, `StepRecord.metadata` | Also hide `pending_commands` and `failed_compensations`, which hold serialized command data |

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Redact values with a pattern (mask anything that looks like a value in `str(exc)`) | Pydantic's text format is not a contract, and a mask cannot know which parts of a user validator's message are content |
| A logging filter that adopters install | Fixes the logs only; the stored outbox errors are data at rest, and the default is still unsafe |
| Store the full error text in a separate, access-controlled column | Every `OutboxStore` adapter would need a second error field and its own retention rule; the `error: str` port stays as is with a value-free text |
| Make `_describe` public | Users format their own errors; a public helper adds API to keep stable for a library-internal need |

## Consequences

### Positive

- A failing payload's values are no longer copied into the outbox's error fields or the library's logs, so
  deleting a payload deletes its values.
- Library objects can be logged and shown in assertion messages without printing the payloads they carry.
- One helper decides what a failure description contains, so a new failure path has one function to call.

### Negative

- Stored error texts and log lines change shape. Tools that parse them (alerting, dashboards) must be
  updated; the upgrade notes say so.
- An operator sees which fields failed but not the values that failed. Reproducing a validation failure needs
  the row's payload, read under the application's own access rules.

### Neutral

- `model_dump()`, equality and attribute access of the three models are unchanged; only `repr()` and `str()`
  hide the payload.
- Exceptions raised by user code (validators, upcasters, translators, handlers) keep their own text when they
  propagate. What the library logs about handler failures is decided in W2-3.

## References

- [ADR-046](ADR-046-event-handlers-fail-independently.md): handler-failure tracebacks, left to W2-3
- [ADR-060](ADR-060-inbound-event-gateway.md): the inbound failure modes
- [ADR-065](ADR-065-outbox-retry-policy-and-dead-letter.md): the dead-letter log line
- [ADR-066](ADR-066-test-first-fixes-with-documentation.md): test-first fixes with documentation
- [ADR-069](ADR-069-upcast-to-class-version-once.md): value-free `UpcastError` messages for versioning
- `docs/others/bug-audit-findings.md` and `docs/others/bug-audit-plan.md` §4.6, §9: rows INF-10, INF-15;
  §13: PD23, PD25, PD30
- `docs/others/bug-fix-plan.md` §8, W1-3
- `src/pydomain/infrastructure/message_broker.py`, `src/pydomain/infrastructure/message_subscriber.py`,
  `src/pydomain/cqrs/saga/hydration.py`, `src/pydomain/es/upcasting.py`, `src/pydomain/cqrs/outbox.py`,
  `src/pydomain/infrastructure/event_registry.py`, `src/pydomain/cqrs/saga/state.py`
- `tests/test_sensitive_payloads.py`, `tests/support/content_sentinel.py`
- YouTrack DCE-118
