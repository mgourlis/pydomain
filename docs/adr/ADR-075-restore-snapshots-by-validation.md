# ADR-075: Restore Snapshots by Validation, Not Attribute Assignment

## Status

Proposed. Amends [ADR-043](ADR-043-snapshot-policy-pluggable-protocol.md) and
[ADR-053](ADR-053-snapshot-schema-version-policy.md).

## Date

2026-10-21

## Context

ADR-043 captures a snapshot with `model_dump(mode="python")` and ADR-053 lets a `SnapshotSchemaPolicy` reject a
stale one. Neither says how `EventSourcedRepository.get_by_id()` turns a snapshot's `state` back into an
aggregate. The code builds `aggregate_cls(id=id_)` and assigns every key of `state` with `setattr`
(`es/event_sourced_repository.py:152-154`). `Entity` does not set `validate_assignment`, so nothing is validated
and nothing is copied. The 0.3.0 audit (`docs/others/bug-audit-findings.md` §2.3, `docs/others/bug-audit-plan.md`
§12) found four defects:

1. **Nested models come back as dicts (ES-08).** `model_dump(mode="python")` turns a nested `BaseModel` into a
   dict, and `setattr` stores that dict. The first replayed event that reads a nested attribute raises
   `AttributeError`; with no tail, the aggregate silently holds dicts where its annotations say models.
2. **A computed field breaks every load (ES-08).** `model_dump()` includes `@computed_field` values, and
   `setattr` on a computed property raises `AttributeError: property ... has no setter`, so an aggregate with a
   computed field can never be loaded from its snapshot.
3. **Loading changes the stored snapshot (ES-09).** The restored aggregate shares the snapshot's lists and dicts,
   so replaying the tail appends to the snapshot itself. `FakeSnapshotStore` keeps and returns the caller's
   object, so each load adds the tail again (a two-item stream loads as 3, 4, 5 items). Any store that caches
   snapshot objects behaves the same.
4. **Two pages describe the snapshot path wrongly.** `how-to/event-sourcing/handle-es-errors.md` §5 says the
   repository falls back to replay by default when a snapshot does not fit; with no schema policy ADR-053
   accepts every snapshot (A24). The `SnapshotPolicy.should_snapshot()` docstring calls `pending_event_count`
   the events "since the last snapshot"; the repository passes the size of the batch it just appended (A36).

## Decision

We will restore a snapshot by validating its state into the aggregate class, exclude computed fields from the
state, and keep stored snapshots isolated from their callers.

**1. Restore by validation (amends ADR-043).** `get_by_id()` builds the aggregate with
`aggregate_cls.model_validate({**snapshot.state, "id": id_})`, then sets `version` from the snapshot and replays
the tail as before. The `id` passed to `get_by_id()` wins over any `id` in the state, as it did. Validation
builds nested models and lists anew, so the aggregate shares no mutable object with the snapshot, and a
snapshot plus its tail gives the aggregate a full replay gives.

**2. Computed fields are not snapshot state (amends ADR-043).** `_take_snapshot()` leaves out every field in
`model_computed_fields`, as it already leaves out `version`. A computed field is derived from the other fields,
so the restored aggregate recomputes it.

**3. Snapshot stores keep their snapshots to themselves.** The `SnapshotStore` Protocol states that changing a
snapshot after `save()`, or changing one that `get()` returned, does not change what the store holds. Two
`SnapshotStoreContract` cases pin it (ADR-067). `FakeSnapshotStore` stores a deep copy on `save()` and returns
a deep copy from `get()`, as ADR-072 requires of every shipped fake.

**4. The docs say what the code does.** `handle-es-errors.md` §5 says a snapshot is used as is unless a
`SnapshotSchemaPolicy` rejects it (ADR-053), and the `pending_event_count` docstring says it is the number of
events in the batch just appended.

**Still open (decided by the author before this ADR is Accepted):**

- Whether restoring may run the aggregate's own model validators on load (`model_validate` runs them).
- What happens to private attributes on restore.
- How stale or invalid snapshot state surfaces: which exception, and whether a failed restore falls back to a
  full replay. ADR-053 rejected detecting drift with a `model_validate()` try/except.
- Whether leaving computed fields out of new snapshots, and ignoring the computed keys in old ones, is a format
  change existing users must be told about.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Keep `setattr` and turn on `validate_assignment` for the restore | Validates one field at a time against a half-restored aggregate, still cannot assign a computed field, and changes assignment semantics for the whole model |
| Keep `setattr` and deep-copy the snapshot first | Fixes ES-09 only; nested models still come back as dicts and computed fields still break the load |
| `model_construct(**state)` | Skips validation, so nested models stay dicts (ES-08 unchanged) |
| Fix only `FakeSnapshotStore` | Hides ES-09 in tests while any caching adapter keeps the defect; the repository must not depend on the store copying |
| Snapshot with `model_dump(mode="json")` | Changes what every store receives and loses Python types the aggregate's validators would have to parse back; the restore change alone fixes ES-08 |

## Consequences

### Positive

- An aggregate loaded from a snapshot equals one built by full replay, nested models included.
- Aggregates with computed fields can use snapshots.
- Loading never changes a stored snapshot, whatever the store does about copies.

### Negative

- Restoring validates every field, which costs more than assigning it.
- Snapshot state that no longer validates now fails where `setattr` accepted it; the open questions above
  decide how that failure surfaces.

### Neutral

- Snapshots of aggregates without computed fields are stored exactly as before.
- `SnapshotThresholdPolicy` and `SnapshotSchemaPolicy` are unchanged.

## References

- `src/pydomain/es/event_sourced_repository.py`, `src/pydomain/es/aggregate.py`, `src/pydomain/es/snapshot.py`,
  `src/pydomain/testing/fake_snapshot_store.py`, `src/pydomain/testing/contracts/snapshot_store.py`
- `tests/es/test_snapshot_restore.py`, `tests/testing/test_contracts_snapshot_store.py`
- `docs/others/bug-audit-findings.md` and `docs/others/bug-audit-plan.md`: rows ES-08, ES-09, A24 and A36
- `docs/others/bug-fix-plan.md` §8, W1-4
- ADR-043: Snapshot Policy as Pluggable Protocol (amended)
- ADR-053: Snapshot Schema Version Policy (amended)
- ADR-066: Test-First Fixes with Documentation in the Same Change
- ADR-067: Protocol Contract Suites
- ADR-072: Shipped Fakes Conform to Their Protocol Contracts
- YouTrack DCE-119
