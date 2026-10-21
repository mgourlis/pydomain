## Summary

<!-- 1-3 bullets describing what this PR does -->

-

## YouTrack Issue

<!-- DCE-NN; the PR title must match the issue summary -->

DCE-

## Type of Change

- [ ] `feat` — New capability
- [ ] `fix` — Bug fix
- [ ] `refactor` — Code restructuring (no behavior change)
- [ ] `test` — Adding or updating tests
- [ ] `docs` — Documentation changes
- [ ] `chore` / `ci` — Tooling, CI, config

## DDD Layer Check

- [ ] Changes are in the correct layer (`ddd/`, `cqrs/`, `es/`, `infrastructure/`, `testing/`)
- [ ] No infrastructure imports in `ddd/`
- [ ] Value Objects are frozen (`ConfigDict(frozen=True)`)
- [ ] Entities use `frozen=False`
- [ ] Domain events named in past tense
- [ ] Only Pydantic v2 APIs used (no v1 shims)

## Tests (test-first)

- [ ] Acceptance criteria are in the issue as test names
- [ ] A red commit (`DCE-NN: test(...)`, tests marked `xfail(strict=True, raises=...)`) passed CI before the fix commit — link the run:
- [ ] Every applicable layer is present: regression example, property, stateful, contract suite, failure injection, content sentinel, typing, docs example
- [ ] No test asserts private state; no existing test was weakened (changed tests say why)
- [ ] Mutation testing on the changed functions; survivors killed or justified
- [ ] Branch coverage of changed files did not fall; `make check` and `make test` pass

## Documentation

- [ ] ADR written via `adr-writer` (Proposed → Accepted), README and supersede/amend chain updated — or "no decision recorded" with the reason
- [ ] arc42 §9 updated (narrative if architectural, mapping, reference, count)
- [ ] Affected arc42 sections updated (§5, §6 diagrams, §8, §10, §11, §12, §7.5)
- [ ] Affected Diataxis concept, how-to, recipe and testing pages updated; pages that taught the defect fixed
- [ ] Docstrings and README match the behaviour
- [ ] Upgrade-notes entry for any behaviour change
- [ ] Docs lint, link check and docs example tests pass

## Checklist

- [ ] Code commits start with the issue ID (`DCE-NN: fix(scope): ...`)
- [ ] Public API is backward-compatible, or the PR targets `release/v*` with `!` commits
- [ ] `__init__.py` exports and `tests/test_package_exports.py` updated if public symbols changed
