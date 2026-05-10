## Summary

<!-- 1-3 bullets describing what this PR does -->

-

## YouTrack Issue

Closes #

## Type of Change

- [ ] `feat` — New capability
- [ ] `fix` — Bug fix
- [ ] `refactor` — Code restructuring (no behavior change)
- [ ] `test` — Adding or updating tests
- [ ] `docs` — Documentation changes
- [ ] `chore` — Tooling, CI, config

## DDD Layer Check

- [ ] Changes are in the correct layer (`domain/`, `application/`, `infrastructure/`, `es/`)
- [ ] No infrastructure imports in `domain/`
- [ ] Value Objects are frozen (`ConfigDict(frozen=True)`)
- [ ] Entities use `frozen=False`
- [ ] Domain events named in past tense
- [ ] Only Pydantic v2 APIs used (no v1 shims)

## Test Plan

- [ ] New unit tests added for domain logic
- [ ] All existing tests pass (`make test`)
- [ ] Lint passes (`make lint`)
- [ ] Type check passes (`make type`)
- [ ] Pre-commit hooks pass (`uv run pre-commit run --all-files`)

## Checklist

- [ ] Commit messages include YouTrack issue ID (`feat(DCE-XX):`)
- [ ] Public API is backward-compatible (this is a library)
- [ ] `__init__.py` exports updated if new public symbols added
