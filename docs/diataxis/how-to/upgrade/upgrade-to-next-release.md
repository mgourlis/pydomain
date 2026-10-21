# How to Upgrade to the Next Release

> **Prerequisites:** none. **Release:** next after 0.3.0 (0.3.1 if it contains only fixes, 0.4.0 otherwise).

This page collects the behaviour changes merged since 0.3.0. Read it before upgrading; each entry says who
is affected and what to do. See [Writing an entry](_index.md#writing-an-entry) for the format.

**Requires pydantic 2.12.5 or later** (DCE-119): see the first behaviour change below.

## Breaking

_None._ Breaking changes ship only through `release/v*` branches.

## Behaviour changes

### pydomain needs Pydantic 2.12.5 or later (DCE-119)

**Requires pydantic 2.12.5.**

Also requires `uuid-utils>=0.10` (was `>=0.9`), the first release with wheels for Python 3.14 (B-77).

**What changed:** the dependency floor rises from `pydantic>=2.7` to `pydantic>=2.12.5`. Snapshots now use
`model_dump(exclude_computed_fields=True)`, new in 2.12, and `model_validate(by_alias=..., by_name=...)`, new in
2.11; 2.12.5 is the latest 2.12 patch, verified by the floor CI job on Python 3.12 and 3.14 ([ADR-075](../../../adr/ADR-075-restore-snapshots-by-validation.md)), B-66, B-75. A CI job runs the
suite at the declared floor. This is a behaviour change, not a breaking one (B-76).

**Who is affected:** projects that pin Pydantic below 2.12.5 or uuid-utils below 0.10.

**What to do:** upgrade Pydantic to 2.12.5 or later and uuid-utils to 0.10 or later; read Pydantic's own release notes for 2.8 to 2.12 if you skip
several versions.

### Stored errors and failure logs no longer carry payload values (DCE-118)

**What changed:** text that pydomain builds from an exception now names the exception's type and, for a
Pydantic `ValidationError`, the model, the error count and each failing location with its error code,
never the values ([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)):

| Where | Before | Now |
|-------|--------|-----|
| Outbox `mark_failed` / `mark_dead_lettered` error, invalid payload | `Payload validation failed: 1 validation error for X` followed by Pydantic's multi-line text with `input_value=…` | `Payload validation failed: ValidationError: 1 error for X at total (int_parsing)` |
| Outbox error, broker failure | `Broker publish failed: <the broker's message>` | `Broker publish failed: ConnectionError` (the type only) |
| Relay log lines | Publish and pass failures logged with `logger.exception` (traceback); validation failure without detail; the dead-letter line ended with the old stored error | `logger.error` with the description appended, no traceback: `… — requeued: <description>`, `Outbox relay pass failed: <type>; backing off`; the dead-letter line ends with the new stored error |
| Inbound validation failure log | `logger.exception` with the `ValidationError` traceback | `logger.error`, `… — discarding: <description>`, no traceback |
| Inbound "invalid tracing IDs" warning | `… — correlation_id='<value>' event_id='<value>'. Dispatching without tracing.` | `… — correlation_id is not a UUID. Dispatching without tracing.` (the fields that failed, never their values); when both ids parse but the domain event rejects them, `… — the domain event rejected them: ValidationError: …` |
| `hydrate_command` warnings | `…: <str(exc)>` | `…: ModuleNotFoundError`, `…: ValidationError: 2 errors for X at …` |
| `UpcastError` from a failing `_transform` | `Failed to upcast 'X' from v1 to v2: <str(exc)>` | `Failed to upcast 'X' from v1 to v2: ValueError`; still raised `from` the original exception |
| `SagaState.error` after a handler or `on_timeout()` raises | `str(exc)`, e.g. `payment gateway unreachable` | The type name, e.g. `RuntimeError`; `Timeout handler failed: RuntimeError` |
| `failed_compensations[...]["error"]` | `str(exc)`, e.g. `Command CancelPayment failed` | The type name, e.g. `CommandExecutionError` |
| `SagaState.suspension_reason` after a forward dispatch fails | `Dispatch failed: <the handler's message>` | `Dispatch failed: RuntimeError` (the handler's exception type); after a timeout, the default `on_timeout()` records `Saga timed out while suspended for: Dispatch failed: RuntimeError` in `state.error` |

The keys of a `dict` field and unknown keys rejected by `extra="forbid"` still appear in locations.

**Who is affected:** alerting, dashboards or scripts that parse the outbox's error column, `SagaState.error`,
`failed_compensations` or pydomain's log lines; tests that assert on an exception's message in those
places; operators who read a broker's stack trace from the relay's log.

**What to do:** match these texts by their prefixes (`Payload validation failed:`, `Broker publish
failed:`, `Timeout handler failed:`, `Dispatch failed:`, `Saga timed out while suspended for:`) or by the type name, not by the exception's message. To see why a
payload failed, validate the stored payload under your own access rules. Log what you need inside your
broker adapter, without the message body. If your saga code needs the reason for a failure, call
`saga.fail(reason)` yourself with a value-free reason instead of raising. Handler-failure logs (event
handlers, inbound translation and dispatch, the saga manager's log lines, a `SubscriptionRunner`'s failed
batch) are unchanged until Wave 2. See
[Handle Sensitive Payloads](../infrastructure/handle-sensitive-payloads.md).

### Payload fields no longer print in `repr()` (DCE-118)

**What changed:** `OutboxEntry.payload`, `GenericDomainEvent.data`, `CompensationRecord.data`,
`SagaState.pending_commands` and `SagaState.failed_compensations` are left out of `repr()` and `str()`.
Before, logging one of these objects, or an assertion message that showed one, printed the payload it
carries. The attributes, equality and `model_dump()` are unchanged
([ADR-071](../../../adr/ADR-071-library-failure-paths-carry-no-payload-values.md)).

**Who is affected:** code or tests that read these fields out of the `repr()` or `str()` of the objects,
for example doctests or snapshot tests of their printed form.

**What to do:** read the field from the attribute (`entry.payload`, `event.data`, `record.data`,
`state.pending_commands`, `state.failed_compensations`) or from `model_dump()`.

### Upcasted events reach the class version once, or raise (DCE-117)

**What changed:** with an `UpcasterRegistry` attached, `EventRegistry.deserialize` now writes the version
the upcaster chain reached to the payload's `event_version`, and raises `UpcastError` when that version is
not the class's version (the default of its `event_version` field, or 1 for a model without one). Before,
the event kept its stored `event_version`, so serializing it and reading it again ran the chain a second
time, and a payload whose chain stopped short of the class (a missing upcaster) or went past it was
validated anyway. Payloads that silently passed with a stale shape now raise. An event stored at a version
newer than the class (written by newer code during a rolling deploy) is unchanged: no upcaster runs,
nothing is raised, and validation decides as before. Separately, a `KeyError` raised by a registered
class's validator or by an upcaster's `upcast()` override now propagates instead of returning a
`GenericDomainEvent`, and `UpcasterRegistry.register` raises `TypeError` for an upcaster without
`target_version` and `ValueError` for a second upcaster for the same step or a `target_version` not
greater than `source_version`, where it used to overwrite or accept them. Registering the same step
twice now raises even when it is the same upcaster class, so a registry shared between test fixtures that
each register the same upcaster fails on the second fixture.
([ADR-069](../../../adr/ADR-069-upcast-to-class-version-once.md), amending ADR-042;
[Event Versioning](../../concepts/es/event-versioning.md)).

**Who is affected:** applications that pass an `UpcasterRegistry` to `EventRegistry` and have stored events
at a version with no complete upcaster chain to the current class, including classes whose `event_version`
was bumped for an additive change without an upcaster; code or test fixtures that register the same
upcaster step twice or rely on the overwrite; code that expects a `GenericDomainEvent` when a registered
class fails with `KeyError`. Upcasters that set `event_version` themselves keep working. Without an
`UpcasterRegistry`, only the `KeyError` change applies.

**What to do:** register an upcaster for every version step between your oldest stored event and the
current class. A step with no field changes needs a pass-through upcaster:

```python
class OrderPlacedV2ToV3(EventUpcaster):
    source_type: ClassVar[str] = "OrderPlaced"
    source_version: ClassVar[int] = 2
    target_version: ClassVar[int] = 3

    def _transform(self, event: dict[str, Any]) -> dict[str, Any]:
        return event  # v3 only added a field with a default
```

Register each step once, and give each test its own `UpcasterRegistry`. Remove `event_version` assignments
from `_transform` if you like; they are no longer needed. Fix or catch the `KeyError` in validators and
`upcast()` overrides that used to be hidden.

### `event_version` needs a default to be read through an `UpcasterRegistry` (DCE-117)

**What changed:** a class that declares `event_version` without a default (for example
`event_version: int` redeclared on a `DomainEvent` subclass) has no class version. Read through an
`EventRegistry` with an `UpcasterRegistry` attached, it now raises `UpcastError` naming the type and asking
for a default; before, it validated the payload's own `event_version`. A `default_factory` counts as a
default ([ADR-069](../../../adr/ADR-069-upcast-to-class-version-once.md), decision 2b).

**Who is affected:** event classes that redeclare `event_version: int` without a default, read through an
`UpcasterRegistry`.

**What to do:** give the field its current version as a default:

```python
class OrderPlaced(DomainEvent):
    event_version: int = 3  # was: event_version: int
```

### Stored `"version"` values are checked, and integer strings are upcast (DCE-117)

**What changed:** with an `UpcasterRegistry` attached, an envelope `"version"` that is a string of ASCII
digits (`"2"`) is now read as that integer, and any other value that is not an `int` (`None`, a `bool`, a
float, or other text such as `"-1"`, `" 2"`, `"2.0"`, `"v2"`) raises `UpcastError` naming the type, not the
value. In 0.3.0 a `None` or string version found no upcaster, so the chain was skipped and the payload was
validated unchanged, and a `bool` was read as 0 or 1. An upcaster whose `_transform` or `upcast()` returns
something other than a dict now raises `UpcastError` as well; in 0.3.0 the result was passed on, which led
to a `ValidationError` later or to an error in the next upcaster
([ADR-069](../../../adr/ADR-069-upcast-to-class-version-once.md), decision 2b).

**Who is affected:**
- Event stores or adapters that write the version as an integer string. Those rows are now upcast through
  the chain; in 0.3.0 they were read silently in their stale shape. Results change for these rows.
- Event stores or adapters that write `None`, a `bool` or non-numeric text as the version. Every read of
  such a row now raises.
- Custom upcasters that return a new object that is not a dict.

**What to do:** check that your upcasters produce the current shape for the integer-string rows that used
to skip them. Store `"version"` as an integer, as `EventRegistry.serialize` does, and repair rows whose
version is `None` or not a number. Return the payload dict from `_transform` and from `upcast()` overrides.

### Rollback discards the events buffered in repositories (DCE-116)

**What changed:** `AbstractUnitOfWork.rollback()` used to clear only the unit of work's own list of
collected events. Events that `save()` had buffered in a repository survived, and when the repository
outlived the unit of work (one `FakeRepository` or `EventSourcedRepository` shared by every dispatch) the
next commit published a refused command's events under the next command's tracing IDs. `rollback()` now
calls `pull_events()` on every repository in `_repos` and discards the result. Calling it again changes
nothing ([ADR-068](../../../adr/ADR-068-rollback-drains-event-buffers.md), restoring ADR-005).

A committed scope is not rolled back, but leaving it now drains the repositories too, so a change staged
after `commit()` never reaches a later scope. `collect_events()` still returns the committed events.

**Who is affected:** applications and tests that share a repository across units of work; subclasses whose
`_repos` holds an object without `pull_events()` (commit already required it); applications that share one
repository across units of work that run at the same time. With concurrent dispatches, one scope's commit
or drain can take another dispatch's buffered events: they are dropped or attributed to another command,
not only stamped with the wrong tracing IDs. This was already possible before; draining on exit makes the
dropped case more likely. `AggregateLockingBehavior` does not make sharing safe: its locks are per aggregate key, so commands on different aggregates still share the buffer, and it releases them before the commit and the exit (CQRS-K3, fixed by W2-1).

**What to do:** nothing, unless a test relied on a failed command's events appearing in a later dispatch.
When dispatches run concurrently, build the repository per unit of work (in the factory or in
`__aenter__`) instead of sharing one; share a repository only between units of work that run one after
another.

### Storage rollback moves to the `_rollback()` hook (DCE-116)

**What changed:** `AbstractUnitOfWork` has a new async extension hook, `_rollback()`, a no-op by default like
`_flush()` and `_commit()`. `rollback()` calls it and then, in `finally` blocks, drains the repositories
and starts a new scope, so a storage rollback that raises can no longer skip the drain. The error is
re-raised afterwards.

**Who is affected:** subclasses that override `rollback()` to roll back their session.

**What to do:** move the storage rollback into `_rollback()` and drop the `super().rollback()` call:

```text
# before
async def rollback(self) -> None:
    await self._session.rollback()
    await super().rollback()

# after
async def _rollback(self) -> None:
    await self._session.rollback()
```

Keep `_rollback()` idempotent: it can run twice in a row (see the next entry).

### Leaving a unit of work without `commit()` rolls back (DCE-116)

**What changed:** `__aexit__` used to roll back only when the body raised. It now rolls back whenever the
scope did not commit, as the `UnitOfWork` Protocol always said. `FakeUnitOfWork._rolled_back` is `True`
after such an exit, and it is reset to `False` each time the fake is entered (it used to stay `True`
forever after the first rollback).

**Who is affected:** code that enters a unit of work, stages changes and leaves without committing,
expecting them to stay pending; tests that asserted `uow._rolled_back is False` after a clean exit, or that
read `_rolled_back` across several `async with` blocks.

Code that calls `rollback()` itself and then leaves the block now gets a second rollback from `__aexit__`:
the scope the explicit rollback started has not committed either, and `__aexit__` cannot tell whether
anything was staged since. `rollback()` drains nothing new the second time, but a storage rollback in
`_rollback()` (or in an overridden `rollback()`) runs twice.

**What to do:** call `commit()` before leaving the block when the changes should persist. Make the storage
rollback idempotent (SQLAlchemy's `session.rollback()` already is). Read `_rolled_back` right after the scope
you are testing.

### The Command Bus rolls back once, and a raising rollback keeps `CommandExecutionError` (DCE-116)

**What changed:** on a failed dispatch the bus called `rollback()` and then `__aexit__` called it again. A
rollback that raised replaced `CommandExecutionError` with the raw storage error. The bus now leaves the
rollback to `__aexit__`, so it runs once, and wraps the failure outside the `async with` block: the caller
always gets `CommandExecutionError`, whose `__cause__` is the handler's (or the commit's) exception and
whose `__context__` is the rollback's exception, if it raised. In that case a note on the error, shown in
tracebacks, names the rollback exception's type (`clean-up also failed: ConnectionError`), never its
message. The same note appears when a committed scope's drain raises after a handler's own commit. If the
drain on exit raises after a successful commit, that exception propagates unwrapped and no events are
returned, though the commit persisted. A cancellation is not wrapped: `CancelledError` propagates after one rollback.

**Who is affected:** unit-of-work subclasses that counted on `rollback()` running twice, or that roll back
only when called by the bus; callers that caught the raw rollback error.

**What to do:** catch `CommandExecutionError` and read `__cause__`; read `__context__` for a rollback
failure. Make an overridden `rollback()` idempotent.

### `collect_events()` returns a copy (DCE-116)

**What changed:** `AbstractUnitOfWork.collect_events()` returned its internal list, so the events a
dispatch returned changed when a later scope ran on the same unit of work, such as a nested dispatch with a
shared factory. It now returns a new list on each call.

**Who is affected:** code that appended to or cleared the returned list to change the unit of work's
events.

**What to do:** nothing for readers of the list. Do not rely on changing it.

### A second `commit()` in one scope raises `UnitOfWorkAlreadyCommittedError` (DCE-116)

**What changed:** a second `commit()` in one scope used to flush again, write every event collected so far
to the outbox again and commit again; a retry after a failed `commit()` did the same. It now raises the new
`UnitOfWorkAlreadyCommittedError` (a `CQRSError`, exported from `pydomain.cqrs` and `pydomain`) before any
hook runs. `rollback()` or entering the unit of work again starts a new scope.

Inside a dispatch, a handler that commits makes the bus's own commit raise: the caller gets
`CommandExecutionError` whose `__cause__` is `UnitOfWorkAlreadyCommittedError`. Nothing is written twice,
the handler's own commit **is** persisted, and the scope is not rolled back. Its events are never
dispatched in-process, because the bus raises before it returns them; they reach consumers only through the
outbox, if a writer is configured.

A retry after a commit that failed part-way also raises; the message says nothing from the failed commit is
guaranteed persisted. Call `rollback()` (or leave the block) and run the work again in a new scope.

**Who is affected:** handlers that call `uow.commit()` themselves (ADR-020 forbids it); code that commits
twice in one `async with` block or retries a failed commit without rolling back.

**What to do:** remove `commit()` calls from handlers. Outside the bus, commit once per `async with`
block:

```text
# before
async with uow:
    ...
    await uow.commit()
    ...
    await uow.commit()

# after
async with uow:
    ...
    await uow.commit()
async with uow:
    ...
    await uow.commit()
```

To retry a failed commit, roll back (or leave the block) and run the work again in a new scope: the
rollback drains the staged events, so re-running the work is what stages them again.

### A shallow copy of an aggregate no longer shares its pending events (DCE-125)

**What changed:** `model_copy()` and `copy.copy` of an `AggregateRoot` used to share the pending-event list with
the original, so an event recorded on one appeared on both. Every copy now owns a new list holding the events
that were pending at the time of the copy, as deep copies already did ([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md), amending ADR-050).

**Who is affected:** code or tests that copy an aggregate and expect events recorded on the copy to show on the
original, or the reverse.

**What to do:** record the event on the aggregate you save. Code that relied on the sharing published events
twice or lost track of them, so it needs a fix, not a workaround.

### `NewType` and PEP 695 alias ids are generated (DCE-125)

**What changed:** `Entity[OrderId]` with `OrderId = NewType("OrderId", UUID)`, with `type OrderId = UUID`, with
`Annotated[UUID, ...] | None`, or with aliases nested in unions, used to raise `TypeError` when built without an
`id`. The guard now resolves these recursively and checks the generated value against the types they name: a
matching generator produces an id, and a mismatch raises `DomainError`, as ADR-007 promises. An id type the guard
cannot check, such as the unbound `TypeVar` of a generic entity built without a type argument (or a bare
`Entity()`), now raises `DomainError` saying to parametrize the generic or pass an id, instead of `TypeError`;
any other type it cannot check, such as `Literal["a"]`, raises `DomainError` asking for an explicit id. For
`Entity[UUID | None]`, a generator that returns `None` is now accepted, as the annotation allows; 0.3.0 raised
`DomainError`.
`Entity[Any]` used to raise `TypeError` too; it now accepts every generated value ([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)).

**Who is affected:** entities with a `NewType`, alias or `Annotated` id that always passed `id` explicitly, and
code that caught `TypeError` from the guard.

**What to do:** nothing for the first; catch `DomainError` instead of `TypeError` for the second.

### `Entity.model_validate()` no longer writes the id into your dict (DCE-125)

**What changed:** validating a dict without an `id` used to add the generated `id` to that dict, so validating it
again reused the id. The dict is now left unchanged, and each validation generates a new id
([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)).

**Who is affected:** code that read the generated id back from the dict it validated.

**What to do:** read the id from the returned entity (`entity.id`).

### A per-subclass `_id_generator` is used by events, commands and queries (DCE-125)

**What changed:** a `DomainEvent`, `Command` or `Query` subclass that set its own `_id_generator` class variable
was ignored: its `event_id`, `command_id` or `query_id` came from the base class's generator. The subclass's generator is now
used when the model is validated, as it already was for entities (arc42 §8.11, [ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)). `configure()` is unchanged.

**Who is affected:** event, command and query subclasses that set `_id_generator`.

**What to do:** check that the generator you set is the one you want; until now it had no effect.
`model_construct()` skips validation and still uses the base class's generator.

### Generated `event_id`, `command_id` and `query_id` count as set (DCE-125)

**What changed:** the ids used to come from the field's default factory, so they were not in
`model_fields_set`. They are now filled in before validation, so they are in `model_fields_set`, and
`model_dump(exclude_unset=True)` includes them ([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)).

**Who is affected:** code that dumps events, commands or queries with `exclude_unset=True` and expects the id to
be left out, for example to diff a dump against defaults.

**What to do:** exclude the id explicitly (`exclude={"event_id"}`) if you do not want it in the dump.

### `stamp()` validates its ids (DCE-125)

**What changed:** `stamp()` accepted any value for `correlation_id` and `causation_id`, which produced events whose
dump no longer validated. It now validates the two ids, each against its field's type and constraints: a UUID
string is converted to a `UUID`, and any other value raises `pydantic.ValidationError`. As before, no model
validator of the event runs during `stamp()` ([ADR-011](../../../adr/ADR-011-domain-event-stamp-immutability.md),
[ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)).

**Who is affected:** code or tests that call `stamp()` with something other than a `UUID` or UUID string, such
as a plain string id or an integer.

**What to do:** pass `UUID` values. If tracing ids come from outside as strings, they must be valid UUID strings.

### `stamp()` returns a deep copy (DCE-125)

**What changed:** the stamped copy of a domain event used to share lists and dicts in its payload with the
original, so changing one changed the other. `stamp()` now deep-copies the event, so the original is never
modified ([ADR-011](../../../adr/ADR-011-domain-event-stamp-immutability.md), [ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)).

**Who is affected:** code that relied on the stamped event and the original sharing a payload object.

**What to do:** nothing; no correct code relied on it.

### Composite specifications dump their children's fields (DCE-125)

**What changed:** `model_dump()` and `model_dump_json()` of an `AndSpecification`, `OrSpecification` or
`NotSpecification` showed every child as `{}`. They now contain each child's own fields, nested. There is still no
round trip: a dump cannot be validated back into the leaf classes; a type-tagged round trip is planned as a later
feature ([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md), amending ADR-010).

**Who is affected:** code that stored or compared composite dumps.

**What to do:** expect the children's fields in the dump. Keep rebuilding stored rules in code.

### Deep composite specifications no longer hit the recursion limit (DCE-125)

**What changed:** `AndSpecification`, `OrSpecification` and `NotSpecification` evaluated their children
recursively, so a chain of about 1,000 `and_()` calls raised `RecursionError`. They now use an explicit stack;
short-circuiting, the `subsumes()` rules and composite subclasses that override either method behave as before
([ADR-070](../../../adr/ADR-070-aggregate-copies-id-types-and-event-stamping.md)).

A `NotSpecification` built through `model_construct()` without its child used to raise `AttributeError` when
evaluated; it now raises a `ValueError` that names the missing child.

**Who is affected:** nobody, unless code caught `RecursionError` or `AttributeError` from a specification.

**What to do:** nothing.

### `InMemoryMessageSubscriber.simulate_message()` raises `KeyError` for an unknown topic (DCE-124)

**What changed:** `simulate_message(topic, payload)` returned silently when no handler was subscribed to
`topic`, though its docstring and [the how-to](../testing/use-in-memory-message-subscriber.md) said it raises
`KeyError`. It now raises `KeyError`
([ADR-072](../../../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md)).

**Who is affected:** tests that simulate a message on a topic nobody subscribed to, by mistake (a misspelt
topic, a gateway translation registered after the call) or on purpose to check that nothing happens.

**What to do:** fix the topic or the order of registration. To check that an unrouted message is ignored,
expect the `KeyError`: `with pytest.raises(KeyError): await subscriber.simulate_message(...)`.

### `FakeEventStore` accepts a retry after `ConcurrencyError` with the same `command_id` (DCE-124)

**What changed:** `FakeEventStore.append_to_stream` recorded the `command_id` before checking
`expected_version`, so an append rejected with `ConcurrencyError` used up its `command_id`, and the retry
ADR-041 prescribes raised `DuplicateCommandError`. A `command_id` is now recorded only when its append
stores events. A repeated `command_id` still raises `DuplicateCommandError`, checked before the version
([ADR-072](../../../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md)).

**Who is affected:** tests that expected `DuplicateCommandError` on a retry after a conflict.

**What to do:** expect the retry to succeed. The `EventStore` Protocol docstring now states this, and
`EventStoreContract` has a case for it (`test_retry_after_concurrency_error_with_same_command_id_succeeds`);
an event store of your own that records the id before the version check fails that case. In your own store,
record the `command_id` only when the events are stored, in the same transaction, so a rejected append
leaves no trace of it.

### An empty append to `FakeEventStore` stores nothing (DCE-124)

**What changed:** `FakeEventStore.append_to_stream(aggregate_id, [], expected_version)` created an empty stream,
so `read_stream` returned version `0` instead of raising `StreamNotFoundError`, `EventSourcedRepository.get_by_id`
returned an aggregate at version `0` instead of `None`, and a `command_id` passed with it was used up. An empty
append now runs the duplicate and version checks as before (`DuplicateCommandError`, `ConcurrencyError`) and,
when they pass, stores nothing: no stream is created and the `command_id` is not recorded. The `EventStore`
Protocol now states this, and `EventStoreContract` checks it (`test_empty_append_creates_no_stream`,
`test_empty_append_to_existing_stream_leaves_it_unchanged`, `test_empty_append_does_not_record_command_id`,
`test_empty_append_at_stale_version_raises_concurrency_error`, `test_empty_append_with_duplicate_command_id_raises`)
([ADR-072](../../../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md)).

**Who is affected:** tests that create a stream with an empty append, or expect `DuplicateCommandError` after
an empty append with the same `command_id`; event store adapters of your own that run `EventStoreContract`
and create a stream or record the `command_id` for an empty append. `EventSourcedRepository.save` never makes
an empty append, so application code is not affected.

**What to do:** create a stream in tests by appending at least one event. In your own `EventStore`, return
after the duplicate and version checks when `events` is empty, without writing anything.

### A negative `from_version` raises `ValueError` (DCE-124)

**What changed:** `FakeEventStore.read_stream` and `read_all` sliced the event list with `from_version`, so
`from_version=-1` returned the last event. Both now raise `ValueError` for any negative `from_version`;
`read_stream` raises it before looking the stream up, so a missing stream with a negative offset raises
`ValueError`, not `StreamNotFoundError`. The `EventStore` Protocol now states this promise, and
`EventStoreContract` checks it (`test_negative_from_version_raises_value_error`,
`test_negative_from_version_on_missing_stream_raises_value_error`)
([ADR-072](../../../adr/ADR-072-shipped-fakes-conform-to-their-protocol-contracts.md), D-ES-05).

**Who is affected:** tests that read with a negative offset; event store adapters of your own that run
`EventStoreContract` and slice, clamp or ignore a negative offset. The library never writes a negative
snapshot version or `SubscriptionRunner` checkpoint, and it does not validate them either: if a snapshot store
or checkpoint store returns a negative value, it now reaches the event store and raises `ValueError`, from
`EventSourcedRepository.get_by_id` or from the runner's cycle, which also ends `run()`.

**What to do:** pass a zero-based offset. In your own `EventStore`, raise `ValueError` when `from_version` is
negative in `read_stream` and `read_all`, before looking the stream up.

### `bootstrap()` refuses an inbound gateway on another bus (DCE-121)

**What changed:** `bootstrap()` raises `ValueError` when an inbound gateway's bus is not the application's
bus, before starting anything. Passing `inbound_gateways=` without `message_bus=` always raises, because
`bootstrap()` would create a new bus the gateways do not dispatch into. One gateway object listed twice in
`inbound_gateways` raises `ValueError` too. Before, such an application started normally: inbound events
reached only the handlers registered on the gateway's own bus, while `app.dispatch` and the handlers
registered on `app.message_bus` used the other bus, so those handlers never saw inbound events and no error
said so. The message names the gateway, e.g. `inbound_gateways[0] (InboundEventGateway) dispatches into a different MessageBus than the
application's` ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md), amending
ADR-047).

