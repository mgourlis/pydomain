# ADR-070: Aggregate Copies, Identity Types, Event Stamping and Composite Specifications

## Status

Proposed

## Date

2026-10-21

## Context

The 0.3.0 correctness audit (`docs/others/bug-audit-findings.md`, `docs/others/bug-audit-plan.md` §12) confirmed
eight defects in the `pydomain.ddd` building blocks and in `Command`. They are small and local, but four of them
contradict an Accepted ADR or a documented contract:

1. **Copies share the event buffer (DDD-01).** `AggregateRoot._pending_events` is a `PrivateAttr` (ADR-050).
   Pydantic's shallow copy (`model_copy()`, `copy.copy`) copies the private-attribute dictionary but not the list
   in it, so the copy and the original hold the same list. An event recorded on the copy appears on the original,
   and the same event can be published twice. ADR-050 did not say what a copy owns.
2. **The id guard rejects valid id types (DDD-03, DDD-04).** ADR-007 promises auto-generation for every `TId`,
   guarded by a `DomainError` on a type mismatch. The guard passes the field annotation straight to `isinstance`,
   so `Entity[OrderId]` with `OrderId = NewType("OrderId", UUID)`, or `Entity[InvoiceId]` with
   `type InvoiceId = UUID` (PEP 695), raises a raw `TypeError` even when the generator is correct. The branch for
   `origin is type(None)` can never be true and is dead.
3. **The guard writes into the caller's dict (DDD-05).** `Entity.model_validate(d)` adds `id` to `d`, so
   validating the same dict again silently reuses the first id.
4. **Per-subclass generators are ignored by events and commands (DDD-09).** arc42 §8.11 says `Entity`,
   `DomainEvent` and `Command` each keep an independent `_id_generator` that a subclass can override, as
   `Entity` does. The `event_id` and `command_id` default factories read `DomainEvent._id_generator` and
   `Command._id_generator` by name, so an override on a subclass has no effect.
5. **`stamp()` neither validates nor isolates (DDD-10).** ADR-011 says `stamp()` returns a new frozen copy and
   that the original is never modified. It uses `model_copy(update=...)`, which skips validation: `stamp(
   correlation_id="x", causation_id=123)` returns an event whose dump no longer validates. The copy is shallow,
   so a list in the payload is shared, and appending to the stamped copy's list changes the original.
6. **Deep composite specifications overflow the stack (DDD-15).** `AndSpecification`, `OrSpecification` and
   `NotSpecification` evaluate their children recursively, so a chain of 1,000 `and_()` calls raises
   `RecursionError`. Subsumption itself is sound (1,600 pairs checked) and stays as it is.
7. **Composite specifications do not serialize (DDD-14).** `concepts/ddd/specifications.md` calls
   specifications serializable. A composite's `model_dump()` drops its children's fields (the children are
   typed as the base `Specification`), and nothing can rebuild a leaf from a dump.
8. **The concept page teaches a false subsumption result (A22).** `concepts/ddd/specifications.md` asserts
   `adult.subsumes(senior) is True` for two specifications that do not override `subsumes()`; the default is
   `False`.

## Decision

We will fix the defects as one cluster and amend the four ADRs whose text they change.

**1. Every copy of an aggregate owns its event buffer (amends ADR-050).** `AggregateRoot.__copy__` gives the
copy a new list holding the events pending at the moment of the copy, so `model_copy()` and `copy.copy` behave
like `model_copy(deep=True)` and `copy.deepcopy` already do. After the copy, an event recorded or pulled on one
side never shows on the other. Shallow copies share the (frozen) event objects; deep copies hold copies of them.

**2. The id guard unwraps `NewType` and PEP 695 aliases (amends ADR-007).** Before the `isinstance` check, the
guard follows `NewType.__supertype__` and `TypeAliasType.__value__` until it reaches a runtime type, for the
annotation itself and for each member of a union. A correct generator then produces an id, and a wrong one raises
`DomainError`, as ADR-007 promises. The dead `type(None)` branch is removed.

**3. The guard never mutates its input (amends ADR-007).** `_ensure_id` returns a new dict with `id` added; the
caller's dict is unchanged.

**4. `DomainEvent` and `Command` honour a per-subclass `_id_generator` (restores arc42 §8.11).** The hook is the
existing `_id_generator` class variable, the same one `Entity` uses. A `model_validator(mode="before")` fills a
missing `event_id` or `command_id` from `cls._id_generator`, without mutating the input. `configure()` is
unchanged: it still sets the base class's generator, which every subclass without an override uses.

**5. `stamp()` returns an isolated copy that validates its arguments (amends ADR-011).** The stamped copy is a
deep copy, so it shares no mutable payload with the original, which restores ADR-011's guarantee that the
original is never modified. `stamp()` rejects a `correlation_id` or `causation_id` that is not a `UUID`.
*Open:* which exception it raises and whether it coerces UUID strings (see Open questions).

