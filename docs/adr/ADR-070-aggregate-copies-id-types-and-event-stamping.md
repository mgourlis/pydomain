# ADR-070: Aggregate Copies, Identity Types, Event Stamping and Composite Specifications

## Status

Accepted

## Date

2026-10-21

## Context

The 0.3.0 correctness audit (`docs/others/bug-audit-findings.md`, `docs/others/bug-audit-plan.md` §12) confirmed
eight defects in the `pydomain.ddd` building blocks, in `Command` and in `Query`. They are small and local, but four of them
contradict an Accepted ADR or a documented contract:

1. **Copies share the event buffer (DDD-01).** `AggregateRoot._pending_events` is a `PrivateAttr` (ADR-050).
   Pydantic's shallow copy (`model_copy()`, `copy.copy`) copies the private-attribute dictionary but not the list
   in it, so the copy and the original hold the same list. An event recorded on the copy appears on the original,
   and the same event can be published twice. ADR-050 did not say what a copy owns.
2. **The id guard rejects valid id types (DDD-03, DDD-04).** ADR-007 promises auto-generation for every `TId`,
   guarded by a `DomainError` on a type mismatch. The guard passes the field annotation straight to `isinstance`,
   so `Entity[OrderId]` with `OrderId = NewType("OrderId", UUID)`, or `Entity[InvoiceId]` with
   `type InvoiceId = UUID` (PEP 695), raises a raw `TypeError` even when the generator is correct. So do
   `Annotated` ids, aliases nested inside unions, and the unbound `TypeVar` of a generic entity built without a
   type argument. The branch for `origin is type(None)` can never be true and is dead.
3. **The guard writes into the caller's dict (DDD-05).** `Entity.model_validate(d)` adds `id` to `d`, so
   validating the same dict again silently reuses the first id.
4. **Per-subclass generators are ignored by events, commands and queries (DDD-09).** arc42 §8.11 says
   `Entity`, `DomainEvent` and `Command` each keep an independent `_id_generator` that a subclass can override,
   as `Entity` does; `Query` has the same class variable. The `event_id`, `command_id` and `query_id` default
   factories read `DomainEvent._id_generator`, `Command._id_generator` and `Query._id_generator` by name, so an
   override on a subclass has no effect.
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

**Scope of the copy decision.** This ADR decides that an aggregate's event buffer is never shared between copies
and that a copy keeps the events pending when it was made. What pickling, `model_validate(model_dump())` and
re-emission do with pending events (DDD-02) stays with cluster W2-9 of the fix plan.

**1. Every copy of an aggregate owns its event buffer (amends ADR-050).** `AggregateRoot.__copy__` gives the
copy a new list holding the events pending at the moment of the copy, so `model_copy()` and `copy.copy` behave
like `model_copy(deep=True)` and `copy.deepcopy` already do. After the copy, an event recorded or pulled on one
side never shows on the other. Shallow copies share the (frozen) event objects; deep copies hold copies of them.

