# How to Upgrade to the Next Release

> **Prerequisites:** none. **Release:** next after 0.3.0 (0.3.1 if it contains only fixes, 0.4.0 otherwise).

This page collects the behaviour changes merged since 0.3.0. Read it before upgrading; each entry says who
is affected and what to do. See [Writing an entry](_index.md#writing-an-entry) for the format.

## Breaking

_None._ Breaking changes ship only through `release/v*` branches.

## Behaviour changes

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
