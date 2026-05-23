# MCP Call Sequences — YouTrack Project Discovery

Full parameter examples for each MCP tool used during discovery.

## get_project

Returns project metadata, custom field definitions, and bundle values.

```
mcp__youtrack__get_project(project: "PROJ")
```

### Response Parsing

The response contains:
- `shortName` — the project ID (e.g., "PROJ")
- `name` — display name
- `fields` — array of custom field objects, each with:
  - `name` — field display name
  - `fieldType` — type reference
  - `isRequired` — boolean
  - `defaultValue` — optional default

### Extracting Field Types

Field type strings follow the pattern `type[<bundle>]`:
- `state[State]` — state machine field, bundle name is "State"
- `enum[IssueType]` — enum field, bundle name is "IssueType"
- `enum[Priority]` — priority enum
- `user[]` — user-type field
- `string` — free text
- `date` — date field
- `integer` — numeric
- `version[<bundle>]` — version bundle

## get_issue_fields_schema

Returns detailed field schema including allowed values.

```
mcp__youtrack__get_issue_fields_schema(project: "PROJ")
```

### Response Parsing

The response contains per-field schema:
- `name` — field machine name (e.g., "State", "Priority")
- `type` — full type string
- `required` — boolean
- `defaultValue` — optional default
- For enum/state fields: `values` array with `{name, id, isResolved, color}`
- For user fields: bundle reference
- For version fields: `values` array with version details

### Detecting State Machines

State bundles have an implicit order (first → last in the values array). Terminal states (resolved) have `isResolved: true`.

Example state bundle:
```
values: [
  { name: "Open", isResolved: false },
  { name: "In Progress", isResolved: false },
  { name: "Fixed", isResolved: false },
  { name: "Verified", isResolved: false },
  { name: "Closed", isResolved: true },
  { name: "Won't Fix", isResolved: true },
  { name: "Duplicate", isResolved: true },
]
```

The valid transition from a state is typically to the next non-resolved state, or to any terminal state.

## find_user_groups

Returns user groups available in the project.

```
mcp__youtrack__find_user_groups(project: "PROJ")
```

### Response Parsing

Each group has:
- `name` — display name
- `id` — group ID
- `usersCount` — number of members

## search_issues (for context)

Returns recent issues for tag/user extraction.

```
mcp__youtrack__search_issues(query: "project: PROJ #Unresolved sort by: created desc", limit: 50)
```

Or for specific issue types:
```
mcp__youtrack__search_issues(query: "project: PROJ Type: Bug #Unresolved", limit: 30)
```

### Response Parsing

Extract from each issue:
- `idReadable` — issue ID (e.g., "PROJ-42")
- `summary` — title
- `fields` — populated field values (for tag/user extraction)
- `customFields` — custom field values

### Tag Extraction from Recent Issues

Parse the `tags` array from each issue response. Collect unique tag names and count occurrences.

## find_user

Resolves a user by name or username.

```
mcp__youtrack__find_user(query: "John")
mcp__youtrack__find_user(query: "jdoe")
```

### Response Parsing

Results include:
- `login` — username
- `fullName` — display name
- `id` — user ID

## Session Memory Cache Format

After discovery completes, write to `/memories/session/youtrack-{PROJECT}-config.md` using the format shown in the SKILL.md. The cache is read by all other YouTrack skills.
