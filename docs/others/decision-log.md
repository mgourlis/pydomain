# Decision Log and Feature Backlog

| Item | Value |
|---|---|
| Date | 2026-10-21 |
| Scope | Every question the author answered during the 2026-10 correctness audit (Epic DCE-105) and after, and the feature ideas those answers produced |
| Status | Maintained: a row is added whenever the author answers a question |
| Rule | Each answer is also recorded where it applies (an ADR, an issue, the fix plan, `CLAUDE.md`). This log is the index, and the only place that lists every option the author was offered. Rows that are recorded nowhere else say so |

The open questions of the audit are listed in [bug-fix-plan.md §14](bug-fix-plan.md#14-decisions-for-the-author). When
one is answered, §14 moves it to its "Decided" list and this log gets a row.

## Table of Contents

1. [Answered questions](#1-answered-questions)
2. [Feature backlog](#2-feature-backlog)

---

## 1. Answered questions

### 1.1 Process and tooling

| # | Date | Question | Answer | Rejected | Recorded in |
|---|---|---|---|---|---|
| P-1 | 2026-10-21 | Where the audit fix issues live in YouTrack | One audit Epic, DCE-105, with `Relates` links to the Level Epics DCE-1 to DCE-5 | Under the Level Epics | bug-fix-plan.md §12, §14 (D-Epic); DCE-105 |
| P-2 | 2026-10-21 | Must commits name their issue | `DCE-NN:` is required on `feat`, `fix`, `perf`, `refactor` and `test`; optional on other types | Required everywhere; keep it optional | bug-fix-plan.md §14 (D-P3); ADR-066; `.github/scripts/check-commit-msg.sh` |
| P-3 | 2026-10-21 | How granular the fix ADRs are | One ADR per fix cluster | One per audit row; one per release | bug-fix-plan.md §14 (D-ADR); ADR-066 |
| P-4 | 2026-10-21 | The per-fix workflow, and whether 0.4.0 code waits for `v0.3.1` | The §11 workflow is accepted, with no hold: Wave 2 code merges once its ADR is Accepted | Hold 0.4.0 until `v0.3.1`; a release branch | bug-fix-plan.md §6, §14 (D-W2); `CLAUDE.md`; ADR-066 |
| P-5 | 2026-10-21 | How the squash commit on `dev` gets a valid subject | The PR title carries the prefix (`DCE-NN: <type>(<scope>): <summary>`) and CI lints it | Edit the subject at merge; lint `dev` after merge | bug-fix-plan.md §14; ADR-066; `CLAUDE.md` step ⑧; `.github/workflows/lint-commits.yml` |
| P-6 | 2026-10-21 | How the red commit gets a CI run the PR can link | CI runs on pushes to feature, fix, chore, ci, docs and hotfix branches | A draft PR at the red step; local evidence only | bug-fix-plan.md §14; ADR-066; `.github/workflows/test.yml` |
| P-7 | 2026-10-21 | Which timezone the DCE project uses | Europe/Athens | Europe/Moscow; drop the field | bug-fix-plan.md §14; `CLAUDE.md` |
| P-8 | 2026-10-21 | What happens to the Codecov upload, which never worked | Remove it | Fix it with a token; leave it as is | bug-fix-plan.md §14; `.github/workflows/test.yml` |
| P-9 | 2026-10-21 | Which documentation is maintained (D-P11) | The repository docs only (`docs/diataxis/`, `docs/arch42/`, `docs/adr/`); the YouTrack KB is historical | Keep both; generate the KB from the repo | bug-fix-plan.md §14, §15; `CLAUDE.md` (DCE-114) |
| P-10 | 2026-10-21 | How users hand their adapter to a contract suite | A pytest fixture per role | A factory method; both | ADR-067 |
| P-11 | 2026-10-21 | How suites get the domain objects they store | Hook methods the user overrides (`new_aggregate`, `change`, `new_event`, …) | Suite-supplied sample types; sample types, overridable | ADR-067 |
| P-12 | 2026-10-21 | Whether the contract suites pin an async backend | Pin asyncio by default (session scope, overridable) | Don't pin | ADR-067 |
| P-13 | 2026-10-21 | Who owns DCE-109, whose worktree was clean and whose session had exited | Abandoned: redo it here | Another session owns it; redo it but keep the old worktree | DCE-109 |
| P-14 | 2026-10-21 | How blocked YouTrack transitions, pushes and PRs are handled | The author adds allow rules to the local Claude Code settings; Claude keeps doing transitions, pushes and PRs | Approve each one as it comes; the author does them | Only here: a local working preference, not a repository rule |
| P-15 | 2026-10-21 | Which model each new agent runs on | The author chooses; Claude proposes a model with a reason before starting any agent. Custom agents with a set model start as defined; resumed agents keep theirs | Every agent inherits the main session's model | bug-fix-plan.md §11.1, §14 (D-Agents) |
| P-16 | 2026-10-21 | Which model reviews architecture and docs (⑩) | A trial: on PR #109 one ⑩ runs on Sonnet beside an Opus ⑩; compare the findings, then keep or revert | Sonnet only; Opus only | bug-fix-plan.md §11.1 |
| P-17 | 2026-10-21 | The result of the ⑩ model trial (P-16) on PR #109 | Sonnet runs ⑩ by default; Opus runs ⑩ where the docs make claims about runtime behaviour (lifecycles, concurrency, failure paths) and in the DRAFT clusters | Sonnet always; Opus always | bug-fix-plan.md §11.1, §14 (D-Agents) |
| P-18 | 2026-10-21 | Whether the docs runner's `<!-- docs-test: check` marker, which runs hidden calls in page order, stays in PR #109 (DCE-121) | Keep it in this PR | A separate PR; visible calls on the pages | `docs/diataxis/how-to/testing/add-runnable-docs-examples.md` |
| P-19 | 2026-10-21 | Where a docs-runner guard goes that fails when a runnable page defines `test_*`, `Test*` or `main` and no check block calls it | DCE-126's scope; with it, DCE-126 also covers running the visible fixtures and a runner-side `sys.modules` clean-up | A new Task under DCE-105; backlog item F-7 | A DCE-126 comment |

### 1.2 Library behaviour

| # | Date | Issue | Question | Answer | Rejected | Recorded in |
|---|---|---|---|---|---|---|
| B-1 | 2026-10-21 | DCE-116 | What a second `commit()` in one unit-of-work scope does (D-CQRS-10) | It raises `UnitOfWorkAlreadyCommittedError(CQRSError)` before any hook runs | Silent no-op (loses later changes); incremental commits (see F-2) | ADR-068; DCE-116; bug-fix-plan.md §14 |
| B-2 | 2026-10-21 | DCE-116 | What `rollback()` does after a successful commit | Drains the buffers, clears collected events and starts a new scope; a later `commit()` is allowed | No-op; raise | ADR-068; DCE-116 |
| B-3 | 2026-10-21 | DCE-116 | Whether the bus rolls back when a handler committed itself and the bus's commit then raises | No rollback: the data is already committed. The caller gets `CommandExecutionError` caused by `UnitOfWorkAlreadyCommittedError`, whose message says the first commit's changes are persisted | Force a rollback in `__aexit__` | ADR-068; DCE-116 |
| B-4 | 2026-10-21 | DCE-116 | Which error surfaces when the body and `rollback()` both raise (direct UoW use) | Python's default: the rollback error, with the body's error as `__context__` | Re-raise the body's error with a note | ADR-068; DCE-116 |
| B-5 | 2026-10-21 | DCE-116 | Whether `commit()` may be retried after a commit that failed part-way | No: the retry raises; `rollback()` starts a new scope | Allow the retry | ADR-068; DCE-116 |
| B-6 | 2026-10-21 | DCE-117 | When the "chain must end at the class version" check runs | Only when an `UpcasterRegistry` is attached (opt-in, arc42 §8.8) | Always (see F-3); always with a warning | ADR-069; DCE-117 |
| B-7 | 2026-10-21 | DCE-117 | What happens to a stored event version newer than the class | It validates unchanged, as before (rolling deploys); `UpcastError` only when an upcaster chain stops short or overshoots | Raise; a distinct `NewerEventVersionError` | ADR-069; DCE-117 |
| B-8 | 2026-10-21 | DCE-117 | What `UpcasterRegistry.register` checks | `TypeError` for a missing class variable; `ValueError` for a duplicate step or `target_version <= source_version`; version jumps allowed | Require steps of exactly +1 | ADR-069; DCE-117 |
| B-9 | 2026-10-21 | DCE-125 | What `DomainEvent.stamp()` raises for a non-UUID id | Pydantic `ValidationError`; UUID strings are coerced, as at construction | `TypeError`; `DomainError` | ADR-070; DCE-125 |
| B-10 | 2026-10-21 | DCE-125 | How composite specifications serialize | Dump only: children's fields appear (`SerializeAsAny`); no round trip in 0.3.1 | A type tag plus registry now (see F-1); a documented discriminated union | ADR-070; DCE-125 |
| B-11 | 2026-10-21 | DCE-125 | Whether `Query` gets the per-subclass id generator fix | Yes, in the same PR as `DomainEvent` and `Command` | A separate issue | ADR-070; DCE-125 |
| B-12 | 2026-10-21 | DCE-125 | How `stamp()` validates its ids (Pydantic's `validate_assignment` ran the event's model validators twice) | Validate only the two ids with the field's validator, then copy; no model validator runs | `validate_assignment` with both ids set first; re-validate the whole event | ADR-070; DCE-125 |
| B-13 | 2026-10-21 | DCE-125 | What this PR does about the rest of DDD-03 (unbound generic ids, `Annotated` and nested alias unions) | Resolve id types recursively; any id type still unresolvable raises `DomainError` saying to parametrize the generic | Use the `TypeVar` bound now (see F-5); leave it open | ADR-070; DCE-125 |
| B-14 | 2026-10-21 | DCE-116 | Events staged after a successful commit leaked into the next commit (B-3 skips the rollback) | A committed scope still drains every repository's buffer on exit, discarding those events; no storage rollback, and the committed events stay in `collect_events()` | Document the gap | ADR-068; DCE-116 |
| B-15 | 2026-10-21 | DCE-116 | A storage rollback that raised skipped the drain | A new `_rollback()` hook, like `_flush`/`_commit`; `rollback()` calls it inside try/finally around the drain | Teach try/finally in the docs only | ADR-068; DCE-116 |
| B-16 | 2026-10-21 | DCE-116 | When the handler and the rollback both fail, the rollback error was hidden from tracebacks and logs | `CommandExecutionError` gets a note naming the failed clean-up's error type only, never its message (`clean-up also failed: <type>`; it also covers the committed-exit drain) | Reachable only through `__context__` | ADR-068; DCE-116 |
| B-17 | 2026-10-21 | DCE-117 | What a stored envelope version given as a string does | An integer string is converted (`"2"` → 2) so adapters with text versions upcast correctly; `None`, `bool`, floats and other text raise `UpcastError` | Strict ints only (every read fails for those adapters) | ADR-069; DCE-117 |
| B-18 | 2026-10-21 | DCE-125 | What the id guard does for an id type of `Any` | `Any` accepts every value, as in Pydantic; the guard checks nothing for such entities | Raise `DomainError` like an unbound generic | ADR-070; DCE-125 |
| B-19 | 2026-10-21 | DCE-116 | How to handle concurrent units of work that share one repository (a drain can drop or misattribute another dispatch's events; `AggregateLockingBehavior` does not prevent it) | Document the rule: share a repository only between units of work that run one after another; build it per unit of work for concurrent dispatches. Scope-keyed buffers become Feature F-6 | Scope-keyed buffers now; no drain on committed exit (undoes B-14) | ADR-068; DCE-116 |
| B-20 | 2026-10-21 | DCE-116 | What happens when the committed-exit drain raises on a successful dispatch | Documented: the exception propagates unwrapped and no events are returned; the commit persisted | Wrap it in `CommandExecutionError` | ADR-068; DCE-116 |
| B-21 | 2026-10-21 | DCE-124 | What `read_stream`/`read_all` do with a negative `from_version` (D-ES-05) | Raise `ValueError`; stated in the `EventStore` Protocol, implemented in `FakeEventStore`, pinned by a contract case | Treat it as 0 (hides wrong offsets); keep Python slicing (the fake reads from the end) | ADR-072; DCE-124; bug-fix-plan.md §14 |
| B-22 | 2026-10-21 | DCE-121 | Which exception `bootstrap()` raises when an inbound gateway's bus is not the application's bus | `ValueError`, whose message names the gateway's index and type (`inbound_gateways[1] (InboundEventGateway)`) and no payload | A new `BootstrapError`/`WiringError`; `TypeError` | ADR-073; DCE-121 |
| B-23 | 2026-10-21 | DCE-121 | When the bus check runs | At the top of `bootstrap()`, before anything starts, so nothing needs cleaning up | In each gateway's start, through the partial-start clean-up | ADR-073; DCE-121 |
| B-24 | 2026-10-21 | DCE-121 | What counts as a mismatch | Any gateway whose bus is not the application's bus; omitting `message_bus=` with gateways present raises, so the docs pass the bus (`bootstrap(message_bus=bus, inbound_gateways=[gateway])`) | Adopt the gateways' common bus when `message_bus=` is omitted | ADR-073; DCE-121 |
| B-25 | 2026-10-21 | DCE-121 | How `bootstrap()` reads a gateway's bus | A new read-only `InboundEventGateway.message_bus` property | Read the private `_message_bus` from `bootstrap.py` | ADR-073; DCE-121 |
| B-26 | 2026-10-21 | DCE-121 | What `shutdown()` does with errors from its stops | Every stop runs in the existing order, inbound gateways first, then the outbound gateway before the broker (ADR-063 point 7); the first error is re-raised with a note `clean-up also failed: <type>` (type only) per later failure. Only `Exception` is isolated: a `BaseException` such as a cancellation propagates at once | An `ExceptionGroup` of all errors; log only, never raise | ADR-073; DCE-121 |
| B-27 | 2026-10-21 | DCE-121 | What a start that fails part-way cleans up, and how | The parts that started are stopped in shutdown order; the part whose `start()` raised is not stopped. The original start error propagates, with the same type-only note when a clean-up stop fails | Reverse start order; an `ExceptionGroup`; the clean-up error replacing the start error; also stopping the failed part | ADR-073; DCE-121 |
| B-28 | 2026-10-21 | DCE-121 | Type of `Application.event_registry` | `EventRegistry`: `Application.__init__` creates one when given `None`, as `bootstrap()` does | Keep `EventRegistry \| None` | ADR-073; DCE-121; upgrade notes |
| B-29 | 2026-10-21 | DCE-118 | What the value-free description of an exception (`_describe`) contains, and what stored errors keep | One line: type, model, error count, each location with its Pydantic error code (`ValidationError: 2 errors for X at amount (int_parsing), invoice_id (missing)`); no message, input or ctx; any other exception by its type name only. Stored errors keep the library prefix (`Payload validation failed: <description>`) | Locations only, no codes; a structured dict serialized into the string | ADR-071; DCE-118 |
| B-30 | 2026-10-21 | DCE-118 | Field locations can carry input (a dict field's keys, an unknown key under `extra="forbid"`) | Keep full locations; ADR-071 and the sensitive-payloads how-to say these keys are not protected | Top-level field name only; no locations, only count and codes | ADR-071; DCE-118 |
| B-31 | 2026-10-21 | DCE-118 | Whether the relay's broker and store failures are described too, and whether their tracebacks stay | Described: the stored error and the log carry the type and the description, logged with `logger.error` and no traceback. The broker stack trace is no longer logged | Keep the traceback until W2-3; leave it to the adapter | ADR-071; DCE-118 |
| B-32 | 2026-10-21 | DCE-118 | Whether `UpcastError` keeps `raise ... from exc` | Keep the chain; the library's own message uses the description instead of `{exc}`. The cause is the user's exception and may carry values | `from None`; raise outside the `except` block | ADR-071; DCE-118 |
| B-33 | 2026-10-21 | DCE-118 | Whether user-code failures the saga manager stores are in scope, and the inbound translator and dispatch tracebacks | The saga manager's stored errors (`failed_compensations`, `state.error` from a handler failure, the timeout handler's failure) are described now; the inbound translator and dispatch tracebacks, and the saga manager's log lines that format an exception, stay for W2-3 | All to W2-3; both now | ADR-071; DCE-118 |
| B-34 | 2026-10-21 | DCE-118 | Which `SagaState` fields count as library-owned payloads | `pending_commands` and `failed_compensations`, besides `CompensationRecord.data`; the metadata fields stay visible | `CompensationRecord.data` only; the metadata fields too | ADR-071; DCE-118 |
| B-35 | 2026-10-21 | DCE-118 | What happens to the Pydantic `ValidationError` that `EventRegistry.deserialize` lets reach callers | Unchanged: it reaches callers as Pydantic raised it | Wrap it in a library exception | ADR-071; DCE-118 |
| B-36 | 2026-10-21 | DCE-124 | What an empty `append_to_stream` does (ES-04, D1) | It runs the duplicate and version checks as usual (`DuplicateCommandError` / `ConcurrencyError`); on success it stores nothing, creates no stream and does not record the `command_id`. Stated in the `EventStore` Protocol, pinned by `EventStoreContract` cases | A true no-op that skips the checks; recording the `command_id`; a fake-only rule with the Protocol silent | ADR-072; DCE-124 |
| B-37 | 2026-10-21 | DCE-124 | Whether W1-9 adds a `MessageSubscriberContract` case (D2) | No: `simulate_message()` is not in the Protocol (ADR-067 rule 3); its `KeyError` is pinned by a fake test | A new Protocol promise for unsubscribed topics plus a case; a re-subscribe case | ADR-072; DCE-124 |
| B-38 | 2026-10-21 | DCE-124 | Which error a negative `from_version` on a missing stream raises (D3) | `ValueError`: the offset is checked before the stream lookup | `StreamNotFoundError` first | ADR-072; DCE-124 |
| B-39 | 2026-10-21 | DCE-124 | Whether the new `EventStore` promises are breaking (D4) | Behaviour changes in the upgrade notes, not breaking; no `!` commits | Breaking changes through `release/v*` | ADR-072; DCE-124; upgrade-to-next-release.md |
| B-40 | 2026-10-21 | DCE-121 | What happens when `bootstrap()` is cancelled while starting | The parts already started are stopped, shielded from the cancellation, then the cancellation is re-raised; shutdown still stops at once on cancellation (B-26) | Propagate at once without cleanup (started consumers leak) | ADR-073; DCE-121 |
| B-41 | 2026-10-21 | DCE-121 | Whether `KeyboardInterrupt`/`SystemExit` during a start also trigger the cleanup | No: the cleanup runs on `asyncio.CancelledError` only; interrupts and exits propagate at once | Clean up on any `BaseException` (can delay a Ctrl-C or exit) | ADR-073; DCE-121 |
| B-42 | 2026-10-21 | DCE-118 | Whether the saga manager's dispatch-failure suspension reason (and so `state.error` after a timeout) is sanitized | Yes: `describe(cause)`, like the other stored saga errors | Defer it and narrow the docs' claims | ADR-071; DCE-118 |
| B-43 | 2026-10-21 | DCE-118 | What the inbound "invalid tracing IDs" warning logs | The field that failed to parse, never its value | Keep the values, as an ADR exception | ADR-071; DCE-118 |
| B-44 | 2026-10-21 | DCE-118 | Which cluster owns the subscription batch-failure traceback (`subscription.py`) | W2-3, with the other handler-failure tracebacks; listed in every gap list | W2-7; fix it now | ADR-071; DCE-118 |
| B-45 | 2026-10-21 | DCE-121 | What happens when `bootstrap()` is cancelled during the partial-start clean-up | The clean-up is shielded like B-40: it finishes, its failures are noted on the start error, then the cancellation is re-raised with the start error as `__context__` | Let the cancellation interrupt the clean-up (parts not yet stopped keep running) | ADR-073; DCE-121 |
| B-46 | 2026-10-21 | DCE-121 | What `bootstrap()` does with one gateway object listed twice | `ValueError` naming the second position and the type, no payload, checked with the bus check before anything starts. The same gateways passed to two `bootstrap()` calls stay a documented caveat | Accept it (started and stopped twice) | ADR-073; DCE-121 |
| B-47 | 2026-10-21 | DCE-121 | Whether `Application` shares the caller's `inbound_gateways` list | No: `__init__` stores a copy and the property returns a new list each time (still a `list`) | Keep the caller's list; return a tuple | ADR-073; DCE-121 |

---

## 2. Feature backlog

Ideas for making pydomain better, collected from the author's requests and from decisions that deferred
something. An item becomes a YouTrack Feature when it is scheduled. The Wave 2 and Wave 3 clusters already
planned are in bug-fix-plan.md [§9](bug-fix-plan.md#9-wave-2-040-fixes-that-need-a-decision) and
[§10](bug-fix-plan.md#10-wave-3-050-breaking-changes); they are not repeated here.

Status: **Wanted** means the author asked for it; **Idea** means a decision deferred it and nobody has asked
for it yet.

| # | Feature | Origin | Status | Notes |
|---|---|---|---|---|
| F-1 | **JSON to Specification**: load a specification, including nested `And`/`Or`/`Not` composites, from its JSON dump (a type-tagged round trip with a registry of leaf specifications) | Requested by the author (2026-10-21); deferred by B-10 | Wanted | Needs an ADR that amends ADR-010 and ADR-070, a registry API for leaf specifications, and a safe loader that only builds registered types. Pydantic's serializer fails on composites nested more than a few hundred levels deep; neither the dump nor the loader should have that limit |
| F-2 | Incremental commits: several `commit()` calls in one scope, each writing only what is new | Rejected for 0.3.1 by B-1 | Idea | Changes what `collect_events()` returns and conflicts with ADR-020 (the bus owns the lifecycle); only worth it with a real multi-step use case |
| F-3 | Check the event version even without an `UpcasterRegistry`: warn first, then raise in a major release | Deferred by B-6 | Idea | Closes the silent stale-shape read (ES-15) for users who don't upcast; needs a deprecation period |
| F-4 | Upcast unregistered event types too | The unregistered-type part of INF-14, left unchanged by DCE-117 (ADR-069 Neutral) | Idea | Today an unregistered type falls back to `GenericDomainEvent` without upcasting |
| F-5 | Entities with an unbound generic id (`TypeVar`): use its bound or default to generate the id | Deferred by B-13 | Idea | Until then, such an entity raises `DomainError` without an explicit id |
| F-6 | **Scope-keyed event buffers**: each unit of work gets its own pending-event buffer in a shared repository (e.g. through a context variable), so concurrent dispatches can share one repository safely | Requested by the author as a Feature (2026-10-21); deferred by B-19 | Wanted | Changes how `Repository` implementations store pending events; pairs with W2-1 (locks held through the commit). Until then, build the repository per unit of work. |
