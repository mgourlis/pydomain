---
name: youtrack-state-propagation
description: >-
  Monitor and propagate issue states across linked issues in YouTrack.
  Validates state machine transitions, checks dependency chains, ensures parent/subtask consistency.
when_to_use: >-
  When the user changes an issue state with linked issues, resolves an issue, checks dependency
  status, or asks about blocked/blocking issues. Triggers on: "resolve PROJ-42 and its subtasks",
  "what's blocking PROJ-42", "are all subtasks done", "can I close the epic",
  "blocked issues report", "hierarchy health check", "mark PROJ-42 as done",
  "dependencies resolved", "can I proceed with PROJ-42", "show me blockers",
  "propagate state", "batch resolve subtasks", "which issues are ready".
argument-hint: [issue-id]
arguments: [issue_id]
allowed-tools:
  - mcp__youtrack__get_issue
  - mcp__youtrack__update_issue
  - mcp__youtrack__search_issues
---

# YouTrack State Propagation

> Validate and propagate issue states across linked issues. Advisory — always confirm with user before batch changes. For single-issue updates use `youtrack-issue-management`; for understanding issue context use `youtrack-issue-analyzer`.

## Prerequisites

- YouTrack MCP server must be configured and running
- Issue ID (e.g., `PROJ-42`) — provided as argument
- `<prerequisite>` Load `youtrack-project-discovery` to discover project state machines before validating transitions. `</prerequisite>`

## Core Principle

State propagation is **advisory, not automatic**. Never batch-change linked issues without:
1. Showing the user what will change and why
2. Listing all affected issues with before/after states
3. Getting explicit confirmation before applying

## State Validation Protocol

### 1. Load State Machine

From the discovery cache at `/memories/session/youtrack-{PROJECT}-config.md` (extract project short name from `$issue_id`), load the state machine definition for the project.

### 2. Fetch Issue

```
mcp__youtrack__get_issue("$issue_id")
```

Extract:
- `state` — current state value
- `customFields[].state` — if project uses custom state field
- Links — all linked issues

### 3. Validate Transition

Check the target state against the state machine:
- Is the target state a valid transition from the current state?
- If not, list all valid transitions from the current state
- Check for terminal states (isResolved: true) — once resolved, transitions back to open may be restricted

### 4. If Invalid

Report to the user:
- Current state → target state is not a valid transition
- Valid transitions from current state: [list]
- Ask: "Did you mean one of these?"

## Link Graph Traversal

### 1. Fetch the Issue with Links

`mcp__youtrack__get_issue` returns the issue with its links. Each link includes:
- `linkType` — the relationship type
- `direction` — outward or inward
- `linkedIssue` — the other issue's ID and summary

### 2. Build the Dependency Tree

For each linked issue, fetch its state via `mcp__youtrack__get_issue`. Recursively expand based on the operation type:
- For **resolve**: expand subtasks and dependencies
- For **blocker check**: expand "depends on" relations
- For **hierarchy**: expand "subtask of" / "parent for" relations

Build an in-memory tree:
```
PROJ-10 (Epic, In Progress)
  ├── PROJ-42 (Story, In Progress)
  │   ├── PROJ-100 (Task, Open) → blocker: PROJ-200 (needs review)
  │   └── PROJ-101 (Task, Fixed)
  └── PROJ-43 (Story, Open)
      └── PROJ-102 (Task, Open)
```

### 3. Depth Limit

Cap traversal at 3 levels deep to avoid excessive API calls. Report if the graph extends beyond that.

## Propagation Rules

### Parent → Subtask

When the user resolves a parent issue:
1. Fetch all subtasks
2. Check if any subtasks are unresolved
3. If unresolved subtasks exist: warn that they'll be left unresolved unless explicitly handled
4. Offer to resolve them: "$issue_id has 2 unresolved subtasks. Resolve them too?"
5. If confirmed, resolve subtasks first (bottom-up), then the parent

### Subtask → Parent

When the user resolves a subtask:
1. Fetch the parent issue
2. Check if ALL siblings are now resolved
3. If yes: "All subtasks of PROJ-10 are now resolved. Resolve PROJ-10 as well?"
4. If no: Report how many subtasks remain

### Dependency (depends on)

