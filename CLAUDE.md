# pydomain — Python DDD/CQRS/ES Library

### Rules

- **MUST** delegate all implementation to VoltAgent agents; use `code-reviewer` before merge; use `git-workflow-manager` for all git ops.
- **NEVER** edit `src/pydomain/` or `tests/` directly — always delegate.
- **NEVER** execute the entire SDLC in one step — delegate each phase.

### VoltAgent Agents

Invoke by **full qualified name** (e.g. `voltagent-lang:python-pro`). No wrapper agents.

| Agent | Model | Scope |
|---|---|---|
| `voltagent-lang:python-pro` | sonnet | All Python: entities, VOs, aggregates, events, commands, queries, buses, handlers, fakes |
| `voltagent-qa-sec:test-automator` | sonnet | Test fixtures, cases, fakes |
| `voltagent-qa-sec:qa-expert` | sonnet | Test strategy (**read-only**) |
| `voltagent-qa-sec:code-reviewer` | opus | Pre-merge review (**read-only**) |
| `voltagent-qa-sec:architect-reviewer` | opus | DDD boundary review (**read-only**) |
| `voltagent-dev-exp:refactoring-specialist` | sonnet | Safe refactoring |
| `voltagent-dev-exp:git-workflow-manager` | haiku | Branch/commit/PR |
| `voltagent-dev-exp:documentation-engineer` | haiku | KB articles, runbooks |
| `voltagent-meta:agent-organizer` | sonnet | Decompose tickets into task sequences |

> Full catalog: `@docs/voltagent-subagents-guide.md`

---

## YouTrack Project

| Item | Value |
|---|---|
| Project | `DCE` (shortName) · `py-ddd-cqrs-es` · `https://mgourlis.youtrack.cloud` |
| MCP | `youtrack-mcp` v0.11.0 · Timezone `Europe/Moscow` · User `admin` |

> Issue types, states, transitions, and KB access → `@.claude/skills/youtrack-project/SKILL.md`

## State-Driven Workflow

**Every YouTrack state maps to specific actions and agents.** Follow in order. Do not skip states.

| Transition | Step | Required Action | Agent | Gate / Output |
|---|---|---|---|---|
| **Open → Refine** | — | Clarify scope, acceptance criteria | *(direct)* | Clear description + criteria |
| **Open/Rework/Refine → Develop** | ① | Decompose ticket into task sequence | `agent-organizer` | Task list with agent assignments |
| | ② | `git checkout dev && git pull` then create feature branch | `git-workflow-manager` | Branch `feature/DCE-XX-*` |
| **Develop** | ③ | Write domain / application / infra code | `python-pro` | Implementation passes `ruff` |
| | ④ | Write test code (fakes, fixtures, cases) | `test-automator` | Test files in `tests/` |
| **Develop → Test** | ⑤ | Run full test suite, verify all green | *(direct — `runTests`)* | All tests pass |
| **Test → Review** | ⑥ | Code quality + Pydantic v2 review | `code-reviewer` (opus) | Review approved |
| | ⑦ | DDD boundary / layer discipline review | `architect-reviewer` (opus) | No layer violations |
| **Review → Merged** | ⑧ | Create PR, merge after approval | `git-workflow-manager` | PR merged to `dev` |
| **Merged** | ⑨ | Update KB articles if needed | `documentation-engineer` | `DCE-A-NN` articles updated |
| **Merged → Staging** | ⑩ | Merge `dev` → `staging`, verify | `git-workflow-manager` | Staging branch verified |
| **Staging → Done** | ⑪ | Merge `staging` → `main`, tag release, close issue | `git-workflow-manager` + *(MCP `issue_change_state`)* | Release tagged on `main` |
| **Any → Rework** | — | Add comment explaining what failed | *(direct)* | Comment on issue |

