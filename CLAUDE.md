# pydomain — Python DDD/CQRS/ES Library

## YouTrack Project

| Item | Value |
|---|---|
| Project ID | `DCE` (shortName), `0-1` (internal) |
| Project name | `py-ddd-cqrs-es` |
| YouTrack URL | `https://mgourlis.youtrack.cloud` |
| MCP server | `youtrack-mcp` v0.11.0 |
| Timezone | `Europe/Moscow` |
| Current user | `admin` (myrgourlis@gmail.com) |


## Agent Orchestration

This project uses specialized VoltAgent subagents from the `voltagent-subagents` marketplace. **Never try to execute the entire SDLC in one step.** Follow this delegation chain:

1. **Planning (`agent-organizer`):** Decompose YouTrack tickets, map requirements to our DDD layers, and assign domain agents.
2. **Execution:** Launch specialist agents directly for each step. Run agents in **parallel** when they touch different files with no dependencies. Run agents **sequentially** when one step's output feeds the next.
3. **Repo-wide refactoring:** Use `refactoring-specialist` for safe surgical changes + `architect-reviewer` for DDD boundary checks. **Human provides the approval gate** — review the proposed changes before execution.

> Full agent catalog and usage patterns: `@docs/voltagent-subagents-guide.md`

### When to use `multi-agent-coordinator`

**Only** when multiple agents must communicate/share state **during** execution (saga patterns, distributed workflows). For this project's sequential phases, launch specialists directly — `multi-agent-coordinator` adds unnecessary overhead.

### Specialist Agent Assignments

> **IMPORTANT:** Always use the **full qualified agent name** including namespace (e.g. `python-pro`, NOT just `python`). Omitting the namespace will cause "Agent not found" errors.

| Layer | Agent | Model | Use When | Project-Specific Notes |
|---|---|---|---|---|
| **Planning** | `agent-organizer` | sonnet | Decomposing YouTrack tickets into task sequences | Feed it the DCE issue types & workflow from this file |
| **Domain** | `python-pro` | sonnet | Value objects, entities, aggregate roots, domain events, domain services, specifications | **Must** follow copilot-instructions.md: frozen VOs, mutable entities, Pydantic v2 only, no infrastructure imports in `domain/` |
| **Application** | `python-pro` | sonnet | Commands, queries, command bus, query bus, message bus, unit of work | Handlers own the orchestration; UoW manages commits |
| **Infrastructure** | `python-pro` | sonnet | Repository implementations, event store adapters, snapshot stores | SQLAlchemy or in-memory fakes; track `_seen` for UoW |
| **Tests (strategy)** | `qa-expert` | sonnet | Test strategy, edge-case identification, coverage planning (**read-only**) | Focus on domain invariant tests, handler tests with FakeUoW |
| **Tests (code)** | `test-automator` | sonnet | Writing pytest fixtures, test cases, fakes, conftest.py | Use `pytest-anyio` + `anyio`; fakes over mocks; test domain logic directly |
| **Review** | `code-reviewer` | opus | Pre-merge review: correctness, layer discipline, Pydantic v2 API usage | Flag: v1 shims, infrastructure in domain, missing frozen on VOs |
| **Review (DDD)** | `architect-reviewer` | opus | DDD boundary review, layer discipline, aggregate consistency | Check: no repo for non-root entities, events named in past tense |
| **Refactoring** | `refactoring-specialist` | sonnet | Safe refactoring: extract method, rename, reduce complexity | Preserve public API; library consumers depend on it |
| **Docs (KB)** | `documentation-engineer` | haiku | YouTrack KB articles (DCE-A-NN), runbooks, architecture guides | Articles are Markdown mode; cross-link with `DCE-A-XX` syntax |
| **Git & PR** | `git-workflow-manager` | haiku | Branch creation, conventional commits, PR creation | `dev` is the default branch |
| **Tooling** | `tooling-engineer` | sonnet | `pyproject.toml`, `Makefile`, CI/CD, pre-commit hooks, ruff config | Build backend: `hatchling`; linter: `ruff` target `py312` |

