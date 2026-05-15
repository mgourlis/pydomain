---
name: youtrack-project
description: YouTrack issue management for the DCE project. Activate when creating, editing, transitioning issues, or working with KB articles. Provides issue types, states, valid transitions, and KB access patterns.
---

# YouTrack Issue Management — DCE Project

## Project Config

| Item | Value |
|---|---|
| Project ID | `DCE` (shortName), `0-1` (internal) |
| Project name | `py-ddd-cqrs-es` |
| YouTrack URL | `https://mgourlis.youtrack.cloud` |
| MCP server | `youtrack-mcp` v0.11.0 |
| Timezone | `Europe/Moscow` |
| Current user | `admin` (myrgourlis@gmail.com) |

## Issue Types

- **Bug** — Unexpected behavior or defect in existing functionality.
- **Feature** — New capability or enhancement. Decomposed into **Feature Tasks** via `Subtask` links.
- **Feature Task** — Subdivision of a Feature. Must be scoped for a single agent to implement and test in one pass.
- **Task** — Non-code work (documentation, research, configuration, tooling setup).
- **Performance Problem** — Measurable degradation in speed, memory, or resource consumption.
- **Epic** — Large body of work spanning multiple issues; groups related features.
- **Usability Problem** — Developer experience issue: unclear APIs, confusing naming, poor ergonomics.

## Issue States (DCE Workflow)

| State | Description |
|---|---|
| **Open** | Newly created, awaiting triage. |
| **Refine** | Needs clarification on scope, acceptance criteria, or design. |
| **Develop** | Actively being implemented. |
| **Rework** | Returned from a later stage; needs fixes. |
| **Test** | Implementation complete; awaiting testing. |
| **Review** | Tests passed; awaiting code/design review. |
| **Merged** | Review approved; PR merged to `dev`. |
| **Staging** | Merged to `staging` for final verification. |
| **Done** | Merged to `main`, release tagged. Terminal state. |

## Valid Transitions

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

## Creating Issues

1. **Pick the right type**: new capability → Feature; subdivision → Feature Task; bug → Bug; non-code → Task.
2. **Set initial state**: new issues start in **Open**. If design clarification needed first, set to **Refine**.
3. **Link appropriately**:
   - Feature Tasks → parent Feature via `Subtask of`.
   - Features → parent Epic via `Subtask of`.
   - Blocking: `Depends on` (A blocks B).
   - Related: `Relates`.
4. **Scope Feature Tasks** — each must be completable by a single agent in one pass.
5. **Clear description** — include context, acceptance criteria, and KB article references (`DCE-A-XX`).

Use `issue_link_add` to link issues, `issue_links` to list links.

## Editing / Progressing Issues

1. **Follow valid transition paths** — never skip states (e.g., no Develop → Review).
2. **Moving to Rework** — add a comment explaining what failed and what needs to change.
3. **Moving to Done** — ensure the issue passed through Test, Review, Merged, and Staging first.
4. **Update descriptions** when scope or design decisions change.

## Knowledge Base

KB articles (`DCE-A-NN`) document every DDD/CQRS/ES pattern. Root article: `DCE-A-1`.

- Articles are in **Markdown mode** (`usesMarkdown: true`).
- Cross-article links use `DCE-A-XX` syntax (not markdown links).
- Read index: `article_get(articleId="DCE-A-27")`
- Read article: `article_get(articleId="DCE-A-NN")`
- Update article: `article_update(articleId="DCE-A-NN", content="...", usesMarkdown=true)`
