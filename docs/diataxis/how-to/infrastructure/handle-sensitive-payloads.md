# How to Handle Sensitive Payloads

<!-- docs-test: runnable -->

> **Adoption Level:** 3 and up · **Prerequisites:** [Configure an OutboundEventGateway](configure-outbound-event-gateway.md), [Configure an InboundEventGateway](configure-inbound-event-gateway.md)

## Problem

Your events, commands and integration events carry personal or special-category data, and that data must not end up in logs or in error columns outside its retention rules. You need to know what pydomain keeps out of its own text, what it cannot protect, and what your code has to do on top.

## Solution

pydomain never puts payload values into text it builds: the errors it stores, its log lines on failure paths, the exception messages it raises and the `repr()` of its payload fields ([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md), arc42 §8.13). It describes a failure by type names, field locations and Pydantic error codes. Your part is to keep content out of the places the library copies as they are (field locations and your own exceptions), to log identifiers instead of payloads, and to prove it with a sentinel test.

## Steps

### 1. Know what the library writes down

A failure on a library path is described in one line. An invalid outbox payload, for example, is stored as:

```python
from datetime import UTC, datetime
from typing import Any

from pydomain.cqrs.integration_events import IntegrationEvent
from pydomain.cqrs.outbox import OutboundEventRegistry, OutboxEntry
from pydomain.ddd.domain_event import DomainEvent
from pydomain.infrastructure.message_broker import OutboundEventGateway
from pydomain.testing import FakeOutboxStore, InMemoryMessageBroker


class PatientReferred(DomainEvent):
    referral_id: str
    note: str


class PatientReferredIntegration(IntegrationEvent):
    referral_id: str
    note: str
    flags: dict[str, int] = {}


registry = OutboundEventRegistry()
registry.register_translation(
    PatientReferred,
    PatientReferredIntegration,
    "referrals.created",
    lambda event: PatientReferredIntegration(referral_id="r1", note="synthetic"),
)

store = FakeOutboxStore()
entry = OutboxEntry(
    message_id="m1",
    topic="referrals.created",
    payload={"referral_id": "r1", "note": 42},  # note must be a string
    occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
)
await store.append([entry])
await OutboundEventGateway(store, InMemoryMessageBroker(), registry).run_once()

assert store.failures[0][1] == (
    "Payload validation failed: ValidationError: 1 error for "
    "PatientReferredIntegration at note (string_type)"
)
```

The same kind of description is used by the inbound gateway's validation log, `hydrate_command`'s warnings, the `UpcastError` that wraps a failing upcaster, and the errors `SagaManager` stores in `state.error` and `failed_compensations`. Any exception other than a `ValidationError` is described by its type name only, so a broker's `ConnectionError("... body ...")` is stored as `Broker publish failed: ConnectionError`.

### 2. Keep content out of keys

Field locations are copied as Pydantic reports them. A field name is schema, but the keys of a `dict` field, and an unknown key rejected by `extra="forbid"`, come from the input:

```python
store = FakeOutboxStore()
await store.append(
    [
        entry.model_copy(
            update={"payload": {"referral_id": "r1", "note": "x", "flags": {"anxious": "yes"}}}
        )
    ]
)
await OutboundEventGateway(store, InMemoryMessageBroker(), registry).run_once()

assert "flags.anxious (int_parsing)" in store.failures[0][1]
```

Do not use content as dict keys (a diagnosis, a name, a free-text label). Model it as a list of objects with a value field, so only field names reach the location.

### 3. Keep values out of your own exceptions

Exceptions your code raises keep their text: validators, upcasters, translators, handlers. The library's own message stays value-free, but it chains your exception as the cause, and anything that logs the traceback prints it:

```python
from typing import ClassVar

from pydomain.es.exceptions import UpcastError
from pydomain.es.upcasting import EventUpcaster


class ReferralV1ToV2(EventUpcaster):
    source_type: ClassVar[str] = "PatientReferred"
    source_version: ClassVar[int] = 1
    target_version: ClassVar[int] = 2

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        if "note" not in event:
            raise KeyError("note")  # names the field, not its value
        return {**event, "note_length": len(event["note"])}


try:
    ReferralV1ToV2().upcast({"referral_id": "r1"})
except UpcastError as error:
    assert str(error) == "Failed to upcast 'PatientReferred' from v1 to v2: KeyError"
    assert isinstance(error.__cause__, KeyError)  # your exception, your text
```

Name fields and identifiers in your messages, never the values that failed. A validator's `ValueError` message is safe from the library's descriptions but not from your own logging of the exception.

### 4. Log identifiers, not payloads

The library's payload fields are left out of `repr()` and `str()`: `OutboxEntry.payload`, `GenericDomainEvent.data`, `CompensationRecord.data`, `SagaState.pending_commands` and `SagaState.failed_compensations`. Logging an entry shows its identity and routing only:

```python
assert "payload" not in repr(entry)
assert "message_id='m1'" in repr(entry)
```

Your own event and command fields print as Pydantic prints them. Log `message_id`, `event_id`, `correlation_id` and the type name, and read the payload under your access rules when you need it. If a field must never print, declare it with `Field(repr=False)` on your model.

### 5. Prove it with a sentinel

Put a fixed synthetic string into a payload, drive the failure, and assert the string appears in no log record, stored error or `repr()`:

```python
import logging

SENTINEL = "SENTINEL-synthetic-content-0001"


class Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))  # message and any traceback


collect = Collect()
logging.getLogger("pydomain").addHandler(collect)
try:
    store = FakeOutboxStore()
    await store.append(
        # The sentinel is the value that fails: a list where a string is expected.
        [entry.model_copy(update={"payload": {"referral_id": "r1", "note": [SENTINEL]}})]
    )
    await OutboundEventGateway(store, InMemoryMessageBroker(), registry).run_once()
finally:
    logging.getLogger("pydomain").removeHandler(collect)

assert collect.lines  # the failure was logged
assert not any(SENTINEL in line for line in collect.lines)
assert not any(SENTINEL in error for _, error, _ in store.failures)
assert SENTINEL not in repr(store.entries)
```

Run the same check on your own adapters, translators and handlers.

### 6. Treat handler-failure logs as sensitive for now

Some library log lines still carry an exception's text or traceback: a failing in-process event handler (`pydomain.event_bus`), an inbound translator or dispatch failure (`pydomain.message_subscriber`), and the `SagaManager`'s lines about failed commands and handlers (`pydomain.saga`). Wave 2 decides what those lines may contain. Until then, route the `pydomain` loggers to a sink with the same access rules as the data, or keep your handlers' exception messages free of values (step 3).

## Expected Outcome

The outbox's error column, the saga states' errors and pydomain's own failure log lines name what failed and where, never the values. Your sentinel test fails as soon as a value leaks through your own code.

## See Also

- [OutboundEventGateway](../../concepts/infrastructure/outbound-event-gateway.md#what-the-relay-writes-down) — what the relay stores and logs
- [Inbound Event Gateway](../../concepts/infrastructure/inbound-event-gateway.md) — the inbound failure modes
- [How to Hydrate Saga State](../sagas/saga-state-hydration.md) — hydration warnings and failed compensations
- [ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md) — the decision and what it leaves out
