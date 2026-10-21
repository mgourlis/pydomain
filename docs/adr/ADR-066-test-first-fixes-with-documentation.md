# ADR-066: Test-First Fixes with Documentation in the Same Change

## Status

Proposed

## Date

2026-10-21

## Context

A correctness audit of 0.3.0 (`docs/others/bug-audit-findings.md`) verified 134 suspected defects with probe
scripts. 88 were confirmed and 20 partly confirmed; 39 are S1 (silent message loss, duplicate side effects,
state leaking between commands). Three observations shape how the fixes, and later changes, are made:

1. **Coverage did not catch them.** The suite had 1,757 tests and 96.65 % branch coverage, and every defect
   sat on covered lines. The bugs live in ordering, failure paths, cancellation, copy semantics and fakes
   that are looser than their Protocols. A test written after a fix, against the implementation, tends to
   assert what the code does rather than what the contract says, and can pass while the defect remains.
2. **The documentation taught several of the defects.** 69 lines in 15 Diataxis pages use attributes that
   do not exist. A repository how-to loses concurrent updates, and an outbox how-to writes outside the
   command's transaction. Upcaster examples produce a double upcast. None of these examples was ever run.
   Behaviour, docs and ADRs had drifted apart because they were changed separately, if at all.
3. **Decisions were changed without a record.** Several confirmed defects contradict an Accepted ADR
   (ADR-004 against the exception hierarchy, ADR-062 against event idempotency), and two ADRs contradict each
   other (ADR-046 and ADR-060).
   ADRs are immutable once Accepted (`docs/adr/README.md`), so a fix that changes behaviour an ADR describes
   has to say so in a new ADR. Otherwise the ADR index stops describing the library.

## Decision

We will make every change that alters library behaviour **test-first**, and ship it with **its ADR, arc42
and Diataxis updates in the same pull request**. The full protocol is in `docs/others/bug-fix-plan.md`
§4 (tests), §5 (documentation) and §11 (workflow), and in the PR template's two definitions of done.

### Test-first

1. **Specify.** The issue's acceptance criteria are written as test names, one per defect, each naming the
   contract it checks (ADR, docstring or doc page).
2. **Red.** The tests are committed first (`DCE-NN: test(<scope>): …`), each marked
   `@pytest.mark.xfail(strict=True, raises=<expected failure>, reason="DCE-NN …")`. CI runs on feature-branch
   pushes, so this commit has its own green run. Strict mode turns an unexpected pass into a failure, and
   `raises=` makes the test fail for the intended reason, never an import error or a typo. Together they
   prove every new test fails before the fix.
3. **Green.** The smallest change that makes the tests pass, with the markers removed
   (`DCE-NN: fix(<scope>): …`).
4. **Refactor and harden.** Refactor with tests green, then mutation-test the changed functions. Every
   surviving mutant is killed or justified in the PR.

Tests assert the contract through the public API, spies or the Protocol contract suites, never private
attributes. An existing test is never weakened to let a change pass. A test that blessed a defect is changed
in the same PR, and the PR says why. Each fix includes every applicable test layer: regression example,
property, stateful, contract-suite case, failure and cancellation injection, content sentinel, typing,
docs example.

### Documentation in the same change

- **One ADR per fix cluster** (a set of defects with one root cause), written with the `adr-writer` skill:
  Proposed when development starts, Accepted when review approves, before the merge. A fix that changes an
  Accepted decision supersedes or amends it. A fix that restores a documented contract cites the ADR or page
  it restores. Changes that record no decision (pure tooling, docs drift) say so in the PR instead.
- **arc42 §9** for every ADR (narrative if architectural, mapping, reference table, decision count). Every
  other arc42 section whose description the change alters is updated too, including the §6 sequence
  diagrams.
- **Diataxis**: the concept pages, how-tos, recipes and testing pages that describe the behaviour. A page
  that taught the defect is fixed in the same PR and marked runnable, so the docs example tests keep it
  correct.
- **Docstrings, README and upgrade notes**: every docstring that states the contract, and an upgrade-notes
  entry for each behaviour change, because release notes are generated from commit subjects only.

### Traceability

Code commits (`feat`, `fix`, `perf`, `refactor`, `test`) and PR titles start with `DCE-NN:`. PR titles become
squash-commit subjects, so they are linted like commits.

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| Write tests after the fix | The audit shows covered code still hides defects; a test written after the fix often checks the implementation and cannot show it would have failed without the fix |
| Red tests without `strict`/`raises` (plain `xfail` or a skipped test) | A plain `xfail` stays green when the test unexpectedly passes or fails for the wrong reason (an import error), so it proves nothing |
| Local red evidence only (paste pytest output into the PR) | Not independently verifiable; CI on branch pushes costs little and gives a linkable run |
| Batch documentation at release time | This is how the drift arose: by release time nobody knows which pages a change touched, and reviewers never see code and docs together |
| One ADR per release, or one per defect | Per release mixes unrelated decisions, so one later change forces superseding the whole ADR. Per defect produces 100+ ADRs, most recording one-line fixes with no real decision |
| ADRs only for new features, not for fixes | Several fixes change behaviour that an Accepted ADR describes; without a new ADR the index would contradict the code |

## Consequences

### Positive

- Every fix carries proof that its test reproduced the defect, linked from the PR.
- Code, ADRs, arc42 and Diataxis change together and are reviewed together, so drift has to pass two
  reviews to land.
- Runnable docs examples turn "the docs teach a defect" into a failing test.
- The ADR index stays an accurate history of behaviour, including corrections.

### Negative

- Each fix costs more. The fix plan estimates 40–60 % on top of the code change, mostly in arc42 §6
  diagrams and Diataxis how-tos.
- About 30 new ADRs over the audit, each with arc42 §9 updates.
- CI runs twice for pushes on branches that have an open PR (push and pull_request events).

### Neutral

- Changes that record no decision (CI, tooling, docs-only) still use the test and documentation
  checklists, marking items that don't apply as N/A.
- The probes from the audit are kept in `tests/support/probes/` as reference material until Wave 1 is
  done.

## References

- `docs/others/bug-audit-findings.md`: the verified defects
- `docs/others/bug-audit-plan.md`: the audit method, the coverage baseline (1,757 tests, 96.65 %) and the row IDs
- `docs/others/bug-fix-plan.md`: §4 test protocol, §5 documentation protocol, §11 workflow
- `.github/PULL_REQUEST_TEMPLATE.md`: test and documentation definitions of done
- `CLAUDE.md`, State-Driven Workflow
- YouTrack DCE-113 (this decision), DCE-105 (the audit Epic)
