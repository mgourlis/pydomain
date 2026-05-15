---
name: youtrack-project-discovery
description: >-
  Discover and cache YouTrack project configuration — fields, states, link types,
  tags, users. Internal utility used by other YouTrack skills via <prerequisite>.
disable-model-invocation: true
user-invocable: false
argument-hint: [project]
arguments: [project]
allowed-tools:
  - mcp__youtrack__get_project
  - mcp__youtrack__get_issue_fields_schema
  - mcp__youtrack__find_user_groups
  - mcp__youtrack__search_issues
  - mcp__youtrack__find_user
---

# YouTrack Project Discovery

> Internal utility that discovers and caches project configuration. Loaded by other YouTrack skills via `<prerequisite>`, never invoked directly by users. Handles the MCP call sequence, parses responses into a normalized config, and writes a session-memory cache for the calling skill to read.

## Prerequisites

- YouTrack MCP server must be configured and running
- `$project` — the project short name (e.g., `PROJ`, `MYAPP`). When loaded as a prerequisite, derive `$project` from the calling skill's context (e.g., extract project short name from an issue ID like `PROJ-42` → `PROJ`).

## Discovery Protocol

### Step 1: Check Cache

```
/memories/session/youtrack-{PROJECT}-config.md
```

Where `{PROJECT}` is `$project` uppercased for consistent cache keys. If the cache exists and was written in the current session, skip discovery — load and return.

### Step 2: Fetch Project Info

```
mcp__youtrack__get_project($project)
```

Extract:
- `shortName` — project ID
- `name` — display name
- `fields` — custom field definitions (names, types, allowed values)
- `leader` — project lead (default assignee)

### Step 3: Fetch Field Schema

```
mcp__youtrack__get_issue_fields_schema($project)
```

Extract:
- Required fields list (fields with `required: true`)
- Per-field type: `state[<bundle>]`, `enum[<bundle>]`, `user[<bundle>]`, `string`, `date`, `period`, `integer`, `float`, `boolean`, `version[<bundle>]`, `build[<bundle>]`, `text`
- Per-field allowed values (for enum, state, version, build, user bundles)
- Default values

### Step 4: Fetch Users and Groups

```
mcp__youtrack__find_user_groups($project)
mcp__youtrack__search_issues("project: $project sort by: updated desc") limit 50
```

Extract:
- Available user groups for assignment
- Recent issue context (assignee names, common tags, active states)
- Extract unique assignee usernames from recent issues
- Resolve usernames via `mcp__youtrack__find_user` if needed

### Step 5: Detect State Machines

Analyze state-type fields from Step 3:
- For each `state[<bundle>]` field, extract the ordered list of values
- State machine defines valid transitions (typically linear: A→B→C)
- Detect issue-type associations (which states apply to which issue types)

### Step 6: Write Cache

Write `/memories/session/youtrack-{PROJECT}-config.md` (uppercase `$project` for consistent cache keys):

```markdown
# YouTrack Project Config: {PROJECT}

## Project Info
- Name: {get_project.name}
- Short Name: {get_project.shortName}
- Leader: {get_project.leader}

## Fields
| Field | Type | Required | Allowed Values |
|-------|------|----------|---------------|
| Type | enum[IssueType] | yes | Bug, Feature, Task, Story, Epic |
| State | state[State] | yes | Open, In Progress, Fixed, Verified |
| Priority | enum[Priority] | yes | Critical, Major, Normal, Minor |
| Assignee | user | no | — |

## State Machines
### Default State Machine (applies to Bug, Task, Feature)
- Open → In Progress → Fixed → Verified → Closed
- Open → Won't Fix / Duplicate / Incomplete (terminal states)

## Link Types
| Type | Direction | Description |
|------|-----------|-------------|
| depends on | outward | Blocks until resolved |
| is required for | inward | Unblocks this |
| subtask of | outward | Child of |
| parent for | inward | Parent of |

## Tags (from recent issues)
| Tag | Occurrences |
|-----|-------------|
| needs-review | 12 |
| blocked-by-external | 3 |

## Users (from recent issues)
| Username | Full Name |
|----------|-----------|
| jdoe | John Doe |
```

## Cache Key Conventions

- Cache file is at `/memories/session/youtrack-{PROJECT}-config.md` (`{PROJECT}` = `$project` uppercased)
- Cache is valid for the duration of the session — no TTL needed
- If the user switches to a different project, run discovery again for that project
- Multiple projects can be cached simultaneously (one file each)

## Error Handling

- If `get_project` fails (project not found): report the project doesn't exist or the MCP server isn't connected
- If `get_issue_fields_schema` fails: proceed with only `get_project` data
- If `search_issues` returns empty: note that the project may be new, skip tag/user discovery
- If MCP server is unreachable: report the YouTrack MCP server needs to be configured and running

## Usage by Calling Skills

Calling skills load this prerequisite and then read the cache:

```
<prerequisite>
Load `youtrack-project-discovery` to discover and cache project configuration before any operation.
</prerequisite>
```

The calling skill can then:
1. Read `/memories/session/youtrack-{PROJECT}-config.md` for the field map (where `{PROJECT}` is `$project` uppercased)
2. Validate requested operations against discovered fields
3. Provide autocomplete-like suggestions for enum/state values

## References

- **[`${CLAUDE_SKILL_DIR}/references/mcp-call-sequences.md`]** — Full MCP tool call sequences with parameter examples for each discovery step
