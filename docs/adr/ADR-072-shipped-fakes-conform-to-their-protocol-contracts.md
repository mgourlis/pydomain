# ADR-072: Shipped Fakes Conform to Their Protocol Contracts

## Status

Accepted. Notes [ADR-041](ADR-041-optimistic-concurrency-command-idempotency.md).

## Date

2026-10-21

## Context

`pydomain.testing` ships a fake or in-memory adapter for every storage and messaging Protocol, and ADR-067 runs
each `*Contract` suite against them. The 0.3.0 correctness audit found two fakes that break their own docstrings
or ADR-041, or that settle cases the Protocol left open in a way no real store does (rows FAKE-09, FAKE-10,
ES-03, ES-04 and ES-05 in `docs/others/bug-audit-findings.md`; A28 in `docs/others/bug-audit-plan.md` §12). A
test that passes against such a fake proves nothing about a real adapter, and a test that fails against it
fails for a reason production never sees:

1. **The subscriber ignores an unknown topic (FAKE-09, A28).** `InMemoryMessageSubscriber.simulate_message()`
   says it raises `KeyError` when no handler is registered for the topic, and the how-to
   `use-in-memory-message-subscriber.md` teaches `pytest.raises(KeyError)`. The fake returns silently, so a test
   that simulates a message on a misspelt topic passes without delivering anything.
2. **A legitimate retry is refused (ES-03).** ADR-041 says the version check and the `command_id` check happen
   atomically in `append_to_stream()`. `FakeEventStore` records the `command_id` before it checks
   `expected_version`, so an append rejected with `ConcurrencyError` still marks its command as processed. The
   retry that ADR-041 tells callers to make (reload, then append again with the same `command_id`) raises
   `DuplicateCommandError`, and the command's events are never stored.
3. **An empty append creates a stream (ES-04), a case the Protocol left open.** Appending `[]` at
   `expected_version=0` to a missing stream creates an empty stream. `read_stream` then returns version `0`
   instead of raising `StreamNotFoundError`, `EventSourcedRepository.get_by_id` returns a version-0 aggregate
   instead of `None`, and a later `expected_version=0` append succeeds on the "existing" stream.
4. **A negative offset reads from the end (ES-05), another case the Protocol left open.** `read_stream` and
   `read_all` slice with `from_version`, so `from_version=-1` returns the last event, a result no real store
   gives for an offset. The `EventStore` Protocol says `from_version` is a zero-based offset but does not say
   what a negative one does.

## Decision

We will make the shipped fakes keep every promise their docstrings and the governing ADRs make, pin each promise
that belongs to a Protocol with a case in its `*Contract` suite (ADR-067), and state in the Protocol any promise
the fix needed that the Protocol left open.

**1. `simulate_message()` raises `KeyError` for a topic with no handler, and no `MessageSubscriber` case is
added (B-37).** This is the fake's own documented behaviour. `simulate_message()` is not part of the
`MessageSubscriber` Protocol, which, like ADR-059, says nothing about a message on a topic nobody subscribed
to, and ADR-067 rule 3 allows cases only for what a Protocol promises. The `KeyError` is therefore pinned by a
fake test, and `MessageSubscriberContract` is unchanged.

**2. `FakeEventStore` records a `command_id` only when the append stores events (restores ADR-041).** The duplicate
check runs first, as before, then the version check; the `command_id` is recorded only after both pass and the
events are stored. A retry after `ConcurrencyError` with the same `command_id` and the current version
succeeds. `EventStoreContract` gains the case
`test_retry_after_concurrency_error_with_same_command_id_succeeds`, which follows from ADR-041's atomicity.

**3. A negative `from_version` raises `ValueError` (D-ES-05, decision log B-21).** `read_stream` and `read_all`
raise `ValueError` for any `from_version` below `0`. The `EventStore` Protocol docstrings now state this,
`FakeEventStore` implements it, and `EventStoreContract` gains `test_negative_from_version_raises_value_error`
for both reads. `read_stream` checks the offset before it looks the stream up, so a negative offset on a
missing stream raises `ValueError`, not `StreamNotFoundError` (B-38), pinned by
`test_negative_from_version_on_missing_stream_raises_value_error`.

**4. An empty append runs the checks and stores nothing (B-36).** An append with no events runs the duplicate
check and the version check like any other, raising `DuplicateCommandError` or `ConcurrencyError`. When both
pass it stores nothing: no stream is created, nothing reaches the global log, and the `command_id` is not
recorded, so a later append by the same command is accepted. The `EventStore` Protocol states the rule, and
`EventStoreContract` gains `test_empty_append_creates_no_stream`,
`test_empty_append_to_existing_stream_leaves_it_unchanged`, `test_empty_append_does_not_record_command_id`,
`test_empty_append_at_stale_version_raises_concurrency_error` and
`test_empty_append_with_duplicate_command_id_raises`.

