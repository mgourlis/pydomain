# pydomain — Python DDD/CQRS/ES Library

## About

**pydomain** is a Python 3.12+ installable library that provides tactical and architectural building blocks for Domain-Driven Design (DDD), Command-Query Responsibility Segregation (CQRS), and Event Sourcing (ES). It is **not an application** — it is packaged and published to PyPI (`pydomain-lib`) for other Python projects to depend on.

The library is opinionated about patterns but unopinionated about infrastructure. Domain layer classes have zero infrastructure imports. Repository and EventStore contracts are defined as `typing.Protocol` — no inheritance lock-in. Users adopt incrementally across five levels: Tactical DDD → CQRS → Message Bus → Event Sourcing → Advanced ES (snapshots, upcasters, subscriptions).

Built entirely on **Pydantic v2** — every domain concept (entities, value objects, events, commands, queries) is a `BaseModel`, giving validation, serialization, and type safety out of the box. Only two runtime dependencies: `pydantic>=2.12.5` and `uuid-utils>=0.10`.

| Item | Value |
|---|---|
| Build backend | `hatchling` |
| Package name | `pydomain-lib` (PyPI) |
| Import name | `pydomain` |
| Python | ≥ 3.12 |
| Validation | Pydantic v2 only |
| Async | `async`/`await` throughout; `anyio` for async tests |
| Linter | `ruff` (target `py312`) |
| Repository | `github.com/mgourlis/pydomain` |

Full design details → `README.md` and `.github/copilot-instructions.md`.

---

## YouTrack Project

| Item | Value |
|---|---|
| Project | `DCE` (shortName) · `pydomain` · `https://mgourlis.youtrack.cloud` |
| Timezone `Europe/Athens` · User `admin` |

> Issue types, states and transitions →  use  `youtrack-state-propagation` and `youtrack-issue-analyzer` skills

**Documentation source of truth** (decided 2026-10-21, DCE-114): `docs/diataxis/`, `docs/arch42/` and
`docs/adr/` in this repository. The YouTrack Knowledge Base articles (`DCE-A-NN`) are historical: they
stopped being updated in June 2026 and are not changed by fixes or features. Reference repo paths in
issues, not KB articles.

## State-Driven Workflow

Every issue that changes code is test-first and ships with its documentation (ADR-066;
`docs/others/bug-fix-plan.md` §4, §5, §11).

| Step | Action | State |
|---|---|---|
| ① | Analyse: read the issue, name the contract (ADR, docstring or doc page), write the acceptance criteria as test names, list the ADR, arc42 and Diataxis pages to change | Open |
| ② | Branch `feature/DCE-NN-<slug>` (or `fix/`, `chore/`) from `dev`; create the ADR as Proposed if the change records a decision | Develop |
| ③ | **Red:** write the tests, marked `@pytest.mark.xfail(strict=True, raises=<expected failure>, reason="DCE-NN …")`; commit `DCE-NN: test(<scope>): …`; CI green proves they fail today | Develop |
| ④ | **Green:** the smallest change that passes; remove the markers; commit `DCE-NN: fix(<scope>): …` (or `feat`) | Develop |
| ⑤ | **Refactor and harden:** refactor with tests green; mutation testing on the changed functions | Develop |
| ⑥ | **Document:** ADR text, arc42 §9 and the affected sections, Diataxis pages, docstrings, upgrade notes | Develop |
| ⑦ | **Checks:** `make check`, `make test` (Python 3.12–3.14 in CI), docs lint and link check, docs example tests (once DCE-111 lands) | Test |
| ⑧ | **PR** to `dev`; title `DCE-NN: <type>(<scope>): <issue summary>` (it becomes the squash commit's subject; CI lints it with the commit rules, which require the prefix on code types); both definitions of done in the PR template ticked | Review |
| ⑨ ⑩ | **Two independent reviews in parallel:** ⑨ code and tests, ⑩ architecture and docs. A failed review moves the issue to Rework with a comment | Review |
| ⑪ | ADR → Accepted; squash-merge into `dev` (the PR title is the commit subject) | Merged |
| ⑫ | `dev` → `staging` (merge commit), once per release | Staging |
| ⑬ | `staging` → `main` (merge commit), tag, merge the sync-back PRs | Done |

**Workflow rules:**
- Steps ①② **must** complete before any code is written.
- Step ③ comes before step ④: no production change without a committed test that fails because of it.
  Never weaken an existing test to make a change pass.
- Steps ⑨⑩ can run in **parallel** (two independent reviews).
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

Branch from `dev` with `git switch -c feature/DCE-NN-<desc> dev`. Patterns: `feature/`, `fix/`, `chore/<issue-id>-<desc>`.

### Commit Format

Commits follow Conventional Commits. Code commits (`feat`, `fix`, `perf`, `refactor`, `test`) **must** start
with `DCE-NN:` for YouTrack auto-linking; the other types may omit it. The commit-msg hook and the
`Lint Commits` workflow enforce this (`.github/scripts/check-commit-msg.sh`).

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
DCE-27: docs: describe pipeline behaviors in the concepts pages
```

**Rules:** Start code commits with `DCE-NN:` (and preferably every commit made for an issue). One logical
change per commit. Imperative mood.

> PR creation, GitHub templates, pre-commit hooks → `@.claude/skills/git-conventions/SKILL.md`
