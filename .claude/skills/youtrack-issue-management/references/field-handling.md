# Field Handling — YouTrack Issue Management

How to set each field type, handle required fields, and manage multi-value operations.

## Field Type Reference

### Enum Fields (enum[<bundle>])

Examples: Type (Bug/Feature/Task), Priority (Critical/Major/Normal/Minor)

```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Priority", "value": "Critical"}
])
```

- Single value only
- Value must match one of the allowed values from the bundle
- Case-sensitive — match exactly as displayed in the discovery cache

### State Fields (state[<bundle>])

Examples: State (Open/In Progress/Fixed/Verified/Closed)

```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "State", "value": "In Progress"}
])
```

- Validate against the state machine (see discovery cache)
- Some states are terminal (isResolved: true) — transitions back may be restricted
- State changes follow a defined order; skipping states may be allowed or denied depending on configuration

### User Fields

Examples: Assignee, Reviewer

Assignment is handled via the dedicated assignee tool:
```
mcp__youtrack__change_issue_assignee(issue_id: "PROJ-42", assignee: "jdoe")
```

For custom user fields, use the standard field update:
```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Reviewer", "value": "jdoe"}
])
```

### String Fields

Free-text fields. Set directly:
```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "CustomString", "value": "any text value"}
])
```

### Date Fields

```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Due Date", "value": "2026-06-01"}
])
```

Format: YYYY-MM-DD.

### Period Fields

Duration values:
```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Estimation", "value": "2h 30m"}
])
```

### Integer/Float Fields

```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Story Points", "value": 5}
])
```

### Version Fields (version[<bundle>])

```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Fix version", "value": "2.3.0"}
])
```

Value must match a version from the project's version bundle.

### Build Fields (build[<bundle>])

```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Found in build", "value": "build-142"}
])
```

### Boolean Fields

```
mcp__youtrack__update_issue(issue_id: "PROJ-42", fields: [
  {"name": "Flagged", "value": true}
])
```

## Required Field Handling

### During Creation

1. Read the required fields list from the discovery cache
2. Check what the user provided
3. For any missing required field:
   - If there's a default value, use it
   - If the field has a clear reasonable default (e.g., Priority: Normal), propose it
   - Otherwise, ask the user: "The Type field is required. Should this be Bug, Feature, or Task?"

### During Update

Required field rules don't strictly apply to updates (the field already has a value from creation). However, if clearing a required field, warn the user.

## Multi-Value Field Operations

Some fields support multiple values (e.g., tags, some custom fields).

### Adding a Value

To add to an existing multi-value field, you must explicitly include the current values plus the new one:
```
// WRONG — this replaces all current values
fields: [{"name": "MultiField", "value": "NewValue"}]

// RIGHT — get current values first, then append
current: ["OldValue1", "OldValue2"]
fields: [{"name": "MultiField", "value": "OldValue1, OldValue2, NewValue"}]
```

### Removing a Value

Similarly, filter out the value to remove:
```
current: ["A", "B", "C"]
remove "B"
fields: [{"name": "MultiField", "value": "A, C"}]
```

## Default Value Handling

From the discovery cache, check if fields have defaults:
- The `Type` field often defaults to "Task"
- The `State` field defaults to the first non-resolved state (typically "Open")
- The `Priority` field may default to "Normal"

When creating issues:
- Apply defaults automatically for fields the user didn't specify
- Mention what defaults were applied: "Created as Task with Normal priority"

## Custom Field Name Resolution

Project-specific fields may have names that differ from the standard set. Always match against the discovery cache's exact field names. If a user says "set severity to High" but the field is named "Bug Severity", use the actual field name from the cache.

## Error Recovery

- If a field update fails because the value isn't in the allowed set: re-read the discovery cache for valid values and suggest the closest matches
- If a state transition is rejected: re-read the state machine and suggest valid transitions
- If a user assignment fails: `mcp__youtrack__find_user(query: "$name")` to find the correct username/login