**5. These are behaviour changes, not breaking changes (B-39).** The fakes are test doubles and the new
Protocol promises restore or settle what ADR-041 and the docstrings implied, so they ship in a fix release with
upgrade-note entries and no `!` commits. An adopter's own event store that breaks one of the new promises
fails the matching contract case and is told what to change in the upgrade notes.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Change the docstring and the how-to to match the silent subscriber | A misspelt topic in a test would keep passing without delivering anything; the docstring's `KeyError` is the more useful promise for a test double |
| Record the `command_id` before the version check and document that a retry needs a new `command_id` | Contradicts ADR-041 ("both checks happen atomically"), and a new id per retry defeats the deduplication it exists for |
| A negative `from_version` is treated as `0` (D-ES-05) | Hides a caller's wrong offset; the read silently returns the whole stream |
| A negative `from_version` keeps Python's slicing (D-ES-05) | The fake reads from the end, which no real store does, so a test can pass on the fake and fail in production |
| A negative offset on a missing stream raises `StreamNotFoundError` (B-38) | Makes the argument check depend on the store's state; the caller's error is the offset, whatever the stream |
| An empty append is a true no-op, skipping the checks (B-36) | A stale `expected_version` or a repeated `command_id` would pass silently, so the fake would be looser than a store that checks every append |
| An empty append records its `command_id` (B-36) | Nothing was appended, yet the command could never append its events later |
| The empty-append rule only in the fake, with the Protocol silent (B-36) | Adopters' stores would never face it, and the next drift would again go unseen (ADR-067) |
| A `MessageSubscriber` Protocol promise for unsubscribed topics, plus a case (B-37) | Adds a promise to real adapters to cover a helper only the fake has; a real broker never delivers a topic it was not subscribed to |
| A contract case for a second `subscribe` to the same topic (B-37) | Needs a decision on replace-or-add semantics that W1-9 does not make |
| Mark the new Protocol promises as breaking (`!`) (B-39) | They restore ADR-041 or settle cases the Protocol left open, and only test doubles and contract runs change; breaking changes ship only through `release/v*` |
| Fix the fakes without contract cases | Adopters' adapters would never face the same promises, and the next drift between a fake and its Protocol would again go unseen (ADR-067) |

## Consequences

### Positive

- A test that passes against `FakeEventStore` or `InMemoryMessageSubscriber` now passes for the reasons a real
  adapter would.
- Adopters' `EventStore` adapters face the retry, negative-offset and empty-append cases through
  `EventStoreContract`.
- `FakeEventStore` no longer refuses the retry ADR-041 prescribes.

### Negative

- The `EventStore` Protocol now promises `ValueError` for a negative `from_version`. An adopter's adapter that
  slices or clamps fails the new contract case until it validates the offset.
- A test that relied on the silent subscriber, an empty stream after an empty append, or a negative offset now
  fails and must change (upgrade notes).
- An adopter's store that creates a stream, or records the `command_id`, on an empty append fails the new
  empty-append cases until it returns after the checks.

### Neutral

- Negative snapshot versions and negative `SubscriptionRunner` checkpoints (also named by ES-05 in the audit)
  are not covered by D-ES-05. The library never writes a negative value and does not validate them either, but
  a negative value a snapshot or checkpoint store returns now reaches the event store and raises `ValueError`,
  from `EventSourcedRepository.get_by_id` or from the runner's cycle, which also ends `run()`.
- `InMemoryMessageSubscriber` still keeps one handler per topic and passes payloads by reference (FAKE-09's
  other observations); this ADR does not change either.

## References

- `src/pydomain/testing/in_memory_message_subscriber.py`, `src/pydomain/testing/fake_event_store.py`
- `src/pydomain/es/event_store.py`: the `EventStore` Protocol docstrings
- `src/pydomain/testing/contracts/event_store.py`: the new contract cases
- `docs/others/bug-audit-findings.md`: FAKE-09, FAKE-10, ES-03, ES-04, ES-05
- `docs/others/bug-audit-plan.md` §12: A28
- `docs/others/bug-fix-plan.md` §8 (W1-9), §14 (D-ES-05)
- `docs/others/decision-log.md` §1.2: B-21, B-36, B-37, B-38, B-39
- ADR-041: Optimistic Concurrency via `expected_version` + `command_id` Idempotency
- ADR-066: Test-First Fixes with Documentation in the Same Change
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- YouTrack DCE-124