**Workflow rules:**
- Steps ①② **must** complete before any code is written.
- Steps ③④ can run in **parallel** (domain + tests touch different paths).
- Steps ⑥⑦ can run in **parallel** (two independent reviews).
- YouTrack state transitions via MCP tools (`issue_change_state`) — do NOT delegate.

### Repo-Wide Refactoring

Cross-layer changes: `refactoring-specialist` for the refactor + `architect-reviewer` for DDD boundary checks. **Human approval gate** required.

### Specialist Agent Assignments

> Use the **full qualified name** with namespace prefix (e.g. `voltagent-lang:python-pro`, NOT `python-pro`).

| Layer | Agent | Use When | Notes |
|---|---|---|---|
| **Planning** | `meta:agent-organizer` | Decomposing tickets | Feed DCE issue types & workflow |
| **DDD** | `lang:python-pro` | VOs, entities, aggregates, events, services, specs | Follow copilot-instructions.md: frozen VOs, mutable entities, Pydantic v2 |
| **CQRS** | `lang:python-pro` | Commands, queries, buses, pipeline behaviors | Commands imperative, queries return typed results |
| **Infra** | `lang:python-pro` | Message bus, UoW, DI, serialization | Wires domain + CQRS; in-memory fakes |
| **Test strategy** | `qa-sec:qa-expert` | Edge-case identification, coverage (**read-only**) | Domain invariant tests, FakeUoW |
| **Test code** | `qa-sec:test-automator` | Fixtures, cases, fakes, conftest.py | `pytest-anyio` + `anyio`; fakes over mocks |
| **Review** | `qa-sec:code-reviewer` | Pre-merge review (opus) | Flag: v1 shims, infra in domain |
| **DDD review** | `qa-sec:architect-reviewer` | DDD boundary review (opus, **read-only**) | No repo for non-root entities |
| **Refactoring** | `dev-exp:refactoring-specialist` | Extract method, rename, reduce complexity | Preserve public API |
| **Docs** | `dev-exp:documentation-engineer` | KB articles (DCE-A-NN), runbooks | Markdown mode; `DCE-A-XX` links |
| **Git & PR** | `dev-exp:git-workflow-manager` | Branch, commit, PR, release merges | `feature/*` → `dev` → `staging` → `main` |
| **Tooling** | `dev-exp:tooling-engineer` | pyproject.toml, Makefile, CI/CD, ruff | `hatchling`; `ruff` target `py312` |
| **Security** | `qa-sec:security-auditor` | Vulnerability assessment (**read-only**) | Rarely needed |

### `qa-expert` vs `test-automator`

- `qa-expert` = **read-only** — designs test strategy, identifies edge cases, coverage gaps.
- `test-automator` = **write-capable** — writes fixtures, parametrized cases, fakes.
- TDD cycle: `qa-expert` designs → `test-automator` writes.

---

## Git Conventions

### Branching

**4-branch model:** `feature/*` → `dev` → `staging` → `main`

| Branch | Purpose | Merges into |
|---|---|---|
| `feature/DCE-XX-*` | Per-issue development | `dev` via PR |
| `dev` | Integration (default) | `staging` |
| `staging` | Pre-release verification | `main` |
| `main` | Release branch | — (tagged only) |

Branch from `dev` via `git-workflow-manager`. Patterns: `feature/`, `fix/`, `chore/<issue-id>-<desc>`.

### Commit Format

Every commit **must** start with `DCE-NN:` for YouTrack auto-linking:

```
DCE-NN: <imperative summary under 72 chars>

<optional body — what and why>

Co-Authored-By: Claude <noreply@anthropic.com>
```

**Examples:**
```
DCE-51: add pipeline behaviors to command bus
DCE-48: resolve optimistic concurrency race in repository
DCE-27: update KB article for pipeline behaviors
```

**Rules:** Always start with `DCE-NN:`. One logical change per commit. Imperative mood.

> PR creation, GitHub templates, pre-commit hooks → `@.claude/skills/git-conventions/SKILL.md`
