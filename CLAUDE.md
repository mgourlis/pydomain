# pydomain — Python DDD/CQRS/ES Library

---

## YouTrack Project

| Item | Value |
|---|---|
| Project | `DCE` (shortName) · `py-ddd-cqrs-es` · `https://mgourlis.youtrack.cloud` |
| Timezone `Europe/Athens` · User `admin` |

> Issue types, states, transitions, and KB access →  use  `youtrack-state-propagation` and `youtrack-issue-analyzer` skills

## State-Driven Workflow


**Workflow rules:**
- Steps ①② **must** complete before any code is written.
- Steps ③④ can run in **parallel** (domain + tests touch different paths).
- Steps ⑥⑦ can run in **parallel** (two independent reviews).
- YouTrack state transitions via MCP tools (`issue_change_state`) — do NOT delegate.

### Repo-Wide Refactoring


### Specialist Agent Assignments


---

## Git Conventions

### Branching GITFlow `dev` → `staging` → `main` (Long Lived Branches)

**4-branch model:** `feature/*` → `dev` → `staging` → `main`

`hotfix/*` branches for emergency fixes directly to `main`
`release/v*` branches for major releases with breaking changes


| Branch | Purpose | Merges into |
|---|---|---|
| `feature/DCE-XX-*` | Per-issue development | `dev` via PR (squash and merge) |
| `hotfix/*` | Emergency fix from `main` | `main` via PR (squash and merge) |
| `release/v*` | Major release with breaking changes | `main` via PR (merge commit) |
| `dev` | Integration (default) | `staging` via PR (merge commit) |
| `staging` | Pre-release verification | `main` via PR (merge commit) |
| `main` | Release branch | — (tagged only, auto-publishes to PyPI) |

Branch from `dev` via `git-workflow-manager`. Patterns: `feature/`, `fix/`, `chore/<issue-id>-<desc>`.

### Commit Format

Every commit **must** start with `DCE-NN:` for YouTrack auto-linking and follow Conventional Commits format:

```
DCE-NN: <type>(<optional-scope>): <description>

<optional body — what and why>

Co-Authored-By: Claude <noreply@anthropic.com>
```

**Types:** `feat`, `fix`, `perf`, `docs`, `style`, `refactor`, `test`, `chore`, `ci`, `build`, `revert`
  - `feat`/`fix`/`perf` appear in the changelog; all others are filtered out.
  - Add `!` after type/scope for breaking changes (e.g. `DCE-NN: feat(api)!: change login`).

**Merge strategy:**
  - `feature/*` → `dev`: **Squash and merge**
  - `dev` → `staging` / `staging` → `main`: **Create a merge commit**
  - `hotfix/*` → `main`: **Squash and merge**
  - `release/v*` → `main`: **Create a merge commit**

**Examples:**
```
DCE-51: feat: add pipeline behaviors to command bus
DCE-48: fix: resolve optimistic concurrency race in repository
DCE-27: docs: update KB article for pipeline behaviors
```

**Rules:** Always start with `DCE-NN:`. One logical change per commit. Imperative mood.

> PR creation, GitHub templates, pre-commit hooks → `@.claude/skills/git-conventions/SKILL.md`