### Critical: `qa-expert` vs `test-automator`

- `qa-expert` is **read-only** (tools: Read, Grep, Glob, Bash). Use it for test **strategy** — what to test, edge cases, coverage gaps.
- `test-automator` is **write-capable**. Use it to actually **write** test code — fixtures, parametrized cases, fakes.
- In the TDD cycle: `qa-expert` designs the tests → `test-automator` writes them.

### YouTrack State Transitions (do NOT delegate)

YouTrack issue state transitions (`Open → Develop → Test → Review → Merged→ Staging → Done`) are performed **directly via YouTrack MCP tools** (`issue_change_state`). Do not delegate these to any agent.

### Git & PR Workflow

#### Start-of-Work Checklist

**Before writing any code for a new feature/fix, you MUST:**

1. Transition the YouTrack issue to `Develop`.
2. Ensure you are on `dev` and it's up to date (`git checkout dev && git pull`).
3. Create a feature branch using `git-workflow-manager` agent.
4. Only then proceed with the TDD cycle.

#### Branching Strategy

- Branch from `dev` using the `git-workflow-manager` agent.
- Branch naming: `feature/<issue-id>-<short-description>`, `fix/<issue-id>-<short-description>`, `chore/<issue-id>-<short-description>`.
- Commit messages follow Conventional Commits: `feat(DCE-XX):`, `fix(DCE-XX):`, `chore(DCE-XX):`, `docs(DCE-XX):`, `test(DCE-XX):`, `refactor(DCE-XX):` — where `DCE-XX` is the YouTrack issue ID. Every commit **must** include the issue ID so YouTrack can track it.
- Commits are Co-Authored-By: Claude `<noreply@anthropic.com>`.

#### PR Creation

- Use `gh pr create` via Bash, or the VS Code GitHub extension.
- PR title: short (<70 chars), matches the YouTrack issue summary.
- PR body follows the template in `.github/PULL_REQUEST_TEMPLATE.md`.
- After PR creation: transition the YouTrack issue to `Review` state.

#### GitHub Templates

- **PR template**: `.github/PULL_REQUEST_TEMPLATE.md` — auto-loaded on PR creation.
- **Issue templates**: `.github/ISSUE_TEMPLATE/` — `bug_report.md`, `feature_request.md`, `task.md`.
- Blank issues are disabled (`config.yml`). All issues must use a template.
- When creating GitHub issues or PRs, follow the structure defined in these templates.

#### Pre-Commit Hooks

- Git-level pre-commit hooks are configured in `.pre-commit-config.yaml`.
- Hooks: ruff (lint + format), trailing-whitespace, end-of-file-fixer, check-yaml, check-merge-conflict, check-added-large-files, mypy.
- Run manually: `make pre-commit` or `uv run pre-commit run --all-files`.
- Install hooks: `uv run pre-commit install`.

#### Review → Merge Flow

1. Create PR → transition YouTrack issue to `Review`.
2. Launch `code-reviewer` for code quality review.
3. If PR approved and merged:
   - **First**, check the YouTrack Knowledge Base for relevant articles (`article_search`). Create or update KB articles to document the change (architecture decisions, runbooks, API docs) using `documentation-engineer`.
   - **Then**, transition the YouTrack issue to `Review` → `Merged`.
4. If PR has unresolved change requests → transition YouTrack issue to `Rework`.

### Knowledge Base
Use `article_get(articleId="DCE-A-27")` to read the index of all articles in KB.

The project has KB articles (DCE-A-NN) documenting every DDD/CQRS/ES pattern used in the library. The root article is DCE-A-1; Articles are in **Markdown mode** (`usesMarkdown: true`). Cross-article links use the `DCE-A-XX` syntax (not markdown links).
To read an article: `article_get(articleId="DCE-A-NN")`
To update an article: `article_update(articleId="DCE-A-NN", content="...", usesMarkdown=true)`

### Issues

