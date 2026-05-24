---
name: git-conventions
description: >
  Enforce Git conventions and repository rules. Trigger whenever creating a pull request (PR),
  branching, merging to main/dev/staging, writing commit messages, syncing-back,
  running pre-commit hooks, or modifying GitHub templates/issues.
---
# Git & PR Conventions

## Core Syntax & Rules (Cache)

**Branch Naming & Flow:** * 4-branch model: `feature/*` → `dev` → `staging` → `main`
* Name patterns: `<feature|fix|chore>/<issue-id>-<short-description>` (e.g., `feature/DCE-XX-*`).

**Commit Format:** `DCE-NN: <type>(<optional-scope>)!: <description>` (e.g., `DCE-51: feat(api)!: add pipeline`).
* `!` indicates breaking changes.
* **Changelog tracked:** `feat`, `fix`, `perf`.
* **Ignored:** `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `build`, `revert`.

**Branch Map & Merge Strategies:**
* `feature/DCE-XX-*`: Branched from `dev` via `voltagent-dev-exp:git-workflow-manager` (step ②). **Squash and merge** into `dev` via PR (step ⑧). Condenses messy history.
* `hotfix/*`: Emergency fix created by Developer directly from `main`. **Squash and merge** into `main` via PR.
* `release/v*`: Major breaking releases created by Developer. **Create a merge commit** into `main` via PR.
* `dev`: Default integration. **Create a merge commit** into `staging` (step ⑩).
* `staging`: Pre-release verification. **Create a merge commit** into `main` (step ⑪).
* `main`: Release branch. Automatically tagged and publishes to PyPI. No direct commits.
* **Sync-back PRs:** **Create a merge commit**. Never squash sync-backs.

---

## Execution & Workflows

### PR Creation
* Create via `gh pr create` (Bash) or VS Code extension.
* **Title:** Short (<70 chars), must match the YouTrack issue summary exactly.
* **Body:** `.github/PULL_REQUEST_TEMPLATE.md` (auto-loads on creation).
* **Post-PR Action:** Transition the associated YouTrack issue to `Review` state.

### GitHub Templates
* **Issue Templates:** Located in `.github/ISSUE_TEMPLATE/` (`bug_report.md`, `feature_request.md`, `task.md`).
* Blank issues are disabled via `config.yml`. All issues *must* use a template.

### Pre-Commit Hooks
* **Hooks configured:** `ruff` (lint + format), `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-merge-conflict`, `check-added-large-files`, `mypy`.
* **Install:** `uv run pre-commit install`
* **Run:** `make pre-commit` or `uv run pre-commit run --all-files`
