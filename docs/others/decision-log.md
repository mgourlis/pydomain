# Decision Log and Feature Backlog

| Item | Value |
|---|---|
| Started | 2026-10-21 |
| Scope | Every question the author answered during the 2026-10 correctness audit (Epic DCE-105) and after, and the feature ideas those answers produced |
| Rule | Append a row when the author answers a question. Add a backlog item when an answer defers something or the author asks for a feature. Record the same decision in its ADR or issue too; this log is the index, not the only record |

The open decisions of the audit are listed in [bug-fix-plan.md §14](bug-fix-plan.md#14-decisions-for-the-author). When one
is answered, it moves into the tables below.

## Table of Contents

- [1. Answered questions](#1-answered-questions)
  - [1.1 Process and tooling](#11-process-and-tooling)
  - [1.2 Library behaviour](#12-library-behaviour)
- [2. Feature backlog](#2-feature-backlog)

## 1. Answered questions

### 1.1 Process and tooling

| # | Date | Question | Answer | Rejected | Recorded in |
|---|---|---|---|---|---|
| P-1 | 2026-10-21 | Where the audit fix issues live in YouTrack | One audit Epic, DCE-105 | Under the Level Epics; an audit Epic plus Level links | bug-fix-plan.md §14 (D-Epic) |
| P-2 | 2026-10-21 | Must commits name their issue | `DCE-NN:` is required on `feat`, `fix`, `perf`, `refactor` and `test`; optional on other types | Required everywhere; keep it optional | §14 (D-P3), `.github/scripts/check-commit-msg.sh` |
| P-3 | 2026-10-21 | How granular the fix ADRs are | One ADR per fix cluster | One per audit row; one per release | §14 (D-ADR) |
| P-4 | 2026-10-21 | The per-fix workflow, and whether 0.4.0 code waits for `v0.3.1` | The §11 workflow is accepted, with no hold: Wave 2 code merges once its ADR is Accepted | Hold 0.4.0 until `v0.3.1`; a release branch | §6, §14 (D-W2), `CLAUDE.md`, ADR-066 |
| P-5 | 2026-10-21 | How the squash commit on `dev` gets a valid subject | The PR title carries the prefix (`DCE-NN: <type>(<scope>): <summary>`) and CI lints it | Edit the subject at merge; lint `dev` after merge | `.github/workflows/lint-commits.yml` |
| P-6 | 2026-10-21 | How the red commit gets a CI run the PR can link | CI runs on pushes to feature, fix, chore, ci, docs and hotfix branches | A draft PR at the red step; local evidence only | `.github/workflows/test.yml` |
| P-7 | 2026-10-21 | Which timezone the DCE project uses | Europe/Athens | Europe/Moscow; drop the field | `CLAUDE.md` |
| P-8 | 2026-10-21 | What happens to the Codecov upload, which never worked | Remove it | Fix it with a token; leave it as is | `.github/workflows/test.yml` |
| P-9 | 2026-10-21 | Which documentation is maintained (D-P11) | The repository docs only (`docs/diataxis/`, `docs/arch42/`, `docs/adr/`); the YouTrack KB is historical | Keep both; generate the KB from the repo | DCE-114, `CLAUDE.md` |
| P-10 | 2026-10-21 | How users hand their adapter to a contract suite | A pytest fixture per role | A factory method; both | ADR-067 |
| P-11 | 2026-10-21 | How suites get the domain objects they store | Hook methods the user overrides (`new_aggregate`, `change`, `new_event`, …) | Suite-supplied sample types; sample types, overridable | ADR-067 |
| P-12 | 2026-10-21 | Whether the contract suites pin an async backend | Pin asyncio by default (session scope, overridable) | Don't pin | ADR-067 |
| P-13 | 2026-10-21 | Who owns DCE-109, whose worktree was clean and whose session had exited | Abandoned: redo it here | Another session owns it; redo it but keep the old worktree | DCE-109 |
| P-14 | 2026-10-21 | How blocked YouTrack transitions, pushes and PRs are handled | The author adds allow rules; Claude keeps doing transitions, pushes and PRs | Approve each one as it comes; the author does them | — |

### 1.2 Library behaviour

| # | Date | Issue | Question | Answer | Rejected | Recorded in |
|---|---|---|---|---|---|---|
| B-1 | 2026-10-21 | DCE-116 | What a second `commit()` in one unit-of-work scope does (D-CQRS-10) | It raises `UnitOfWorkAlreadyCommittedError(CQRSError)` before any hook runs | Silent no-op (loses later changes); incremental commits (see F-2) | ADR-068, DCE-116 |
| B-2 | 2026-10-21 | DCE-116 | What `rollback()` does after a successful commit | Drains the buffers, clears collected events and starts a new scope; a later `commit()` is allowed | No-op; raise | ADR-068 |
| B-3 | 2026-10-21 | DCE-116 | Whether the bus rolls back when a handler committed itself and the bus's commit then raises | No rollback: the data is already committed. The caller gets `CommandExecutionError` caused by `UnitOfWorkAlreadyCommittedError`, whose message says the first commit's changes are persisted | Force a rollback in `__aexit__` | ADR-068, DCE-116 |
| B-4 | 2026-10-21 | DCE-116 | Which error surfaces when the body and `rollback()` both raise (direct UoW use) | Python's default: the rollback error, with the body's error as `__context__` | Re-raise the body's error with a note | ADR-068 |
| B-5 | 2026-10-21 | DCE-116 | Whether `commit()` may be retried after a commit that failed part-way | No: the retry raises; `rollback()` starts a new scope | Allow the retry | ADR-068 |
| B-6 | 2026-10-21 | DCE-117 | When the "chain must end at the class version" check runs | Only when an `UpcasterRegistry` is attached (opt-in, arc42 §8.8) | Always (see F-3); always with a warning | ADR-069, DCE-117 |
| B-7 | 2026-10-21 | DCE-117 | What happens to a stored event version newer than the class | It validates unchanged, as before (rolling deploys); `UpcastError` only when an upcaster chain stops short or overshoots | Raise; a distinct `NewerEventVersionError` | ADR-069 |
| B-8 | 2026-10-21 | DCE-117 | What `UpcasterRegistry.register` checks | `TypeError` for a missing class variable; `ValueError` for a duplicate step or `target_version <= source_version`; version jumps allowed | Require steps of exactly +1 | ADR-069 |
| B-9 | 2026-10-21 | DCE-125 | What `DomainEvent.stamp()` raises for a non-UUID id | Pydantic `ValidationError`; UUID strings are coerced, as at construction | `TypeError`; `DomainError` | ADR-070, DCE-125 |
| B-10 | 2026-10-21 | DCE-125 | How composite specifications serialize | Dump only: children's fields appear (`SerializeAsAny`); no round trip in 0.3.1 | A type tag plus registry now (see F-1); a documented discriminated union | ADR-070 |
| B-11 | 2026-10-21 | DCE-125 | Whether `Query` gets the per-subclass id generator fix | Yes, in the same PR as `DomainEvent` and `Command` | A separate issue | ADR-070 |

## 2. Feature backlog

Ideas for making pydomain better, collected from the author's requests and from decisions that deferred
something. An item becomes a YouTrack Feature when it is scheduled. The Wave 2 and Wave 3 clusters already
planned are in [bug-fix-plan.md §9 and §10](bug-fix-plan.md#9-wave-2-040-fixes-that-need-a-decision); they are not repeated here.

| # | Feature | Origin | Status | Notes |
|---|---|---|---|---|
| F-1 | **JSON to Specification**: load a specification, including nested `And`/`Or`/`Not` composites, from its JSON dump (a type-tagged round trip with a registry of leaf specifications) | Requested by the author (2026-10-21); deferred by B-10 | Wanted | Needs an ADR that amends ADR-010 and ADR-070, a registry API for leaf specifications, and a safe loader that only builds registered types. Pydantic's serializer fails on dumps nested more than a few hundred levels deep; the loader should not have that limit |
| F-2 | Incremental commits: several `commit()` calls in one scope, each writing only what is new | Rejected for 0.3.1 by B-1 | Idea | Changes what `collect_events()` returns and conflicts with ADR-020 (the bus owns the lifecycle); only worth it with a real multi-step use case |
| F-3 | Check the event version even without an `UpcasterRegistry`: warn first, then raise in a major release | Deferred by B-6 | Idea | Closes the silent stale-shape read (ES-15) for users who don't upcast; needs a deprecation period |
| F-4 | Upcast unregistered event types too (INF-14(a)) | Left unchanged by DCE-117 | Idea | Today an unregistered type falls back to `GenericDomainEvent` without upcasting |
| F-5 | Entities with an unbound generic id (`TypeVar`): use the bound, or skip the guard, instead of a raw `TypeError` | Found in DCE-125 | Idea | Recorded as Neutral in ADR-070 |
| F-6 | Warn or prevent when both a copy of an aggregate and its original are saved with the same pending events | Found in DCE-125 | Idea | Copies own their buffer (ADR-070), so saving both publishes the events twice |
