# ADR-069: Upcast Events to the Class Version Exactly Once

## Status

Accepted. Amends [ADR-042](ADR-042-event-upcaster-chain-cycle-detection.md); notes
[ADR-044](ADR-044-dynamic-event-registry-generic-fallback.md).

## Date

2026-10-21

## Context

ADR-042 decided how `UpcasterRegistry` resolves a chain: follow `source_version → target_version` hops
until no upcaster is registered, and raise `UpcastError` on a cycle. ADR-044 decided that
`EventRegistry.deserialize` applies that chain before `model_validate`, and that an unregistered type falls
back to `GenericDomainEvent`. The contract both serve is in
[Implement an Upcaster](../diataxis/how-to/event-sourcing/implement-upcaster.md): "Application code only
ever sees the latest event format."

The 2026-10 audit (`docs/others/bug-audit-findings.md`, rows INF-14, ES-15 and ES-14, and docs row A30) found
four ways the code breaks that contract:

1. **The version is never updated after the chain.** The chain transforms the payload but leaves its
   `event_version` at the stored value. The upcaster examples in the docs never set it either (A30). The
   event comes back with the stale version, so serializing it again writes the old version, and the next
   read runs the chain a second time. A non-idempotent upcaster (a unit change, euros to cents) turns 50 into
   500 without an error.
2. **A gap in the chain is invisible.** "Stop when no upcaster is registered" cannot tell "already current"
   from "a step is missing". With upcasters 1 → 2 and a class at version 3, a v1 payload is upcast to v2 and
   validated against the v3 class. Defaults hide the missing step.
3. **`except KeyError` is too wide.** It wraps `resolve()`, the upcaster chain and `model_validate`, so a
   `KeyError` from a validator or from an `upcast()` override is logged as "unregistered" and the event is
   returned as a `GenericDomainEvent`.
4. **`UpcasterRegistry.register` checks too little.** It overwrites an upcaster registered for the same
   `(source_type, source_version)`, accepts an upcaster without `target_version`, and accepts a downgrade
   (`target_version` not greater than `source_version`).

## Decision

We will make the upcaster chain end at the class's version, and make both registries refuse input that
would break that.

**1. `deserialize` stamps the version the chain reached.** When an `UpcasterRegistry` is attached,
`EventRegistry.deserialize` tracks the version through the chain: the stored version, then each upcaster's
`target_version`. Before `model_validate`, it writes that version to the payload's `event_version`, when the
class has an `event_version` field. Upcasters no longer need to set it, and one that does is overwritten
with the same value. Serializing the result writes the current version, so the next read runs no upcaster.

**2. The chain must reach the class's version.** When an `UpcasterRegistry` is attached and the chain from
a stored version at or below the class's version does not end at the class's version, `deserialize` raises
`UpcastError` naming the event type and both versions, never payload values. This covers a gap (the chain
stops short) and a chain that overshoots the class, which is a registration mistake. The class's version is
the default of its `event_version` field (a `default_factory` is called); a model without the field is at
version 1, the version `deserialize` assumes for an envelope without one. The check is in `EventRegistry`,
because only it knows the target class; `UpcasterRegistry.resolve` is unchanged.

**2a. A newer stored version is validated unchanged.** An envelope whose version is above the class's was
written by newer code, for example during a rolling deploy or when an older consumer reads a newer
producer's events. `deserialize` runs no upcaster on it, raises nothing, and keeps the payload's own
`event_version`; a payload without one gets the envelope's version, so serializing the event does not relabel
it with the older class's default. Validation decides, as before this ADR. The precedent is
`hydrate_command()`, which strips unknown keys from saga commands written by a newer schema (arc42 §8.10 and
§9.10). Decided by the author on 2026-10-21.

> **Amended by [ADR-078](ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md) decision 3.** The payload's own `event_version` is kept only when it agrees with the
> envelope's version; when they disagree, `deserialize` raises `UpcastError` naming the type only.

**2b. The versions must be well-formed.** With an `UpcasterRegistry` attached, an envelope `"version"`
that is a string of ASCII digits (`"2"`) is read as that `int`, so adapters that store the version as text
upcast correctly. Any other value that is not an `int` (`None`, a `bool`, a float, or other text such as
`"-1"`, `" 2"`, `"2.0"` or `"v2"`) makes `deserialize` raise `UpcastError` naming the type, never the
value. A class that declares `event_version` without a default, or with a default that is not an `int`,
also raises `UpcastError`: it has no version to upcast to. Give the field a default, as `DomainEvent` does;
a `default_factory` counts unless it needs the validated data. The coercion of integer strings was decided
by the author on 2026-10-21.

> **Amended by [ADR-078](ADR-078-cleanup-errors-stored-versions-and-per-class-configure.md) decision 3.** A digit string too long for `int()` also raises `UpcastError`, and the
> payload's `event_version`, when present, is read by the same rule and must agree with the envelope's
> version (v1 for an envelope without one).

**3. Only `resolve()` decides "unregistered".** `deserialize` catches `KeyError` around `resolve()` alone.
A `KeyError` from an upcaster or a validator propagates to the caller. When an upcaster returns something
other than a dict, `deserialize` raises an `UpcastError` that names the upcaster and the returned type; it
checks every hop, so an `upcast()` override is covered too. `EventUpcaster.upcast` makes the same check for
direct callers.