**Who is affected:** code and tests that call `bootstrap(inbound_gateways=[...])` without `message_bus=`, or
build a gateway on a different `MessageBus` than the one they pass, as the inbound-gateway how-to used to
teach. Such code now raises at startup, also when it registered its handlers on the gateway's bus and
therefore worked. Test doubles used as gateways are checked too: a `MagicMock` gateway gets the `ValueError`
(its `message_bus` is a new mock), and a duck-typed one without `message_bus` gets `AttributeError`.

**What to do:** create the bus first, build every gateway on it, and pass it:

```python
# before
app = await bootstrap(inbound_gateways=[gateway])

# after
bus = MessageBus()
gateway = InboundEventGateway(subscriber, bus)
app = await bootstrap(message_bus=bus, inbound_gateways=[gateway])
```

The gateway's bus is now readable as `gateway.message_bus`. Give a test-double gateway a `message_bus`
attribute or property that returns the bus you pass to `bootstrap()`. Only `bootstrap()` checks the wiring:
an `Application` you construct yourself is not checked.

### `Application` exposes `message_bus` and `event_registry` (DCE-121)

**What changed:** `Application` has read-only `message_bus` and `event_registry` properties, as the docs
already used. `Application(message_bus=bus)` built directly, without an `event_registry`, now holds a new
empty `EventRegistry` instead of `None`, so `app.event_registry` is typed `EventRegistry`
([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

`Application` also keeps its own copy of `inbound_gateways`, and `app.inbound_gateways` returns a new list
each time, so changing either list no longer changes the application. `bootstrap()` copies the list before it
checks and starts the gateways, so a gateway added to your list while they start is neither started nor kept.

**Who is affected:** code that builds `Application` itself and relied on its registry being `None`, or that
changed the gateway list after building the application.

**What to do:** pass your own `EventRegistry` to `Application(...)` if you need a specific one, and pass the
complete gateway list when you build it. Code that reached for the private `app._message_bus` can move to
the public `app.message_bus`.

### Shutdown runs every stop, and a failed start cleans up (DCE-121)

**What changed:** `Application.shutdown()` stops the inbound gateways, then the outbound gateway, then the
broker, as before, but a stop that raises an `Exception` no longer skips the ones after it. After the last
stop, the first error is re-raised with a note `clean-up also failed: <type>` for each later failure (the
type only, never the message). A cancellation still propagates at once. When a `start()` inside `bootstrap()`
raises, the parts started before it are now stopped, in the same order, before the start error is re-raised;
the part that failed to start is not stopped. The same clean-up runs when `bootstrap()` is cancelled while
starting: it is shielded from the cancellation (`asyncio.shield`), and the cancellation is re-raised afterwards,
with the same notes. A cancellation that arrives during the clean-up of a failed start does not interrupt it
either: `bootstrap()` finishes the clean-up, then raises the cancellation, with the start error as its
`__context__`. Because a cancelled `bootstrap()` now waits for its clean-up, a stop that hangs keeps an
`asyncio.timeout` around `bootstrap()` from completing until the stop returns; under anyio cancel scopes, which
cancel again at once, `bootstrap()` re-raises at once and the clean-up finishes in the background.
`KeyboardInterrupt` and `SystemExit` during a start propagate at once, without clean-up. Before, the started
parts kept running and the caller had no `Application` to stop them with ([ADR-073](../../../adr/ADR-073-safe-bootstrap-wiring-and-application-accessors.md)).

**Who is affected:** code that caught a stop error from `shutdown()` and then stopped the remaining parts
itself; adapters whose `stop()` is not safe to call after their `start()` succeeded and a later start failed.

**What to do:** drop any manual clean-up after a failed `shutdown()` or `bootstrap()`. Read the error's
`__notes__` (shown in tracebacks) to see which other stops failed. A clean-up that `bootstrap()` stopped
waiting for (a second cancellation, or an anyio cancel scope) reports its failures as WARNING lines from the
`pydomain.bootstrap` logger, by type only: `Abandoned start clean-up: a stop failed with <type>`, or
`Abandoned start clean-up failed with <type>` when a stop ended it with another `BaseException`. Expect them
in log-capturing tests.

### Snapshots are restored with `model_validate` (DCE-119)

**What changed:** `EventSourcedRepository.get_by_id()` built the aggregate with `cls(id=...)` and assigned each
key of `snapshot.state` with `setattr`, without validation or copies. Nested models came back as dicts, and the
restored aggregate shared the snapshot's lists, so replaying the tail changed the snapshot. It now validates the
state into the aggregate class with `model_validate` ([ADR-075](../../../adr/ADR-075-restore-snapshots-by-validation.md)). As a result:

- nested models are models again, and a snapshot plus its tail equals a full replay;
- the aggregate's field and model validators and `model_post_init` run on the restored state on every load
  (B-53), where before they ran only on the defaults; the state includes the snapshot's `version`. Private
  attributes start from their defaults (B-54); one derived only in `model_post_init` reflects the snapshot on a
  snapshot load and the empty aggregate on a full replay;
- aliased fields are stored under their field names and validated by name only, so they keep their values, also
  when an alias equals another field's name; a top-level key from an older snapshot that is a field's alias or
  serialization alias is renamed to the field name first, so snapshots written under `serialize_by_alias` still
  load. State that gives one field under both its name and an alias (or two aliases) is ambiguous and raises
  `SnapshotRestoreError` naming the field and the keys. Alias keys inside nested models are not renamed: their
  value is dropped without an error and the nested field takes its default, so bump `_snapshot_schema_version`
  for such snapshots (B-66, B-74, B-78);
- a key for a field the aggregate no longer has is ignored under the default `extra="ignore"`, where `setattr`
  raised `ValueError`; an aggregate with `extra="forbid"` rejects it (B-55);
- a compatible value is coerced (a stored `"5"` loads into an `int` field); a value that no longer validates, or
  a new required field without a default, raises `SnapshotRestoreError`, a `DomainError` whose message names the
  failing locations (which include dict keys) and error codes but no value, where `setattr` accepted it
  silently. There is no fallback to full replay (B-55);
- any other exception your validators or `model_post_init` raise during the restore, a `DomainError` included,
  becomes `SnapshotRestoreError` too, named by its type only; a `BaseException` that is not an `Exception`
  propagates (B-67);
- a strict-mode aggregate behind a store that serializes snapshots to JSON fails on values such as `datetime`,
  `UUID`, `Enum`, `Decimal`, `tuple` and `set`, which JSON brings back as strings and lists.

**Who is affected:** aggregates loaded from snapshots that have validators with side effects, private
attributes derived from the fields, validators or `model_post_init` that raise their own exceptions, strict-mode
aggregates, or stored snapshots whose state no longer matches the class, including old snapshots with alias keys
inside nested models; code that relied on a removed field's key raising.

**What to do:** keep model validators free of side effects; derive private attributes in `model_post_init` and
keep them current in `_when`; catch `SnapshotRestoreError` where you caught your own validator's exception.
When you change an aggregate's fields, bump `_snapshot_schema_version` and configure
`RejectStaleSnapshotPolicy`, so old snapshots are skipped and the aggregate is rebuilt by full replay instead
of raising `SnapshotRestoreError`.

### Computed fields are no longer stored in snapshots (DCE-119)

**What changed:** `_take_snapshot()` dumped every `@computed_field` into `snapshot.state`, and loading such a
snapshot always raised `AttributeError` (a property has no setter). Computed fields are now left out of the
state at every depth, nested models' included, and top-level computed keys (names or aliases) are dropped from older snapshots before the restore, so those load too, even for an
aggregate with `extra="forbid"` ([ADR-075](../../../adr/ADR-075-restore-snapshots-by-validation.md)), B-56. Snapshots of aggregates without computed fields are unchanged.

**Who is affected:** code that reads a computed field's value from `snapshot.state` directly, for example a
query over a snapshot table; aggregates whose old snapshots hold a nested model's computed key under that nested
model's `extra="forbid"`: only top-level computed keys are dropped, so loading such a snapshot raises
`SnapshotRestoreError`.

**What to do:** read the value from the restored aggregate, or compute it from the stored fields. For the nested
`extra="forbid"` case, bump `_snapshot_schema_version` and configure `RejectStaleSnapshotPolicy`, so the old
snapshots are skipped and the aggregate is rebuilt by full replay.

### Snapshot stores keep their snapshots isolated; `FakeSnapshotStore` copies (DCE-119)

**What changed:** `FakeSnapshotStore` kept and returned the caller's `Snapshot` object, so changing a snapshot
after `save()`, or one that `get()` returned, changed the stored one, and each load from the fake added the
tail again (ES-09). It now stores and returns deep copies. The `SnapshotStore` Protocol states the promise, and
`SnapshotStoreContract` checks it (`test_changing_a_saved_snapshot_does_not_change_the_stored_one`,
`test_changing_a_returned_snapshot_does_not_change_the_stored_one`) ([ADR-075](../../../adr/ADR-075-restore-snapshots-by-validation.md)).

**Who is affected:** tests that changed a snapshot after saving it and expected the store to see the change, or
compared stored and returned snapshots with `is`; snapshot stores of your own that cache `Snapshot` objects
and run `SnapshotStoreContract`.

**What to do:** save the snapshot again after changing it, and compare snapshots by value. In a caching store,
store and return `snapshot.model_copy(deep=True)`.
### Locks are released however a command ends, and a failed release no longer fails it (DCE-123)

**What changed:** `AggregateLockingBehavior` releases every key whose `acquire()` returned, in reverse order,
after the handler returns or after any `BaseException` from an `acquire()` or the handler. Before, a dispatch
cancelled while it waited on its second key kept the first one, and one `release()` that raised skipped the
keys after it. The releases now run in one background task that the behavior awaits through `asyncio.shield`,
so a second cancellation or an anyio cancel scope stops the wait but not the releases. A failed release is
reported by its type only, never the lock key
([ADR-076](../../../adr/ADR-076-release-every-lock-under-cancellation-and-failing-releases.md)):

| Case | Before | Now |
|------|--------|-----|
| The handler succeeded, a `release()` raised | `CommandExecutionError`, and the unit of work rolled back | The result is returned and the unit of work commits; a WARNING on `pydomain.pipeline`: `Lock release failed for <Command>: <type>` |
| The handler raised, a `release()` raised | The release error replaced the handler's error | The handler's error, with a note `clean-up also failed: <type>` per failed release |
| A `release()` raised a `BaseException` such as a cancellation | Later keys were not released | Every other key is released, then that exception is raised |
| The caller was cancelled during the releases | The release in progress was interrupted | The releases finish; each failure is a WARNING: `Abandoned lock release for <Command> failed with <type>` |

**Who is affected:** code that caught a release error from `dispatch()`; tests that capture
`pydomain.pipeline` warnings; lock providers that tie a lock to the task that acquired it (an `anyio.Lock`, or a
check against `asyncio.current_task()` in `release()`), because `release()` is now called from another task.

**What to do:** stop relying on `dispatch()` to raise for a failed release; give your provider a lease or TTL so a
lock whose release failed frees itself, and watch for the WARNING. Make `release()` work from any task, for
example by keying ownership on a token stored per key.

### `FakeLockProvider` raises `LockReentryError` on a same-task re-acquire (DCE-123)

**What changed:** locks are not reentrant. When the task that holds a key calls `acquire()` for it again,
`FakeLockProvider` raises the new `LockReentryError` (a `CQRSError`, exported from `pydomain.cqrs` and
`pydomain`) instead of deadlocking; the key stays held. A nested dispatch with a key its handler holds therefore
fails with `CommandExecutionError` whose cause chain ends in `LockReentryError`. `LockProviderContract` gains
`test_same_task_cannot_acquire_a_held_key_again`, which passes when the second acquire raises or blocks, and
`test_cancelled_acquire_does_not_take_the_key`
([ADR-076](../../../adr/ADR-076-release-every-lock-under-cancellation-and-failing-releases.md)).

**Who is affected:** tests that nested a same-key dispatch and timed out; adopters running
`LockProviderContract` against a reentrant provider, which now fails the new case.

**What to do:** dispatch the second command after the first returns, or give the commands different keys. A
nested dispatch started in a new task still deadlocks with every provider. A reentrant provider is not
supported.

## Deprecations

_None yet._

## Tooling changes for contributors

These affect contributors to pydomain, not users of the package.

### Code commits and PR titles need the issue prefix (DCE-106)

**What changed:** the commit-msg hook and the `Lint Commits` workflow reject `feat`, `fix`, `perf`,
`refactor` and `test` commits without a `DCE-NN:` prefix. PR titles are linted the same way, because a
squash merge uses the title as the commit subject.

**Who is affected:** contributors writing commits or opening PRs.

**What to do:** start code commits and PR titles with the issue ID, e.g.
`DCE-123: fix(cqrs): drain event buffers on rollback`. Run `make pre-commit` once to install the hook.

### CI runs on Python 3.12, 3.13 and 3.14, and on branch pushes (DCE-107)

**What changed:** `test.yml` runs on every supported Python version and on pushes to `feature/`, `fix/`,
`chore/`, `ci/`, `docs/` and `hotfix/` branches. The Codecov upload was removed.

**Who is affected:** contributors; a red (strict-xfail) test commit now gets its own CI run.

**What to do:** nothing; link the red commit's run from the PR (ADR-066).
