---
name: youtrack-issue-management
description: >-
  Create, update, link, assign, tag, and manage YouTrack issues.
  Discovers project fields, states, link types, and tags dynamically via MCP.
when_to_use: >-
  When the user asks to create, update, search, link, assign, or tag a YouTrack issue.
  Triggers on: "create bug/task/feature", "new issue", "update PROJ-42",
  "change priority", "link to PROJ-99", "assign to John", "tag as needs-review",
  "find open bugs", "search issues about auth", "list my issues", "add comment",
  "set type to Feature", "what fields does PROJ have", "who is assigned to PROJ-42".
argument-hint: [project]
arguments: [project]
allowed-tools:
  - mcp__youtrack__get_project
  - mcp__youtrack__get_issue_fields_schema
  - mcp__youtrack__get_issue
  - mcp__youtrack__create_issue
  - mcp__youtrack__create_draft_issue
  - mcp__youtrack__update_issue
  - mcp__youtrack__link_issues
  - mcp__youtrack__manage_issue_tags
  - mcp__youtrack__search_issues
  - mcp__youtrack__find_user
  - mcp__youtrack__find_user_groups
  - mcp__youtrack__change_issue_assignee
  - mcp__youtrack__add_issue_comment
---

# YouTrack Issue Management

> Full CRUD operations for YouTrack issues with dynamic field discovery. For _understanding_ an issue, use `youtrack-issue-analyzer`; for _state propagation_ across linked issues, use `youtrack-state-propagation`; for _documentation_, use `youtrack-knowledge-base`.

## Prerequisites

- YouTrack MCP server must be configured and running
- `$project` — the project short name (e.g., `PROJ`, `MYAPP`)
- `<prerequisite>` Load `youtrack-project-discovery` to discover and cache project configuration before any operation. `</prerequisite>`
- `<prerequisite>` When creating issues, load `task-creation` first for description quality — structured context, acceptance criteria, imperative titles — then use this skill for YouTrack-specific field handling, linking, and categorization. `</prerequisite>`

## Discovery Protocol

Before any operation, run the project discovery protocol to cache configuration:

1. Load `youtrack-project-discovery` as prerequisite
2. Read `/memories/session/youtrack-{PROJECT}-config.md` (where `{PROJECT}` is `$project` uppercased) for field map, state machines, link types, and users
3. Use cached config for field validation, value lookup, and type-safe payload construction

## Issue Creation Workflow

### 1. Validate Input Against Discovery Cache

- Read the cached field map from `/memories/session/youtrack-{PROJECT}-config.md`
- Identify all required fields (marked `yes` in the Required column)
- Check if the user provided values for required fields
- If missing required values, prompt the user

### 2. Draft the Description

Apply the `task-creation` skill's writing rules:
- **Title:** imperative mood, 5-10 words, specific (e.g., "Fix token expiry off-by-one" not "Fix bug")
- **Description:** structured with Context, What to Do, Acceptance Criteria, and References (if applicable)
- **Acceptance criteria:** testable, outcome-focused, measurable
- Present the draft to the user for approval before creating

### 3. Determine Issue Type

The user may specify an issue type. Common types:
- Bug, Feature, Task, Story, Epic (from `enum[IssueType]` bundle)
- If not specified, ask or default to "Task"

### 4. Build and Create the Issue

```
mcp__youtrack__create_issue(
  project: "$project",
  summary: "Brief, descriptive title",
  description: "Markdown description with details",
  type: "Bug|Feature|Task|Story|Epic"
)
```

Additional fields can be set in the same call or via subsequent `update_issue` calls:
- Priority: `mcp__youtrack__update_issue(issue_id, fields: [{"name": "Priority", "value": "Critical"}])`
- Assignee: handled separately via `mcp__youtrack__change_issue_assignee`

### 5. Post-Creation Operations

After the issue is created:
- Add links: `mcp__youtrack__link_issues(issue1: "PROJ-42", issue2: "PROJ-99", link_type: "depends on")`
- Add tags: `mcp__youtrack__manage_issue_tags(issue_id: "PROJ-42", operation: "add", tags: ["needs-review"])`
- Add comment: `mcp__youtrack__add_issue_comment(issue_id: "PROJ-42", text: "Context...")`

### 6. Draft Option

For complex issues, create a draft first:
```
mcp__youtrack__create_draft_issue(project: "$project", summary: "...", description: "...")
```
Then iterate with the user before publishing.

## Issue Update Workflow

### 1. Fetch Current State

```
mcp__youtrack__get_issue("PROJ-42")
```

This returns the current field values, state, assignee, tags, and links.

### 2. Identify What Changed

Compare the user's request against the current state. Only modify fields that need changing.

### 3. Apply Updates

