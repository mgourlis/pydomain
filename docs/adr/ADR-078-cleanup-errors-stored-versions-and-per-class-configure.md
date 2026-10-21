# ADR-078: Clean-Up Errors, Conflicting Stored Versions and Per-Class `configure()`

## Status

Proposed. Amends [ADR-068](ADR-068-rollback-drains-event-buffers.md),
[ADR-069](ADR-069-upcast-to-class-version-once.md), [ADR-070](ADR-070-aggregate-copies-id-types-and-event-stamping.md)
and [ADR-073](ADR-073-safe-bootstrap-wiring-and-application-accessors.md); notes
[ADR-008](ADR-008-uuidv7-time-ordered-identity.md).

## Date

2026-10-22

## Context

The review probes of the Wave 1 pull requests (`tests/support/probes/review/`, PRs #102, #105 and #109) and audit
row DDD-08 found edge cases that the Accepted ADRs either leave open or decide in a way the author has since
changed (YouTrack DCE-128):

1. **A cancelled dispatch whose rollback raises loses the cancellation** (ADR-068). The bus wraps only an
   `Exception` from the handler; a `CancelledError` passes through `__aexit__`, and when the rollback raises
   there, the caller of `CommandBus.dispatch()` gets the bare storage error. The cancellation survives only in
   its `__context__`, so the task that was cancelled does not see a cancellation.
2. **A drain error replaces the storage error** (ADR-068). When `_rollback()` raises and a repository's
   `pull_events()` raises too, `rollback()` re-raises the drain's error, with the storage error as its
   `__context__`, although the storage failure is the one that matters.
3. **Conflicting or unreadable stored versions are read anyway** (ADR-069). With an `UpcasterRegistry`, an
   envelope without `"version"` whose payload says `event_version: 2` is read as v1 and upcast again; an
   envelope version that disagrees with the payload's is resolved by the payload's (for a newer event) or
   silently overwritten (for an older one); a digit string longer than Python's integer-string limit
   (4300 digits) makes `int()` raise a bare `ValueError` instead of `UpcastError`.
4. **`shutdown()` repeats notes** (ADR-073). One exception instance raised by two stops, or by the same stop in
   two `shutdown()` calls, is noted again each time, and the raised error gets a note about itself.
5. **`Sub.configure()` changes the base class's generator** (DDD-08, ADR-070 point 4, ADR-008). `configure()`
   assigns the base class's `_id_generator` whatever class it is called on, so `Invoice.configure(...)`
   changes the id generator of every entity, and a later `Entity.configure(...)` overrides it again.

## Decision

The author decided each point on 2026-10-22 ([decision log](../others/decision-log.md#12-library-behaviour)
B-88 to B-90, B-101 and B-102).

**1. A cancelled dispatch stays cancelled (amends ADR-068 point 6; B-88 (1)).** When the handler, a pipeline
behavior or the commit raises a `BaseException` that is not an `Exception` (an `asyncio.CancelledError`, also
under anyio, or a `KeyboardInterrupt`) and the clean-up on exit raises an `Exception` too (the rollback, or the
drain of a committed scope), `CommandBus.dispatch()` re-raises the original `BaseException`, not the clean-up's
error. A note on it names the clean-up error's type only, `clean-up also failed: <Type>`, as for a wrapped
failure (ADR-071). The clean-up error is its `__context__`, and is not shown in a traceback, whose text could
carry content; an explicit `__cause__` of the cancellation is kept. The bus still does not wrap a cancellation
in `CommandExecutionError`, and when a `__aexit__` suppresses a cancellation, the bus re-raises it instead of
returning. A direct user of a unit of
work keeps Python's default (ADR-068): only the bus, which owns the dispatch, changes.

**2. The storage error wins over the drain error (amends ADR-068 point 2; B-88 (2)).** When `_rollback()` raises
(any `BaseException`, a cancellation included) and the drain of the repositories raises too, `rollback()`
re-raises the storage error, with a note `draining the event buffers also failed: <Type>` naming the first
drain error's type only. The drain and the scope reset still run in full. A drain error with no storage error
is re-raised as before.

**3. Stored versions must agree and be readable (amends ADR-069 points 2a and 2b; B-89).** With an
`UpcasterRegistry` attached, `EventRegistry.deserialize()` reads the payload's `event_version`, when the
payload has one, by the same rule as the envelope's `"version"` (an `int`, or a string of ASCII digits), and
raises `UpcastError` when the two disagree:

- an envelope without `"version"` (read as v1) whose payload's `event_version` is not 1:
  `Stored envelope of '<type>' has no version, and the payload's event_version is not 1`;
- an envelope version that differs from the payload's, or a payload `event_version` that is not a version
  (`None`, other text): `Stored version of '<type>' disagrees with the payload's event_version`. This replaces
  point 2a's "keeps the payload's own `event_version`" for a newer event: a newer event whose two versions
  agree is still validated unchanged;
- a digit string too long for `int()`, in the envelope or the payload:
  `Stored version of '<type>' has too many digits to read`.

The messages name the event type only, never a version or another payload value (ADR-071). Without an
`UpcasterRegistry` nothing changes, as ADR-069 decided for every version check. `serialize()` writes the
envelope version from the event's `event_version`, so events it wrote always agree.

**4. A failure is noted once per error, never on itself (amends ADR-073; B-90, B-102).** `shutdown()` and the
clean-up after a failed start note each failure **instance** once on the error they raise: the same exception
seen again, from a second stop or a second `shutdown()`, is not noted again, and a failure that is the raised
error itself gets no note about itself. Distinct failures of the same type still get one note each, so the rule
"a note for each later failure" stands. The record of what was noted lives on the raised exception, as a
private list that pickles and copies as an empty list, so a pickled or copied error carries its notes but never
the failure instances or their messages (ADR-071).

**5. `configure()` is per class (amends ADR-070 point 4; notes ADR-008; B-101).** `Sub.configure(id_generator=…)`
sets the generator of `Sub` and its subclasses only, exactly as declaring `_id_generator` on `Sub` does.
`Entity.configure(...)` stays the default for every class without its own generator, and the same rule holds for
`DomainEvent`, `Command` and `Query`. A class with its own generator, declared or configured, keeps it when a
base class is configured later: the most derived class's own generator wins. `model_construct()` still uses the
base class's generator (ADR-070 point 4), as it does for a declared override.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Defer both unit-of-work cases to W2-1 and document today's behaviour | Rejected by the author on 2026-10-22 (B-88): the cancelled task would keep losing its cancellation |
| Wrap only the `int()` case and document that the envelope wins | Rejected by the author on 2026-10-22 (B-89): two stored versions that disagree mean the store is inconsistent, and reading either one silently can upcast twice |
| Defer the version conflicts to W2-7 | Rejected by the author on 2026-10-22 (B-89) |
| Pin the repeated shutdown note | Rejected by the author on 2026-10-22 (B-90) |
| Note each failure instance once, but still note the raised error about itself | Rejected by the author on 2026-10-22 (B-102): a note about the exception it is on says nothing |
| Skip a note whose text the exception already carries | Rejected by the author on 2026-10-22 (B-102): two distinct failures of one type would get one note, breaking "a note for each later failure" |
| Refuse `configure()` on a subclass, so the class-variable override is the only per-class hook | Rejected by the author on 2026-10-22 (B-101): `Sub.configure()` already reads as per-class, and refusing it breaks code that calls it |
| Keep the global behaviour and warn when `configure()` is called on a subclass | Rejected by the author on 2026-10-22 (B-101): the call would still change every other class |

## Consequences

### Positive

- A cancelled dispatch is seen as cancelled by its task and by anyio or `asyncio.timeout`, also when the storage
  rollback fails, and the failure is still visible in a note.
- The storage failure, which says the transaction may not have rolled back, is the one reported.
- An inconsistent stored event fails on read with a message naming its type, instead of being upcast twice or
  validated at the wrong version.
- Notes are not repeated, however often an application shuts down.
- `Invoice.configure(...)` can no longer change the ids of every other entity.

### Negative

- Behaviour changes, listed in the upgrade notes: a cancelled dispatch whose rollback raises now raises the
  cancellation, not the rollback's error; `rollback()` raises the storage error, not the drain's, when both
  fail; a stored event whose two versions disagree now raises `UpcastError` where it was read; a subclass's
  `configure()` no longer changes the base class.
- The note record is an attribute that the library sets on the raised exception. It keeps the noted failure
  instances, and their tracebacks, alive as long as that exception lives; it is bounded by the number of
  failures one clean-up saw, and a pickled or copied exception starts with an empty record.

### Neutral

- `Entity.configure(...)`, `DomainEvent.configure(...)`, `Command.configure(...)` and `Query.configure(...)`
  called on the base class behave as before.
- Without an `UpcasterRegistry`, `deserialize()` checks no versions, as before.
- Only the bus changes for a cancellation: a direct user of a unit of work keeps Python's default for a body
  and a rollback that both fail (ADR-068), because the bus owns the dispatch and its error contract.
- `rollback()` does not keep a record of its drain notes: a `_rollback()` that raises the same stored
  exception instance on every call (a session that keeps its failure) gets the drain note again on each
  call. B-102 covers the shutdown and start clean-up notes only.

## References

- ADR-008: UUIDv7 for Time-Ordered Identity Generation (noted: `configure()` is per class)
- ADR-068: Rollback Drains Event Buffers and Is Idempotent (amended by this ADR)
- ADR-069: Upcast Events to the Class Version Exactly Once (amended by this ADR)
- ADR-070: Aggregate Copies, Identity Types, Event Stamping and Composite Specifications (amended by this ADR)
- ADR-071: Library-Owned Failure Paths Never Carry Payload Values
- ADR-073: Safe Bootstrap Wiring and the Application's Public Accessors (amended by this ADR)
- `src/pydomain/cqrs/command_bus.py`, `src/pydomain/cqrs/unit_of_work.py`,
  `src/pydomain/infrastructure/event_registry.py`, `src/pydomain/infrastructure/bootstrap.py`,
  `src/pydomain/ddd/entity.py`, `src/pydomain/ddd/domain_event.py`, `src/pydomain/cqrs/commands.py`,
  `src/pydomain/cqrs/queries.py`
- `docs/others/decision-log.md`: B-88, B-89, B-90, B-101, B-102
- `docs/others/bug-audit-plan.md` §12: DDD-08
- arc42 §9.29
- YouTrack DCE-128