**2. The id guard resolves the id type fully (amends ADR-007).** Before the `isinstance` check, the guard
resolves the `TId` annotation recursively: it follows `NewType.__supertype__` and `TypeAliasType.__value__`,
strips `Annotated`, and flattens unions at any depth, so `Optional[X]` with `type X = Y | int` and
`type Y = UUID` checks against `UUID`, `int` and `None`, and `Annotated[UUID, ...] | None` against `UUID` and
`None`. A parametrised generic is checked against its origin (`tuple[int, int]` against `tuple`). `Any`
accepts every value, as it does in Pydantic: the guard checks nothing for such an entity
([B-18](../others/decision-log.md#12-library-behaviour)). A correct generator then produces an id, and a wrong one raises `DomainError`, as ADR-007
promises. An id type that still cannot be checked raises `DomainError` whose advice fits the type: for the unbound
`TypeVar` of a generic entity built without a type argument (a `Holder` that subclasses `Entity[T]`, built as
`Holder()`, or a bare `Entity()`) it says to parametrize the generic or pass an id; for any other type, such as
`Literal["a"]`, it says the id type cannot be checked and an explicit id is needed. A union keeps `None` as a
member, so `Entity[UUID | None]` accepts a generator that returns `None`, as its annotation allows. The dead
`type(None)` branch is removed. DDD-03 is fixed in full ([B-13](../others/decision-log.md#12-library-behaviour)).

**3. The guard never mutates its input (amends ADR-007).** `_ensure_id` returns a new dict with `id` added; the
caller's dict is unchanged.

**4. `DomainEvent`, `Command` and `Query` honour a per-subclass `_id_generator` (restores arc42 §8.11).** The
hook is the existing `_id_generator` class variable, the same one `Entity` uses. When the model is validated
(the constructor, `model_validate()`, `model_validate_json()`), a `model_validator(mode="before")` fills a
missing `event_id`, `command_id` or `query_id` from `cls._id_generator`, without mutating the input. The
generated id is then part of `model_fields_set`, so `model_dump(exclude_unset=True)` includes it.
`model_construct()` skips validation, so its id comes from the field's default factory, which uses the base
class's generator; the factory stays so that `model_construct()` and the JSON schema keep treating the id as
optional. `configure()` is unchanged: it still sets the base class's generator, which every subclass without an
override uses. `Query` is included in the same change ([B-11](../others/decision-log.md#12-library-behaviour)).

**5. `stamp()` validates the two ids alone and returns an isolated copy (amends ADR-011).** `stamp()` validates
`correlation_id` and `causation_id` one by one, each with a validator built once per event class from that
field's annotation and constraints (a `TypeAdapter` over the field). A UUID string is converted to a `UUID`, as
construction converts it, a constraint declared on the field (an `Annotated` validator, a `Field` constraint)
applies, and any other value raises `pydantic.ValidationError` ([B-9](../others/decision-log.md#12-library-behaviour)). It then returns
`model_copy(update={...}, deep=True)`, so the stamped copy shares no mutable payload with the original, which
restores ADR-011's guarantee that the original is never modified. **No model validator of the event runs**, as
in 0.3.0: an invariant such as "both tracing ids or neither" never sees a half-stamped event
([B-12](../others/decision-log.md#12-library-behaviour)). Only the field's type and constraints apply: a `@field_validator` method that a subclass declares for
`correlation_id` or `causation_id` is model-level and does not run in `stamp()`.

**6. Composite specifications evaluate iteratively.** `is_satisfied_by()` and `subsumes()` on `AndSpecification`,
`OrSpecification` and `NotSpecification` walk the tree with an explicit stack, so depth is bounded by memory, not
by the recursion limit. Each composite method starts the walk from its own operator and children, and only its
children are checked for an override: a child that is a composite using the library's method is expanded in
place, any other child is called as written. A composite subclass whose override delegates to `super()` therefore
works as the root and nested. The semantics stay as in 0.3.0: `and` and `or` short-circuit left to right, `not`
negates, `subsumes()` of `And` requires every child and of `Or` any child, and `Not.subsumes()` is `False`. A
composite with no children (only possible through `model_construct()`) follows `all(())` and `any(())`: an
empty `And` holds and subsumes, an empty `Or` does neither. A `NotSpecification` without its child has no defined
value, so evaluating it raises a `ValueError` that names the missing child, as the constructor rejects a missing
child with Pydantic's `ValidationError` (a `ValueError`).

**7. Composite specifications dump their children; there is no round trip in 0.3.1 (amends ADR-010).** The
children of `AndSpecification`, `OrSpecification` and `NotSpecification` are typed `SerializeAsAny[Specification]`,
so `model_dump()` and `model_dump_json()` contain each child's own fields, nested:
`{"specifications": [{"threshold": 1}, {"specification": {"threshold": 10}}]}`. A dump carries no type name, so
it cannot be validated back into the leaf types. A type-tagged round trip is deferred to a Wave 2 Feature, backlog
item [F-1](../others/decision-log.md#2-feature-backlog) ("JSON to Specification") in the decision log, following
[B-10](../others/decision-log.md#12-library-behaviour).

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| A copy starts with an empty buffer | The events pending at copy time would be silently dropped from the copy; a copy taken to try an operation would lose the events of the operation it copied. "Own copy of the buffer" matches what deep copies already do |
| Forbid shallow copies of aggregates (`__copy__` raises) | Breaks `model_copy()`, a documented Pydantic API that adopters and tests use; the defect is shared state, not copying |
| Resolve every annotation with a `TypeAdapter(...).validate_python(generated, strict=True)` instead of `isinstance` | Builds and caches a validator per class for a check that only needs the runtime type; strict mode also differs from `isinstance` on edge cases (`bool` for `int`), changing which generators pass |
| Unwrap only the top-level `NewType` or alias and leave nested forms to `isinstance` | Still raises a raw `TypeError` for `Optional[X]` over nested aliases and for `Annotated` inside a union, so DDD-03 stays half fixed |
| Check an unbound `TypeVar` id against the `TypeVar`'s bound, or skip the check | A separate design question (what an unconstrained `TypeVar` should accept); recorded as backlog item [F-5](../others/decision-log.md#2-feature-backlog). Until then the guard says what to do instead of raising `TypeError` |
| An `Any` id raises `DomainError`, like an unbound generic | `Any` is a deliberate "every value" annotation, which Pydantic accepts as such; an entity declared `Entity[Any]` has said it wants no check. Rejected by the author ([B-18](../others/decision-log.md#12-library-behaviour)) |
| An empty `NotSpecification` evaluates to `True` or `False` | `not` of nothing has no value that the Boolean laws fix, unlike `all(())` and `any(())`; picking one would hide a construction mistake |
| Per-subclass generators through `__pydantic_init_subclass__` rewriting each subclass's default factory | Needs a schema rebuild per subclass and still has to look the generator up lazily for `configure()`; the before-validator is the mechanism `Entity` already uses (ADR-007) |
| Drop the `default_factory` so only the before-validator fills the id | `model_construct()` would leave the id unset and the JSON schema would mark it required; the factory stays, and `model_construct()` is documented to use the base class's generator |
| Leave DDD-03 open (only the top-level `NewType` and alias fixed) | DDD-03 would stay half fixed, with a raw `TypeError` for nested and `Annotated` ids and for unbound generics. Rejected by the author |
| Leave `Query` out of the per-subclass generator fix | `Query` has the same `_id_generator` class variable and the same defect; leaving it out would keep the four base types inconsistent. Rejected by the author |
| Keep `stamp()` on `model_copy(update=...)` and document that it does not validate | Leaves ADR-011's "the original is never modified" false and lets `stamp()` build events that fail to deserialize |
| `stamp()` validates through `validate_assignment` with both ids set first | Still runs the event's model validators, twice. Rejected by the author |
| `stamp()` validates through `validate_assignment` on the copy | Runs the event's model validators once per id, twice in all, and the first run sees a half-stamped event, so a "both ids or neither" invariant rejects a correct stamp |
| `stamp()` rebuilds the event with `model_validate({**self.model_dump(), ...})` | Re-validates every field and runs every model validator, breaks on aliases, excluded fields and computed fields, and can generate a new `event_id` through the before-validator if the dump omits it |
| `stamp()` raises `TypeError` after a strict `isinstance(..., UUID)` check | Differs from construction: `DomainEvent(correlation_id="<uuid string>")` is accepted and converted, so the same value would pass one path and fail the other; it would also skip the field's constraints |
| `stamp()` raises `DomainError` | A non-UUID tracing id is a programming error in the caller, not a broken domain rule; ADR-004 keeps `DomainError` for the latter |
| Composite methods detect overrides on themselves (`_evaluate(self, ...)`) | A subclass override that delegates to `super()` is detected as a leaf, called again, and recurses forever |
| Type-tagged composite dumps with a registry of leaf types now | A new public API (tag format, registration, unknown-type handling) that needs its own design; it is deferred to backlog item F-1 rather than decided inside a fix |
| Adopters declare a discriminated union of their leaf types for the children | The children are typed as the base class in the library, so an adopter cannot change their annotation without subclassing every composite |
| Leave composite dumps as they are | The dump silently drops every child's fields, so it is useless for logging or storing a rule, and the concept page called specifications serializable |
| Raise the recursion limit, or flatten `a.and_(b).and_(c)` into one `AndSpecification` | Raising the limit moves the failure and can crash the interpreter; flattening changes the structure, equality and dump of existing specifications and does not help mixed `and`/`or`/`not` trees |

## Consequences

### Positive

- An aggregate copy can no longer publish, or receive, another copy's events.
- `NewType`, PEP 695 alias, `Annotated` and nested union ids, which ADR-007 always meant to support, auto-generate,
  and every id type the guard cannot check gives a `DomainError` that says what to do.
- Validating a dict twice yields two entities with different ids.
- A per-subclass event, command or query id generator works the way the docs describe and `Entity` already behaves.
- A stamped event cannot change the event it was copied from, and its ids are valid UUIDs.
- Specification chains of any practical depth evaluate, including composite subclasses that delegate to `super()`.

### Negative

- Both a copy and its original still hold the events pending at copy time; saving both publishes those events
  twice. Copy an aggregate after its events are pulled, or save only one of the two.
- `stamp()` now raises `ValidationError` for a non-UUID id that it used to accept.
- `stamp()` deep-copies each event, which costs more than the shallow copy for large payloads (typically one to
  five events per command).
- Event, command and query construction runs one more before-validator, and the generated id now counts as set.
- `model_construct()` ignores a per-subclass generator.
- A composite dump cannot be loaded back; adopters who store specifications must rebuild them in code until F-1
  lands. Dumps of composites nested more than a few hundred levels deep raise `PydanticSerializationError` from
  Pydantic's serializer, although evaluation works at any depth.
- An entity that stayed generic over its id type and was built without an id now raises `DomainError` instead of
  `TypeError`.

### Neutral

- `Entity`, `DomainEvent`, `Command` and `Query` now fill their ids the same way.
- The iterative evaluator is internal; the composites' public methods and fields are unchanged.
- `stamp()` runs no model validator, as in 0.3.0; only the ids gain the field's validation.
- Checking an unbound `TypeVar` id against its bound, instead of raising, is backlog item
  [F-5](../others/decision-log.md#2-feature-backlog).
- An `Entity[Any]` id is not checked at all ([B-18](../others/decision-log.md#12-library-behaviour)).

## References

- `src/pydomain/ddd/aggregate_root.py`, `src/pydomain/ddd/entity.py`, `src/pydomain/ddd/domain_event.py`,
  `src/pydomain/cqrs/commands.py`, `src/pydomain/cqrs/queries.py`, `src/pydomain/ddd/specification.py`
- `tests/ddd/test_aggregate_root.py`, `tests/ddd/test_entity.py`, `tests/ddd/test_domain_event.py`,
  `tests/cqrs/test_commands.py`, `tests/cqrs/test_queries.py`, `tests/ddd/test_specification.py`
- `docs/others/bug-audit-findings.md` and `docs/others/bug-audit-plan.md`: rows DDD-01, DDD-03, DDD-04, DDD-05,
  DDD-09, DDD-10, DDD-14, DDD-15 and A22
- `docs/others/bug-fix-plan.md` §8, W1-10, and §9.1, W2-9 (DDD-02)
- [Decision log](../others/decision-log.md): B-9, B-10, B-11, B-12, B-13 and B-18; backlog F-1 and F-5
- ADR-007: Entity Auto-ID with Runtime Type Guard (amended)
- ADR-010: Specification ABC+BaseModel Hybrid (amended)
- ADR-011: DomainEvent `stamp()` Preserves Immutability (amended)
- ADR-050: AggregateRoot `_pending_events` as `PrivateAttr` (amended)
- ADR-066: Test-First Fixes with Documentation in the Same Change
- YouTrack DCE-125