When checking if an issue can proceed:
1. Fetch all "depends on" linked issues
2. Check their states — are they all resolved?
3. If any dependency is unresolved: the issue is BLOCKED
4. If all dependencies are resolved: the issue is READY TO PROCEED

When the user asks what a resolved dependency unblocks:
1. Find all issues that depend on it (via "is required for" links)
2. For each, check if ALL dependencies are now resolved
3. Report newly unblocked issues and offer to update their state

### Duplicates

When the user resolves an issue:
1. Check if other issues are marked as duplicates of this one
2. If yes: "PROJ-88 and PROJ-99 are duplicates of $issue_id. Resolve them too?"
3. Always ask before resolving duplicates — the original may have different scope

### Relates To

No automatic propagation. The "relates to" link is informational. If the user changes an issue state, mention related issues but don't suggest propagation.

## Dependency Status Checks

One-time queries to assess dependency health. These are snapshots, not continuous monitoring.

### Single Issue Status

Given an issue ID, check if it can proceed:

```
1. mcp__youtrack__get_issue(issue_id) → extract "depends on" links
2. For each dependency: get_issue → check state
3. Report:
   - BLOCKED: list unresolved dependencies
   - READY: all dependencies met, clear to proceed
```

### Project-Wide Dependency Scan

Find all issues with unresolved dependencies across the project:

```
mcp__youtrack__search_issues(query: "project: $PROJECT State: Open has: {depends on}" )

For each result:
  1. get_issue → fetch dependency links
  2. Check each dependency's state
  3. Classify: BLOCKED (unresolved deps) or READY (all deps met)

Report:
  🔴 BLOCKED (3 issues): PROJ-42, PROJ-55, PROJ-78
  🟢 READY (2 issues): PROJ-33, PROJ-61
```

## Batch State Operations

### Resolve All Subtasks

```
1. Fetch parent issue → get subtask links
2. For each subtask:
   a. Fetch current state
   b. Validate: can it be resolved from current state?
   c. If any subtask has unresolved dependencies: flag as blocked
3. Present the plan:
   | Issue | Current State | Target State | Status |
   |-------|---------------|-------------|--------|
   | PROJ-100 | Open | Resolved | OK |
   | PROJ-101 | In Progress | Resolved | OK |
   | PROJ-102 | Open | BLOCKED — depends on PROJ-200 |
4. Ask for confirmation
5. Execute: update each issue via `mcp__youtrack__update_issue`
6. Check if parent can now be resolved
```

### Resolve Chain (Issue + Dependencies)

```
1. Build the full dependency graph (following "depends on" links)
2. Topological sort: resolve leaves first, work up
3. Validate each transition
4. Present plan → confirm → execute bottom-up
```

## Reporting

### Blocked Issues Report

```
mcp__youtrack__search_issues(query: "project: $PROJECT State: Open has: {depends on}")

For each result:
  - Fetch its dependencies via get_issue
  - Mark as BLOCKED if any dependency is unresolved
  - Mark as READY if all dependencies are resolved
```

### Ready-to-Proceed Report

Issues that are Open but have all dependencies resolved:
```
mcp__youtrack__search_issues(query: "project: $PROJECT State: Open has: {depends on}")
→ Filter to those where all dependencies are resolved
→ Report: "You can work on PROJ-42, PROJ-55 — all dependencies are met"
```

### Stale Issues Report

Issues in progress with no recent updates:
```
mcp__youtrack__search_issues(query: "project: $PROJECT State: {In Progress} sort by: updated asc")
→ Flag issues not updated in >14 days
```

### Hierarchy Health Check

For a parent/container issue:
```
1. Fetch parent + all subtasks
2. Check consistency:
   - Are subtasks without a parent? (orphaned)
   - Is the parent resolved but subtasks are open? (leaked subtasks)
   - Are subtasks in states beyond the parent? (state drift)
3. Report anomalies
```

## After Completing a Propagation

- Report which issues were changed and their new states
- Report which issues were skipped (and why — blocked, invalid transition, user declined)
- If any issues remain blocked, suggest next steps
- Offer to add comments on the changed issues explaining the propagation

## References

- **[`${CLAUDE_SKILL_DIR}/references/state-machines.md`]** — State machine rules: transition validation, per-issue-type machines, guard and action handling
- **[`${CLAUDE_SKILL_DIR}/references/propagation-rules.md`]** — Complete rule set for each link type, conflict resolution, rollback strategy
