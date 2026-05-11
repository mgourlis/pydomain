---
name: git-conventions
description: Git branching, PR creation, GitHub templates, and pre-commit hooks for the pydomain project. Activate when creating pull requests, working with GitHub templates, or running pre-commit hooks.
---

# Git & PR Conventions — pydomain

## Branch Naming

**4-branch model:** `feature/*` → `dev` → `staging` → `main`

| Branch | Purpose | Who creates | Merges into |
|---|---|---|---|
| `feature/DCE-XX-*` | Per-issue development | `git-workflow-manager` (step ②) | `dev` via PR (step ⑧) |
| `dev` | Integration branch (default) | Pre-existing | `staging` (step ⑩) |
| `staging` | Pre-release verification | Pre-existing | `main` (step ⑪) |
| `main` | Release branch | Pre-existing | — (tagged, never merged into) |

Branch from `dev` using the `voltagent-dev-exp:git-workflow-manager` agent.

Patterns: `feature/<issue-id>-<short-description>`, `fix/<issue-id>-<short-description>`, `chore/<issue-id>-<short-description>`.

## PR Creation

- Use `gh pr create` via Bash, or the VS Code GitHub extension.
- PR title: short (<70 chars), matches the YouTrack issue summary.
- PR body follows the template in `.github/PULL_REQUEST_TEMPLATE.md`.
- After PR creation: transition the YouTrack issue to `Review` state.

## GitHub Templates

- **PR template**: `.github/PULL_REQUEST_TEMPLATE.md` — auto-loaded on PR creation.
- **Issue templates**: `.github/ISSUE_TEMPLATE/` — `bug_report.md`, `feature_request.md`, `task.md`.
- Blank issues are disabled (`config.yml`). All issues must use a template.

## Pre-Commit Hooks

Configured in `.pre-commit-config.yaml`:
- ruff (lint + format), trailing-whitespace, end-of-file-fixer, check-yaml, check-merge-conflict, check-added-large-files, mypy.

Commands:
- `make pre-commit` or `uv run pre-commit run --all-files`
- Install: `uv run pre-commit install`