For description refinement, apply the `task-creation` skill's writing rules — imperative title, structured description with context/acceptance criteria, no implementation prescriptions.

For field changes:
```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Priority", "value": "Critical"},
  {"name": "State", "value": "In Progress"}
])
```

For assignee changes:
```
mcp__youtrack__change_issue_assignee(issue_id: "PROJ-42", assignee: "jdoe")
```

For comments:
```
mcp__youtrack__add_issue_comment(issue_id: "PROJ-42", text: "Updated priority per team decision.")
```

### 4. State Machine Validation

When changing state, validate against the state machine from the discovery cache:
- Check if the target state is a valid transition from the current state
- Warn if the transition skips states (e.g., Open → Closed)
- Report resolved terminal states (isResolved: true)

## Linking Workflow

### Link Types

From the discovery cache, identify available link types. Common defaults:

| Link Type | Direction | Use Case |
|-----------|-----------|----------|
| depends on | outward | PROJ-42 depends on PROJ-99 being resolved first |
| is required for | inward | PROJ-42 is required for PROJ-99 (inverse of depends on) |
| subtask of | outward | PROJ-42 is a child of PROJ-10 |
| parent for | inward | PROJ-10 is the parent of PROJ-42 (inverse of subtask of) |
| relates to | outward | General association, no dependency implied |
| duplicates | outward | PROJ-42 is a duplicate of PROJ-88 |

### Creating Links

```
mcp__youtrack__link_issues(
  issue1: "PROJ-42",
  issue2: "PROJ-99",
  link_type: "depends on"
)
```

The link_type is the outward name from issue1's perspective. YouTrack automatically creates the reciprocal link (issue2 gets "is required for" back to issue1).

### Link Discovery

To find existing links, use `mcp__youtrack__get_issue` — it returns the issue's link list.

## Assignment Workflow

### Finding Users

```
mcp__youtrack__find_user(query: "John")
```

Returns matching users. Use partial name or username.

### Assigning

```
mcp__youtrack__change_issue_assignee(issue_id: "PROJ-42", assignee: "jdoe")
```

To unassign:
```
mcp__youtrack__change_issue_assignee(issue_id: "PROJ-42", assignee: "")
```

### Group Assignment

If groups are available (from `find_user_groups`), the assignee can be a group:
```
mcp__youtrack__change_issue_assignee(issue_id: "PROJ-42", assignee: "qa-team")
```

## Tag Management

### Adding Tags

```
mcp__youtrack__manage_issue_tags(issue_id: "PROJ-42", operation: "add", tags: ["needs-review", "backend"])
```

### Removing Tags

```
mcp__youtrack__manage_issue_tags(issue_id: "PROJ-42", operation: "remove", tags: ["needs-review"])
```

### Tag Best Practices

- Search for existing tags in the discovery cache before inventing new ones
- Use lowercase, hyphen-separated tag names (e.g., `needs-review`, `blocked-by-external`)
- Prefer shared tags (visible to team) over personal tags (visible only to you)
- Tags are auto-removed on issue resolution in some workflows

## Search Workflow

### Basic Search

```
mcp__youtrack__search_issues(query: "project: $project #Unresolved sort by: updated desc")
```

### Search by Type and State

```
mcp__youtrack__search_issues(query: "project: $project Type: Bug State: Open")
```

### Search by Assignee

```
mcp__youtrack__search_issues(query: "project: $project assignee: jdoe #Unresolved")
```

### Full-Text Search

```
mcp__youtrack__search_issues(query: "project: $project authentication timeout")
```

### Common Search Patterns

| Query | What It Finds |
|-------|---------------|
| `project: X #Unresolved` | All open issues |
| `project: X assignee: me` | Issues assigned to current user |
| `project: X tag: needs-review` | Issues needing review |
| `project: X created: -1w` | Issues created in the last week |
| `project: X State: Open has: {depends on}` | Open issues that have dependencies |
| `project: X State: -Resolved sort by: priority` | Unresolved by priority |

## After Completing an Operation

- Report the issue ID and a link (if the user's YouTrack URL is known): `https://<instance>.youtrack.cloud/issue/{issue.id}`
- Confirm what was created/changed
- Offer to perform follow-up operations (link, tag, assign)

## References

- **[`${CLAUDE_SKILL_DIR}/references/field-handling.md`]** — Per-field-type handling, required field validation, multi-value operations, default values
- **[`${CLAUDE_SKILL_DIR}/references/linking-patterns.md`]** — Link type semantics, reciprocal links, when to use each, hierarchy patterns
- **[`${CLAUDE_SKILL_DIR}/references/tagging-patterns.md`]** — Tag discovery, personal vs shared, naming conventions, auto-remove behavior
