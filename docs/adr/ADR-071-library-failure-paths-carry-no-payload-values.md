# ADR-071: Library-Owned Failure Paths Never Carry Payload Values

## Status

Accepted. Notes [ADR-065](ADR-065-outbox-retry-policy-and-dead-letter.md) (the terminal error its dead-letter
log carries) and [ADR-060](ADR-060-inbound-event-gateway.md) (the validation-failure log and the "invalid tracing IDs" warning). Leaves the
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
   one of these objects shows the payload. So do `SagaState.pending_commands` and `failed_compensations`,
   which hold serialized command data.
7. **Saga state errors.** `SagaManager` stores `str(exc)` of a failed compensation command in
   `failed_compensations`, and of a failing event handler or `on_timeout()` in `state.error`, next to the
   saga's data in its repository.

Pydantic's `str(ValidationError)` is not the only carrier: its per-error `msg` includes the text of a user
validator's `ValueError` ("Value error, …"), and `errors()` includes `input` and `ctx` unless excluded.

## Decision

We will keep payload values out of every error string, stored error, log line and `repr()` that pydomain
builds. The author decided the open points on 2026-10-21; each is a row of
[decision-log.md](../others/decision-log.md#12-library-behaviour) (B-29 to B-35); the review of the change
added B-42 to B-44. The handler-failure logs listed under decision 5 are the one exception, left to Wave 2
cluster W2-3.

**1. One value-free description of an exception (B-29).** A private module, `pydomain._describe`, has one
function, `describe(exc)`. For a Pydantic `ValidationError` it returns one line with the exception type,
the model, the error count, and each failing location with its Pydantic error code:

```text
ValidationError: 2 errors for InvoiceIssued at invoice_id (missing), amount (int_parsing)
```

A nested location is dotted (`lines.0.price`); an error on the whole model has the location `<model>`. Any
other exception is described by its type name only (`RuntimeError`). The description never includes an
input value, a Pydantic `msg` (which carries a user validator's message), `ctx` or `url`, or `str(exc)`.
Two strings the application chooses are copied as they are: the error type a validator gives a
`PydanticCustomError` (which it may build from the value) and the model's `title` when its config sets one.
The module imports no other pydomain module, and an architecture rule keeps it that way, so every layer
may use it.

**2. Locations are copied as Pydantic reports them (B-30).** A location names schema fields, with two
exceptions: the keys of a `dict` field (`tags.<key>`) and an unknown key rejected by `extra="forbid"`
appear as they were in the input. They are not protected. Applications that put content in dict keys must
not rely on the descriptions being free of it.

**3. Where the description is used.** Every library-owned failure path that reported an exception's text:

- the relay's stored errors, with their prefixes kept: `Payload validation failed: <description>`,
  `Broker publish failed: <description>` (`mark_failed`, `mark_dead_lettered`);
- the relay's log lines: the validation and publish failures name the message id and topic and end with the
  description, the ADR-065 dead-letter line keeps the message id, topic and attempt count and ends with the
  stored error, and a failed pass logs `Outbox relay pass failed: <description>; backing off`. Broker and
  store failures are logged with `logger.error` and **no traceback** (B-31);
- the inbound gateway's validation-failure log, which names the integration class and topic, ends with the
  description, and has no traceback; and its "invalid tracing IDs" warning, which names the fields that
  failed to parse (`correlation_id is not a UUID`), never their values (B-43). When both ids parse but the
  domain event's own fields reject them (a field narrowed to `UUID4`, given a version-1 UUID), it says
  `the domain event rejected them: <description>` instead;
- both `hydrate_command` warnings (a module or type that cannot be resolved, and a validation failure),
  which name the module and type;
- the message of the `UpcastError` that wraps a failing `_transform`: `Failed to upcast '<type>' from v1 to
  v2: <description>`. It is still raised `from` the original exception (B-32);
- the errors the saga manager stores (B-33): `failed_compensations[...]["error"]` when a compensation
  command fails, `state.error` when a saga's event handler raises, and `Timeout handler failed:
  <description>` when `on_timeout()` raises;
- the suspension reason the saga manager stores when a forward command's dispatch fails, `Dispatch failed:
  <description>` of the handler's own failure (B-42). The default `on_timeout()` copies it into
  `state.error` (`Saga timed out while suspended for: Dispatch failed: RuntimeError`), so that stays
  value-free too.

**4. Library-owned payload fields are left out of `repr()` (B-34).** `OutboxEntry.payload`,
`GenericDomainEvent.data`, `CompensationRecord.data`, `SagaState.pending_commands` and
`SagaState.failed_compensations` are declared with `Field(repr=False)`. Pydantic's `str()` follows the same
field list, so `%s` logging hides them too. Attributes, equality and `model_dump()` are unchanged. The
metadata fields (`SagaState.metadata`, `StepRecord.metadata`) stay visible.

**5. Scope.** The decision covers text that pydomain formats:

- Exceptions that user code raises keep their own text. The cause chained to an `UpcastError` is the
  upcaster's own exception and may quote the payload; so may a validator's or a translator's.
- The `ValidationError` that `EventRegistry.deserialize` lets reach callers is unchanged (B-35): it is
  Pydantic's exception, raised to the caller, not text the library builds.
- Handler-failure logs stay as they are for Wave 2 cluster W2-3, which decides what any failure log may
  contain (ADR-046): the inbound translator and dispatch tracebacks (`pydomain.message_subscriber`), the
  in-process event handler tracebacks (`pydomain.event_bus`), the saga manager's log
  lines that format a command's or handler's exception (`pydomain.saga`), and the traceback a
  `SubscriptionRunner` logs when a batch fails (`pydomain.infrastructure.subscription`, B-44).

> **Amended by [ADR-081](ADR-081-handler-failures-reach-the-inbound-gateway.md) points 4 to 6.** Takes effect with DCE-132 (W2-3). The handler-failure logs follow this ADR too: a type-only description and a value-free stack, without `exc_info`, so the exception of decision 5 ends.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Redact values with a pattern (mask anything that looks like a value in `str(exc)`) | Pydantic's text format is not a contract, and a mask cannot know which parts of a user validator's message are content |
| A logging filter that adopters install | Fixes the logs only; the stored outbox errors are data at rest, and the default is still unsafe |
| Store the full error text in a separate, access-controlled column | Every `OutboxStore` adapter would need a second error field and its own retention rule; the `error: str` port stays as is with a value-free text |
| Make `_describe` public | Users format their own errors; a public helper adds API to keep stable for a library-internal need |
| Locations only, without the error codes; or a structured dict serialized into the string (B-29) | The codes come from Pydantic's fixed vocabulary, carry no input, and say what went wrong; a dict in a `str` field reads badly in logs and is no easier to parse than one line. Rejected by the author on 2026-10-21 |
| Only the top-level field name, or no locations at all (B-30) | The top-level name still shows an unknown key and loses nested paths; no locations leaves operators unable to tell which field failed. Rejected by the author on 2026-10-21 |
| Keep the broker's traceback in the log until W2-3, or leave the broker's text to the adapter (B-31) | These are library-written logs, W2-3 covers handler failures rather than the relay, and adopters forbid content in logs, so the broker's message has to go now. Rejected by the author on 2026-10-21 |
| Cut the chain with `from None`, or raise outside the `except` block (B-32) | `from None` still attaches the original as `__context__`; raising outside the block loses the original entirely, and with it the user's debugging. Rejected by the author on 2026-10-21 |
| Leave the saga manager's stored errors to W2-3 too; or sanitize the inbound translator and dispatch tracebacks now as well (B-33) | `state.error` and `failed_compensations` are stored like the outbox's errors, so they are data at rest and belong here; the tracebacks are handler-failure logs, which W2-3 decides. Rejected by the author on 2026-10-21 |
| Hide only `CompensationRecord.data`; or hide the metadata fields too (B-34) | `pending_commands` and `failed_compensations` hold the same serialized command data; metadata is the application's own context, which it chooses to put there. Rejected by the author on 2026-10-21 |
| Wrap the `ValidationError` that `deserialize` raises in a library exception (B-35) | Changes the exception type callers catch, for text the library never formats. Rejected by the author on 2026-10-21 |
| Leave the dispatch-failure suspension reason as it was and narrow the docs' claims (B-42) | It is stored with the saga like `state.error`, and the default `on_timeout()` copies it into `state.error`, so it is data at rest. Rejected by the author on 2026-10-21 |
| Keep the tracing ids' values in the "invalid tracing IDs" warning, as an exception to this ADR (B-43) | The field name is enough to find the producer's bug, and an id field can carry anything a producer puts there. Rejected by the author on 2026-10-21 |
| Give the subscription batch-failure traceback to W2-7, or remove it now (B-44) | It is a handler-failure traceback like the others; W2-3 decides them together. Rejected by the author on 2026-10-21 |

## Consequences

### Positive

- A failing payload's values are no longer copied into the outbox's error fields, the saga state's errors
  or the library's logs, except the handler-failure logs left to W2-3 (decision 5, arc42 §8.13), so
  deleting a payload deletes its values.
- Library objects can be logged and shown in assertion messages without printing the payloads they carry.
- One helper decides what a failure description contains, so a new failure path has one function to call.

### Negative

- Stored error texts and log lines change shape. Tools that parse them (alerting, dashboards) must be
  updated; the upgrade notes say so.
- An operator sees which fields failed but not the values that failed. Reproducing a validation failure needs
  the row's payload, read under the application's own access rules.
- The broker's and the outbox store's stack traces are no longer logged by the relay, and the inbound
  validation log has no traceback. Debugging a broker adapter needs its own logging.
- `state.error` and a failed compensation's `error` hold the exception's type name instead of its message,
  so a saga that failed with `RuntimeError("payment gateway unreachable")` now records `RuntimeError`.
- Dict keys and unknown keys still appear in locations (B-30).

### Neutral

- `model_dump()`, equality and attribute access of the models are unchanged; only `repr()` and `str()`
  hide the payload.
- Exceptions raised by user code (validators, upcasters, translators, handlers) keep their own text when they
  propagate, including the cause chained to an `UpcastError`. What the library logs about handler failures is
  decided in W2-3.

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
- `src/pydomain/_describe.py`, `src/pydomain/cqrs/saga/manager.py`
- `tests/test_sensitive_payloads.py`, `tests/test_describe.py`, `tests/support/content_sentinel.py`
- `docs/others/decision-log.md` §1.2: B-29 to B-35, B-42 to B-44
- YouTrack DCE-118