**6. Composite specifications evaluate iteratively.** `is_satisfied_by()` and `subsumes()` on `AndSpecification`,
`OrSpecification` and `NotSpecification` walk the tree with an explicit stack, so depth is bounded by memory, not
by the recursion limit. They keep today's semantics: `and` and `or` short-circuit left to right, `not` negates,
`subsumes()` of `And` requires every child and of `Or` any child, and `Not.subsumes()` is `False`. A subclass of a
composite that overrides one of these methods is called as written.

**7. Composite specifications serialize (amends ADR-010).** *Open:* the format and how a leaf is rebuilt (see
Open questions).

### Open questions (to settle before this ADR is Accepted)

- **Q1, decision 5:** what `stamp()` raises for a non-UUID id. Options: Pydantic `ValidationError` (the
  constructor's behaviour; coerces UUID strings), `TypeError` (strict `isinstance`, no coercion), or
  `DomainError` (ADR-004 hierarchy).
- **Q2, decision 7:** the serialized form of a composite and how a leaf specification is reconstructed (a type
  tag plus a registry, a discriminated union the adopter declares, or "dump only, no round trip").

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| A copy starts with an empty buffer | The events pending at copy time would be silently dropped from the copy; a copy taken to try an operation would lose the events of the operation it copied. "Own copy of the buffer" matches what deep copies already do |
| Forbid shallow copies of aggregates (`__copy__` raises) | Breaks `model_copy()`, a documented Pydantic API that adopters and tests use; the defect is shared state, not copying |
| Resolve every annotation with a `TypeAdapter(...).validate_python(generated, strict=True)` instead of `isinstance` | Builds and caches a validator per class for a check that only needs the runtime type; strict mode also differs from `isinstance` on edge cases (`bool` for `int`), changing which generators pass |
| Per-subclass generators through `__pydantic_init_subclass__` rewriting each subclass's default factory | Needs a schema rebuild per subclass and still has to look the generator up lazily for `configure()`; the before-validator is the mechanism `Entity` already uses (ADR-007) |
| Keep `stamp()` on `model_copy(update=...)` and document that it does not validate | Leaves ADR-011's "the original is never modified" false and lets `stamp()` build events that fail to deserialize |
| Raise the recursion limit, or flatten `a.and_(b).and_(c)` into one `AndSpecification` | Raising the limit moves the failure and can crash the interpreter; flattening changes the structure, equality and dump of existing specifications and does not help mixed `and`/`or`/`not` trees |

## Consequences

### Positive

- An aggregate copy can no longer publish, or receive, another copy's events.
- `NewType` and PEP 695 alias ids, which ADR-007 always meant to support, auto-generate.
- Validating a dict twice yields two entities with different ids.
- A per-subclass event or command id generator works the way the docs describe and `Entity` already behaves.
- A stamped event cannot change the event it was copied from.
- Specification chains of any practical depth evaluate.

### Negative

- Both a copy and its original still hold the events pending at copy time; saving both publishes those events
  twice. Copy an aggregate after its events are pulled, or save only one of the two.
- `stamp()` deep-copies each event, which costs more than the shallow copy for large payloads (typically one to
  five events per command).
- Event and command construction runs one more before-validator.

### Neutral

- `Entity`, `DomainEvent` and `Command` now fill their ids the same way.
- The iterative evaluator is internal; the composites' public methods and fields are unchanged.
- `Query` has the same per-subclass generator defect but is outside this cluster.

## References

- `src/pydomain/ddd/aggregate_root.py`, `src/pydomain/ddd/entity.py`, `src/pydomain/ddd/domain_event.py`,
  `src/pydomain/cqrs/commands.py`, `src/pydomain/ddd/specification.py`
- `tests/ddd/test_aggregate_root.py`, `tests/ddd/test_entity.py`, `tests/ddd/test_domain_event.py`,
  `tests/cqrs/test_commands.py`, `tests/ddd/test_specification.py`
- `docs/others/bug-audit-findings.md` and `docs/others/bug-audit-plan.md`: rows DDD-01, DDD-03, DDD-04, DDD-05,
  DDD-09, DDD-10, DDD-14, DDD-15 and A22
- `docs/others/bug-fix-plan.md` §8, W1-10
- ADR-007: Entity Auto-ID with Runtime Type Guard (amended)
- ADR-010: Specification ABC+BaseModel Hybrid (amended)
- ADR-011: DomainEvent `stamp()` Preserves Immutability (amended)
- ADR-050: AggregateRoot `_pending_events` as `PrivateAttr` (amended)
- ADR-066: Test-First Fixes with Documentation in the Same Change
- YouTrack DCE-125