When creating issues, use the YouTrack MCP tools:
- `issue_create` — create a new issue (requires at minimum `summary`; accepts `description`, `assigneeLogin`, `stateName`, `parentIssueId`, `links`)
- `issue_update` — update summary, description, parent
- `issue_change_state` — transition an issue through workflow states
- `issue_assign` — assign to a user
- `issue_comment_create` — add a comment
- `issue_lookup` / `issue_details` — read issue data
- `issues_search` — search by text, project, state, type, assignee, dates
- `issues_list` — list with filters and pagination

#### Creating Issues

When creating a new issue, always consider:

1. **Pick the right type** (see Issue Types below). If it's a new capability → Feature. If it's a subdivision of that capability → Feature Task. If it's a bug → Bug. If it's non-code work → Task.
2. **Set the initial state**. New issues start in **Open** by default. If the issue needs design clarification first, set it to **Refine**.
3. **Link appropriately**:
   - Feature Tasks must be linked to their parent Feature via `Subtask` link.
   - Features within an Epic should be linked to the Epic via `Subtask` link.
   - If issue A blocks issue B, link with `Depend` (A is required for B).
   - Related issues that don't fit the above → `Relates`.
4. **Scope Feature Tasks correctly** — each must be completable by a single agent in one pass. If a Feature Task is too large, split it further.
5. **Write a clear description** — include context, acceptance criteria, and any relevant KB article references (`DCE-A-XX`).

#### Editing / Progressing Issues

When updating an existing issue:

1. **State transitions must follow valid paths** (see Valid Transitions below). Never skip states (e.g., don't jump from Develop directly to Review).
2. **Moving to Rework** — add a comment explaining what failed and what needs to change.
3. **Moving to Done** — ensure the issue has passed through Test, Review, Maerged and Staging first.
4. **Update descriptions** when scope or design decisions change during development.

### Issue Types

- **Bug** — An unexpected behavior or defect in existing functionality that produces incorrect results or errors.
- **Feature** — A new capability or enhancement to be added to the library. Decomposed into **Feature Tasks** via `Subtask` links.
- **Feature Task** — A coding task that is a subdivision of a Feature. Must be scoped small enough for a single agent (with its context window) to implement and test in one pass.
- **Task** — A unit of work that doesn't involve code changes directly (e.g., documentation, research, configuration, tooling setup).
- **Performance Problem** — A measurable degradation in speed, memory usage, or resource consumption under expected load.
- **Epic** — A large body of work that spans multiple issues; used for grouping related features or initiatives.
- **Usability Problem** — An issue with the developer experience (DX): unclear APIs, confusing naming, poor ergonomics of the public interface.


### Issue Link Types

| Link Type | Directed | Source → Target | Target → Source |
|---|---|---|---|
| `Relates` | No | relates to | relates to |
| `Depend` | Yes | is required for | depends on |
| `Duplicate` | Yes | is duplicated by | duplicates |
| `Subtask` | Yes | parent for | subtask of |

Use `issue_link_add` to link issues, `issue_links` to list links for an issue.

### Issue Stage (States) (DCE Workflow)

| State | Description |
|---|---|
| **Open** | Newly created, awaiting triage. |
| **Refine** | Needs clarification on scope, acceptance criteria, or design before development can begin. |
| **Develop** | Actively being implemented. |
| **Rework** | Returned from a later stage; needs fixes before progressing again. |
| **Test** | Implementation complete; awaiting automated/manual testing. |
| **Review** | Tests passed; awaiting code or design review. |
| **Staging** | Review approved; deployed to staging for final verification. |
| **Done** | Verified and complete. Terminal state. |

#### Valid Transitions

```
Open ──┐
       ├──→ Develop ──→ Test ──→ Review ──→ Merged ──→ Staging ──→ Done
Rework─┘              │          │           │           │
       ←──────────────┘←─────────┘←──────────┘←──────────┘
                          (backward → Rework)
```

| From | To |
|---|---|
| Open, Rework, Refine | Develop |
| Develop | Test |
| Test | Review or Rework |
| Review | Merged or Rework |
| Merged | Staging or Rework |
| Staging | Done or Rework |

### MCP Tool Conventions

- All YouTrack MCP tools are prefixed `mcp__youtrack__`
