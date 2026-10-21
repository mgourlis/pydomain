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
`_repos` holds an object without `pull_events()` (commit already required it). A repository shared by
units of work that run at the same time now has one scope's exit drain the other's events: share one only
between units of work that run one after another.

**What to do:** nothing, unless a test relied on a failed command's events appearing in a later dispatch.

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
tracebacks, names the rollback exception's type (`rollback also failed: ConnectionError`), never its
message. A cancellation is not wrapped: `CancelledError` propagates after one rollback.

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
