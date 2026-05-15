# Linking Patterns — YouTrack Issue Management

Link types, their semantics, and when to use each.

## Default Link Types

YouTrack provides default link types. Projects may also define custom ones. All available types are discovered during the project discovery protocol.

### depends on / is required for

**Directed. Asymmetric.**

Use when one issue blocks another from proceeding.

```
PROJ-42 depends on PROJ-99 → PROJ-99 is required for PROJ-42
```

- PROJ-42 cannot be resolved until PROJ-99 is resolved (enforced by workflow in many projects)
- The dependency is directional — PROJ-99 doesn't depend on PROJ-42
- Common for: API endpoints blocking frontend work, infrastructure blocking feature work, bug fixes blocking release

### subtask of / parent for

**Aggregation. Hierarchical.**

Use to decompose work into smaller units.

```
PROJ-42 is subtask of PROJ-10 → PROJ-10 is parent for PROJ-42
```

- Subtask hierarchy supports nesting (epic → story → task)
- Subtask state doesn't automatically propagate to parent (use `youtrack-state-propagation` for that)
- Common for: Epic decomposition, feature breakdown, work splitting

### relates to

**Directed. Informational.**

Use to connect related issues without implying dependency.

```
PROJ-42 relates to PROJ-55
```

- No state enforcement
- Informational only — helps with discovery and context
- Common for: Thematically related work, issues in the same component, cross-team awareness

### duplicates / is duplicated by

**Directed. Duplicate detection.**

Use when one issue reports the same problem or request as another.

```
PROJ-42 duplicates PROJ-88 → PROJ-88 is duplicated by PROJ-42
```

- The "original" issue (PROJ-88) is the one to keep open
- The duplicate (PROJ-42) should typically be resolved/closed
- Common for: Bug report triage, feature request consolidation

## When to Use Each Link Type

| Scenario | Link Type |
|----------|-----------|
| Frontend work cannot start until API is done | depends on |
| Breaking a large task into smaller pieces | subtask of |
| Two issues touch the same component but aren't blocking | relates to |
| Same bug reported twice | duplicates |
| Tracking the cause of a bug | relates to |
| Feature A must ship before Feature B | depends on |
| Organizing work under an epic | subtask of |
| A task is part of a user story | subtask of |

## Subtask Hierarchy Patterns

### Epic → Story → Task (Three-Level)

```
PROJ-10 (Epic: Redesign Auth)
  └── PROJ-42 (Story: Login Flow)
      ├── PROJ-100 (Task: Build Login UI)
      ├── PROJ-101 (Task: Add Form Validation)
      └── PROJ-102 (Task: Write Tests)
```

- Epic defines the goal — no direct work items
- Story is a user-visible unit of value
- Task is a concrete, assignable work item

### Flat Task List (Two-Level)

```
PROJ-10 (Epic: Security Audit)
  ├── PROJ-42 (Task: Penetration Test)
  ├── PROJ-43 (Task: Dependency Scan)
  └── PROJ-44 (Task: Review Access Logs)
```

Use when the work is simple enough that stories add overhead.

### Single Task with Subtasks

```
PROJ-42 (Task: Database Migration)
  ├── PROJ-100 (Task: Write Migration Script)
  ├── PROJ-101 (Task: Test on Staging)
  └── PROJ-102 (Task: Schedule Downtime)
```

Use for complex tasks that benefit from decomposition.

## Dependency Chain Patterns

### Sequential Chain

```
PROJ-100 (Build API) → PROJ-101 (Integrate Frontend) → PROJ-102 (End-to-End Tests)
```

Each link depends on the previous one.

### Fan-In

```
PROJ-100 (API) ─┐
PROJ-101 (DB)   ├→ PROJ-200 (Integration)
PROJ-102 (Auth) ─┘
```

Multiple issues must be done before the dependent can proceed.

### Fan-Out

```
                ┌→ PROJ-200 (Web)
PROJ-100 (API) ─┼→ PROJ-201 (Mobile)
                └→ PROJ-202 (CLI)
```

One issue unblocks multiple dependents.

## Duplicate Management

When the user says an issue is a duplicate:
1. Ask which issue is the "original" (to keep open)
2. Link as duplicate
3. Optionally add a comment on the duplicate explaining why
4. Ask if the duplicate should be resolved/closed

## Link Discovery

To find existing links on an issue:
```
mcp__youtrack__get_issue("PROJ-42")
```
The response includes a `links` array with all linked issues, link types, and directions.

## Creating Links

```
mcp__youtrack__link_issues(
  issue1: "PROJ-42",
  issue2: "PROJ-99",
  link_type: "depends on"
)
```

- issue1 is the source, issue2 is the target
- The link_type is the outward name from issue1's perspective
- YouTrack automatically creates the reciprocal link on issue2

## Removing Links

If a link needs to be removed (e.g., wrong link type, issue no longer related), use `mcp__youtrack__link_issues` with the inverse operation. Check the MCP tool signature for removal options.
