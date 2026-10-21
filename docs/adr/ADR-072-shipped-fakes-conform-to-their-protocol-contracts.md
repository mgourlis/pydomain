# ADR-072: Shipped Fakes Conform to Their Protocol Contracts

## Status

Proposed

## Date

2026-10-21

## Context

`pydomain.testing` ships a fake or in-memory adapter for every storage and messaging Protocol, and ADR-067 runs
each `*Contract` suite against them. The 0.3.0 correctness audit (`docs/others/bug-audit-findings.md`,
`docs/others/bug-audit-plan.md` §12) found two fakes that break their own docstrings, ADR-041, or both. A test that
passes against such a fake proves nothing about a real adapter, and a test that fails against it fails for a
reason production never sees:

1. **The subscriber ignores an unknown topic (FAKE-09, A28).** `InMemoryMessageSubscriber.simulate_message()`
   says it raises `KeyError` when no handler is registered for the topic, and the how-to
   `use-in-memory-message-subscriber.md` teaches `pytest.raises(KeyError)`. The fake returns silently, so a test
   that simulates a message on a misspelt topic passes without delivering anything.
2. **A legitimate retry is refused (ES-03).** ADR-041 says the version check and the `command_id` check happen
   atomically in `append_to_stream()`. `FakeEventStore` records the `command_id` before it checks
   `expected_version`, so an append rejected with `ConcurrencyError` still marks its command as processed. The
   retry that ADR-041 tells callers to make (reload, then append again with the same `command_id`) raises
   `DuplicateCommandError`, and the command's events are never stored.
3. **An empty append creates a stream (ES-04).** Appending `[]` at `expected_version=0` to a missing stream
   creates an empty stream. `read_stream` then returns version `0` instead of raising `StreamNotFoundError`,
   `EventSourcedRepository.get_by_id` returns a version-0 aggregate instead of `None`, and a later
   `expected_version=0` append succeeds on the "existing" stream.
4. **A negative offset reads from the end (ES-05).** `read_stream` and `read_all` slice with `from_version`, so
   `from_version=-1` returns the last event, a result no real store gives for an offset. The `EventStore`
   Protocol says `from_version` is a zero-based offset but does not say what a negative one does.

## Decision

We will make the shipped fakes keep every promise their docstrings and the governing ADRs make, pin each promise
that belongs to a Protocol with a case in its `*Contract` suite (ADR-067), and state in the Protocol any promise
the fix needed that the Protocol left open.

**1. `simulate_message()` raises `KeyError` for a topic with no handler.** This is the fake's own documented
behaviour. `simulate_message()` is not part of the `MessageSubscriber` Protocol, which says nothing about a
message on a topic nobody subscribed to, so the promise is pinned by a fake test, not a contract case.

**2. `FakeEventStore` records a `command_id` only when the append succeeds (restores ADR-041).** The duplicate
check runs first, as before, then the version check; the `command_id` is recorded only after both pass and the
events are stored. A retry after `ConcurrencyError` with the same `command_id` and the current version
succeeds. `EventStoreContract` gains the case
`test_retry_after_concurrency_error_with_same_command_id_succeeds`, which follows from ADR-041's atomicity.

**3. A negative `from_version` raises `ValueError` (D-ES-05, decision log B-21).** `read_stream` and `read_all`
raise `ValueError` for any `from_version` below `0`, before looking the stream up. The `EventStore` Protocol
docstrings now state this, `FakeEventStore` implements it, and `EventStoreContract` gains
`test_negative_from_version_raises_value_error` for both reads.

**4. An empty append creates no stream.** Appending `[]` to a missing stream leaves it missing, so
`read_stream` still raises `StreamNotFoundError`.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Change the docstring and the how-to to match the silent subscriber | A misspelt topic in a test would keep passing without delivering anything; the docstring's `KeyError` is the more useful promise for a test double |
| Record the `command_id` before the version check and document that a retry needs a new `command_id` | Contradicts ADR-041 ("both checks happen atomically"), and a new id per retry defeats the deduplication it exists for |
| A negative `from_version` is treated as `0` (D-ES-05) | Hides a caller's wrong offset; the read silently returns the whole stream |
| A negative `from_version` keeps Python's slicing (D-ES-05) | The fake reads from the end, which no real store does, so a test can pass on the fake and fail in production |
| Fix the fakes without contract cases | Adopters' adapters would never face the same promises, and the next drift between a fake and its Protocol would again go unseen (ADR-067) |

## Consequences

### Positive

- A test that passes against `FakeEventStore` or `InMemoryMessageSubscriber` now passes for the reasons a real
  adapter would.
- Adopters' `EventStore` adapters face the retry and negative-offset cases through `EventStoreContract`.
- `FakeEventStore` no longer refuses the retry ADR-041 prescribes.

### Negative

- The `EventStore` Protocol now promises `ValueError` for a negative `from_version`. An adopter's adapter that
  slices or clamps fails the new contract case until it validates the offset.
- A test that relied on the silent subscriber, an empty stream after an empty append, or a negative offset now
  fails and must change (upgrade notes).

### Neutral

- Negative snapshot versions and negative `SubscriptionRunner` checkpoints (also named by ES-05 in the audit)
  are not covered by D-ES-05 and stay as they are.
- `InMemoryMessageSubscriber` still keeps one handler per topic and passes payloads by reference (FAKE-09's
  other observations); this ADR does not change either.

## References

- `src/pydomain/testing/in_memory_message_subscriber.py`, `src/pydomain/testing/fake_event_store.py`
- `src/pydomain/es/event_store.py`: the `EventStore` Protocol docstrings
- `src/pydomain/testing/contracts/event_store.py`: the new contract cases
- `docs/others/bug-audit-plan.md` §12: FAKE-09, FAKE-10, ES-03, ES-04, ES-05, A28
- `docs/others/bug-fix-plan.md` §8 (W1-9), §14 (D-ES-05)
- `docs/others/decision-log.md` §1.2: B-21
- ADR-041: Optimistic Concurrency via `expected_version` + `command_id` Idempotency
- ADR-066: Test-First Fixes with Documentation in the Same Change
- ADR-067: Protocol Contract Suites in `pydomain.testing.contracts`
- YouTrack DCE-124
