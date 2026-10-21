# ADR-075: Restore Snapshots by Validation, Not Attribute Assignment

## Status

Proposed. Amends [ADR-043](ADR-043-snapshot-policy-pluggable-protocol.md),
[ADR-053](ADR-053-snapshot-schema-version-policy.md) and, for the Pydantic floor,
[ADR-002](ADR-002-pydantic-v2-only.md).

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
`aggregate_cls.model_validate(state, by_alias=False, by_name=True)`, where `state` is `snapshot.state` without the
top-level keys of computed fields, with alias keys renamed (below), with `id` set to the id passed to `get_by_id()` (which wins over any `id` in the
state, as it did) and with `version` set to the snapshot's version, so validators see it and nothing is assigned
afterwards. It then replays the tail as before. Snapshots are keyed by field name, so the state is validated by
name only ([B-74](../others/decision-log.md#12-library-behaviour), which replaces the `by_alias=True` of [B-66](../others/decision-log.md#12-library-behaviour)): a field whose alias equals another
field's name can no longer capture that field's value. Before validating, a top-level key that is not a field name
but equals a field's alias, serialization alias or plain-string validation alias (an `AliasChoices` string, a
one-element `AliasPath`) is renamed to that field's name. State that gives one field under two keys, its name and
an alias or two aliases, is ambiguous: it raises a value-free `SnapshotRestoreError` naming the field and the keys
(`ambiguous state for field 'balance' (keys 'balance', 'Balance')`) ([B-78](../others/decision-log.md#12-library-behaviour)). The order of `AliasChoices` is
not used to pick between two keys: any two keys for one field raise.
So aliased fields (a plain alias, a required one, `AliasChoices`, `AliasPath`, `alias_generator`, nested ones) keep
their values, and top-level alias-keyed state written before this change, by `alias` or `serialization_alias`,
still loads. Nested alias-keyed state is not renamed: an old alias key inside a nested model is dropped without an
error, and the nested field takes its default, so an aggregate whose old snapshots hold aliases inside nested
models must bump `_snapshot_schema_version`. Validation builds nested models and lists anew, so
the aggregate shares no mutable object with the snapshot, and a snapshot plus its tail gives the aggregate a full
replay gives.

- **Validators run on the restored state** ([B-53](../others/decision-log.md#12-library-behaviour)). The restore is a plain `model_validate`: the
  aggregate's field and model validators run on every load from a snapshot, on the state the snapshot holds. A
  validator with side effects runs each time; the docs say to keep them free of side effects. Before, they ran
  only on the defaults, inside `aggregate_cls(id=id_)`.
- **Private attributes start from their defaults** ([B-54](../others/decision-log.md#12-library-behaviour)). They are not snapshot state, as before;
  `model_post_init` runs after validation, so an attribute derived there sees the restored fields. On a full
  replay it runs on the empty aggregate instead, so the docs say to derive private attributes in
  `model_post_init` and keep them current in `_when`; an attribute derived only in `model_post_init` depends on
  the load path.
- **Keys of removed fields are ignored** ([B-55](../others/decision-log.md#12-library-behaviour)) under Pydantic's default `extra="ignore"` only. An
  aggregate that sets `extra="forbid"` rejects them like any other invalid state (point 2); unknown keys are not
  stripped.
- **Values are validated, not checked for type.** A value that still validates is coerced as Pydantic's lax mode
  does (a stored `"5"` loads into an `int` field); a value that no longer validates, or a new required field
  without a default, raises (point 2). A strict-mode aggregate (`strict=True`) behind a store that serializes
  snapshots to JSON fails on values JSON does not keep, such as `datetime`, `UUID`, `Enum`, `Decimal`, `tuple` and `set`.

**2. State that no longer validates raises `SnapshotRestoreError` (amends ADR-053)** ([B-55](../others/decision-log.md#12-library-behaviour)). The
repository wraps the `ValidationError`, and any other `Exception` raised while the state is validated, by a model
validator (a `DomainError` included) or by `model_post_init` ([B-67](../others/decision-log.md#12-library-behaviour)), in a new
`SnapshotRestoreError(DomainError)`, exported from `pydomain.es` and `pydomain`. A `BaseException` that is not an
`Exception` propagates. The message is built with `describe()` (ADR-071): the aggregate id, the snapshot
version and, for a `ValidationError`, the failing locations, which include dict keys as ADR-071 allows (B-30),
with their error codes; any other exception by its type name only; never a value:
`Snapshot of <Aggregate> '<id>' at version <n> could not be restored: <description>`. It carries `aggregate_id`
and `snapshot_version`. It is raised after the `except` block, with no cause and no context: chaining it `from`
the `ValidationError`, or raising it inside the `except` block with `from None`, would keep the stored values
in `__cause__` or `__context__` and so in every printed traceback, which the content sentinel showed. A test
runs the full sentinel check (`assert_absent_from_reprs`) on the error. There is no fallback to a full replay:
ADR-053 already rejected detecting drift with a `model_validate()` try/except, and a snapshot that does not fit
is the signal to bump `_snapshot_schema_version` and configure `RejectStaleSnapshotPolicy`, which still skips
stale snapshots before any validation.

**3. Computed fields are not snapshot state (amends ADR-043)** ([B-56](../others/decision-log.md#12-library-behaviour)). `_take_snapshot()` dumps with
`model_dump(by_alias=False, exclude_computed_fields=True)` ([B-66](../others/decision-log.md#12-library-behaviour)): the state is keyed by field names even
when the model serializes by alias, and computed fields are left out at every depth, nested models' included, as
`version` already is. A nested model with a computed field and `extra="forbid"` therefore reloads. A computed field is derived from the
other fields, so the restored aggregate recomputes it. The restore drops top-level computed keys, under their names or aliases, before validating,
so a snapshot taken before this change loads too, even for an aggregate with `extra="forbid"`. The recursive
exclusion holds for new snapshots only: an old snapshot that holds a nested model's computed key, under that
nested model's `extra="forbid"`, raises `SnapshotRestoreError`, and such an aggregate must bump
`_snapshot_schema_version`. This is a behaviour change, not a breaking one, and has an upgrade note.

**4. Snapshot stores keep their snapshots to themselves.** The `SnapshotStore` Protocol states that changing a
snapshot after `save()`, or changing one that `get()` returned, does not change what the store holds. Two
`SnapshotStoreContract` cases pin it (ADR-067). `FakeSnapshotStore` stores a deep copy on `save()` and returns
a deep copy from `get()`, as ADR-072 requires of every shipped fake.

**5. The Pydantic floor rises to 2.12.5 (amends ADR-002)** ([B-66](../others/decision-log.md#12-library-behaviour), [B-75](../others/decision-log.md#12-library-behaviour), [B-76](../others/decision-log.md#12-library-behaviour)).
`exclude_computed_fields` first appears in Pydantic 2.12, `model_validate(by_alias=..., by_name=...)` in 2.11, and
`pyproject.toml` requires `pydantic>=2.12.5`, the latest 2.12 patch, verified by the floor CI job on Python 3.12 and
3.14 (2.12.0 fails on 3.14 and 2.12.1 is yanked; 2.12.2 to 2.12.4 also pass, but the floor stays at 2.12.5). The `uuid-utils` floor rises to 0.10, the first release with wheels for Python 3.14
([B-77](../others/decision-log.md#12-library-behaviour)). A CI job runs the suite at the declared floors of the runtime dependencies on Python 3.12 and 3.14. The raise is a
behaviour change, not a breaking one, and leads the upgrade notes.

**6. The docs say what the code does.** `handle-es-errors.md` §5 says a snapshot is used as is unless a
`SnapshotSchemaPolicy` rejects it (ADR-053), and the `pending_event_count` docstring says it is the number of
events in the batch just appended.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Keep `setattr` and turn on `validate_assignment` for the restore | Validates one field at a time against a half-restored aggregate, still cannot assign a computed field, and changes assignment semantics for the whole model |
| Keep `setattr` and deep-copy the snapshot first | Fixes ES-09 only; nested models still come back as dicts and computed fields still break the load |
| `model_construct(**state)` | Skips validation, so nested models stay dicts (ES-08 unchanged) |
| Fix only `FakeSnapshotStore` | Hides ES-09 in tests while any caching adapter keeps the defect; the repository must not depend on the store copying |
| A restore context flag that validators can test | A new API surface for a case validators without side effects do not need. Rejected by the author ([B-53](../others/decision-log.md#12-library-behaviour)) |
| Validate fields only, without the model validators (as `stamp()` does, ADR-070) | Needs a per-field validator per aggregate class and leaves invariants unchecked on the restored state. Rejected by the author ([B-53](../others/decision-log.md#12-library-behaviour)) |
| Store private attributes in the snapshot | Changes the snapshot format and would capture `_pending_events` (ADR-050). Rejected by the author ([B-54](../others/decision-log.md#12-library-behaviour)) |
| Chain the error `from` the `ValidationError`, or raise it `from None` inside the `except` block | The cause, or the implicit context, keeps the stored values reachable and prints them in tracebacks. Rejected by the author ([B-55](../others/decision-log.md#12-library-behaviour)) |
| Strip every key the aggregate class does not know before validating | Overrides an aggregate's own `extra="forbid"`, which asks for exactly that rejection. Rejected by the author ([B-55](../others/decision-log.md#12-library-behaviour)) |
| Let the raw `ValidationError` reach the caller | Its message quotes the stored values (ADR-071). Rejected by the author ([B-55](../others/decision-log.md#12-library-behaviour)) |
| Fall back to a full replay when the state does not validate | Hides a schema change that `_snapshot_schema_version` should record, and repeats the replay on every load; ADR-053 rejected it. Rejected by the author ([B-55](../others/decision-log.md#12-library-behaviour)) |
| Keep computed keys in old snapshots and let validation decide | An aggregate with `extra="forbid"` could never load a snapshot taken before this change. Rejected by the author ([B-56](../others/decision-log.md#12-library-behaviour)) |
| Treat leaving out computed fields as breaking | Such snapshots could never be loaded before, so no working code reads them back. Rejected by the author ([B-56](../others/decision-log.md#12-library-behaviour)) |
| Reject classes whose aliases collide with field names when they are built | Refuses valid Pydantic models. Rejected by the author ([B-74](../others/decision-log.md#12-library-behaviour)) |
| Document the alias collision only | Leaves silent data loss in place. Rejected by the author ([B-74](../others/decision-log.md#12-library-behaviour)) |
| Reject state keyed by serialization aliases | Old snapshots of `serialize_by_alias` models would fail to load. Rejected by the author ([B-74](../others/decision-log.md#12-library-behaviour)) |
| Keep the field-name value when an alias key gives the same field | Hides which of two stored values was meant. Rejected by the author ([B-78](../others/decision-log.md#12-library-behaviour)) |
| Pin only Pydantic on 3.14 in the floor job, or run the floor job on 3.12 only | Leaves the declared `uuid-utils` floor untested where it cannot even install. Rejected by the author ([B-77](../others/decision-log.md#12-library-behaviour)) |
| Keep `pydantic>=2.12`, or `>=2.12.5` without a CI job at the floor | 2.12.0 fails on Python 3.14, and an untested floor drifts. Rejected by the author ([B-75](../others/decision-log.md#12-library-behaviour)) |
| Lower the floor to `pydantic>=2.12.2`, the oldest 2.12 patch that passes on 3.14 | The floor stays at the latest 2.12 patch, which the floor job verifies. Rejected by the author ([B-75](../others/decision-log.md#12-library-behaviour)) |
| Treat the floor raise as breaking (`!`, 0.4.0) | No API changes; projects on a recent Pydantic are unaffected. Rejected by the author ([B-76](../others/decision-log.md#12-library-behaviour)) |
| Keep `pydantic>=2.7` and map aliases and nested computed fields by hand | Re-implements what Pydantic 2.12 does, for every nesting shape. Rejected by the author ([B-66](../others/decision-log.md#12-library-behaviour)) |
| Refuse aggregates with aliases or nested computed fields | Rules out common models. Rejected by the author ([B-66](../others/decision-log.md#12-library-behaviour)) |
| Wrap only `DomainError` and `ValueError` from the aggregate's code | Any other exception would still escape with its message, which may quote stored values. Rejected by the author ([B-67](../others/decision-log.md#12-library-behaviour)) |
| Document that the aggregate's own exceptions escape | Leaves the value leak in place. Rejected by the author ([B-67](../others/decision-log.md#12-library-behaviour)) |
| Snapshot with `model_dump(mode="json")` | Changes what every store receives and loses Python types the aggregate's validators would have to parse back; the restore change alone fixes ES-08 |

## Consequences

### Positive

- An aggregate loaded from a snapshot equals one built by full replay, nested models included.
- Aggregates with computed fields can use snapshots.
- Loading never changes a stored snapshot, whatever the store does about copies.

### Negative

- Restoring validates every field, which costs more than assigning it.
- Snapshot state that no longer validates now raises `SnapshotRestoreError` where `setattr` accepted it, and a
  removed field's key is ignored where `setattr` raised `ValueError`.
- Validators with side effects run on every load from a snapshot.
- Adopters on Pydantic 2.7 to 2.12.4 must upgrade Pydantic.
- Old snapshots with aliases or computed keys inside nested models need a `_snapshot_schema_version` bump.
- A private attribute derived only in `model_post_init` differs between a snapshot load and a full replay unless
  `_when` keeps it current.

### Neutral

- Snapshots of aggregates without computed fields are stored exactly as before.
- Private attributes start from their defaults, as before.
- `SnapshotThresholdPolicy` and `SnapshotSchemaPolicy` are unchanged.

## References

- `src/pydomain/es/event_sourced_repository.py`, `src/pydomain/es/aggregate.py`, `src/pydomain/es/snapshot.py`,
  `src/pydomain/testing/fake_snapshot_store.py`, `src/pydomain/testing/contracts/snapshot_store.py`
- `tests/es/test_snapshot_restore.py`, `tests/testing/test_contracts_snapshot_store.py`
- `docs/others/bug-audit-findings.md` and `docs/others/bug-audit-plan.md`: rows ES-08, ES-09, A24 and A36
- `docs/others/bug-fix-plan.md` §8, W1-4
- [Decision log](../others/decision-log.md#12-library-behaviour): B-53, B-54, B-55, B-56, B-66, B-67, B-74, B-75, B-76, B-77 and B-78
- ADR-002: Pydantic v2 Only (amended: the floor)
- ADR-043: Snapshot Policy as Pluggable Protocol (amended)
- ADR-053: Snapshot Schema Version Policy (amended)
- ADR-066: Test-First Fixes with Documentation in the Same Change
- ADR-067: Protocol Contract Suites
- ADR-072: Shipped Fakes Conform to Their Protocol Contracts
- YouTrack DCE-119