**4. `UpcasterRegistry.register` validates its input.** It raises `TypeError` when the upcaster lacks
`source_type`, `source_version` or `target_version`, `ValueError` when `target_version` is not greater than
`source_version`, and `ValueError` when an upcaster is already registered for the same
`(source_type, source_version)`. The exception types follow the existing `TypeError` for missing class
variables and `EventRegistry.register`'s `ValueError` for a duplicate (ADR-044). Version jumps are
allowed: an upcaster may go from v1 to v3.

**5. The cycle guard stays.** With downgrades refused, `register` can no longer build a cycle. A cycle is
still possible if a class variable is changed after registration, so ADR-042's guard remains as defence in
depth.

Without an `UpcasterRegistry`, `deserialize` is unchanged apart from point 3: upcasting stays opt-in
(arc42 §8.8), and a registry with no upcasters is how an application opts into the version check.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Leave the version to the upcaster: document that `_transform` must set `event_version` | The docs taught the opposite since the pages were written (A30), and the failure is silent. The registry already knows the target version of every hop, so asking each upcaster to repeat it adds a way to get it wrong and nothing else |
| Check the version in `UpcasterRegistry.resolve` with a `target_version` argument | `resolve` does not know the event class; every caller would have to pass the right target. `deserialize` is the one place that has both the chain and the class |
| Raise `UpcastError` for a stored version newer than the class too | Breaks rolling deploys and older consumers: every event written by the newer code fails on the older readers until they are deployed, although the payload often validates (additive changes), and `hydrate_command()`, which strips unknown keys from saga commands written by a newer schema (arc42 §8.10 and §9.10), already tolerates newer schemas. An upcaster chain that overshoots is still an error, because it is a registration mistake, not a deploy-order effect |
| Apply the version check without an `UpcasterRegistry` too | Changes Level 3 users who never opted into versioning; an application that bumped `event_version` for an additive change would start failing on read. Attaching an empty `UpcasterRegistry` gives the check to those who want it |
| Treat a `KeyError` from the chain as an `UpcastError` | `EventUpcaster.upcast` already wraps `_transform` failures; an `upcast()` override or a validator that raises `KeyError` is the user's code, and its exception should reach the user unchanged |
| Accept only `int` stored versions, no coercion of integer strings | Every read fails for adapters that store the version as text, although `"2"` has one meaning. Rejected by the author on 2026-10-21 |
| Require `target_version == source_version + 1` in `register` | Stricter than the contract needs: a jump (v1 → v3) is a valid chain and the version check still catches a gap. One step per upcaster stays advice in the docs. Rejected by the author on 2026-10-21 |
| Always check the version, logging a warning when no `UpcasterRegistry` is attached | A warning on every read of every bumped event floods logs without stopping the stale payload; the check is either a contract or it is not. Rejected by the author on 2026-10-21 |
| A distinct `NewerEventVersionError` for a stored version newer than the class | Decision 2a validates newer events instead of raising, so there is nothing to raise; a gap and an overshoot are both upcasting failures and share `UpcastError`. Rejected by the author on 2026-10-21 |
| Remove the cycle guard, since `register` now prevents cycles | Class variables are mutable, so a cycle can still be built after registration, and the guard costs one set per resolve |

## Consequences

### Positive

- A read-serialize-read round trip is stable: each stored event is upcast once, whatever the upcasters do
  with `event_version`.
- A missing upcaster fails on the first read with a message naming the type and the two versions, instead of
  validating an old payload against the new class.
- A bug in a validator or an upcaster override surfaces as its own exception, not as a `GenericDomainEvent`.
- Mistakes in upcaster registration fail at startup, where they are cheap to fix.

### Negative

- Behaviour change: with an `UpcasterRegistry` attached, a stored event whose version has no complete chain
  to the class's version now raises `UpcastError` where it used to validate. Applications relying on
  defaults to fill a skipped version must register the missing upcaster (a pass-through `_transform` is
  enough). The upgrade notes say so.
- Registering two upcasters for the same step, or a downgrade, now raises where it used to overwrite or be
  accepted.
- An event stored at a newer version than the class still reaches validation with a schema the class may
  not describe. Whether it validates depends on the change, as before.

### Neutral

- An unregistered type is still not upcast; it falls back to `GenericDomainEvent` with the stored version
  (ADR-044).
- The `UpcastError` messages added here carry type names and versions only.
- A class that declares `event_version` without a default has no class version; reading it through an
  `UpcasterRegistry` raises `UpcastError` naming the type and asking for a default (decision 2b).

## References

- [ADR-042](ADR-042-event-upcaster-chain-cycle-detection.md): the chain and cycle guard this amends
- [ADR-044](ADR-044-dynamic-event-registry-generic-fallback.md): the `deserialize` flow whose `except KeyError`
  this narrows
- [ADR-066](ADR-066-test-first-fixes-with-documentation.md): test-first fixes with documentation
- `docs/others/bug-audit-findings.md` and `docs/others/bug-audit-plan.md` §12: rows INF-14, ES-15, ES-14, A30
- `docs/others/bug-fix-plan.md` §8, W1-2
- `src/pydomain/es/upcasting.py`, `src/pydomain/infrastructure/event_registry.py`
- `tests/es/test_upcasting.py`, `tests/es/test_upcasting_pipeline.py`,
  `tests/infrastructure/test_event_registry_versioning.py`
- YouTrack DCE-117
