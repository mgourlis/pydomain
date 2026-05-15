# Tagging Patterns — YouTrack Issue Management

Tag management conventions, discovery, and best practices.

## Tag Discovery

Before creating new tags, check what already exists in the project:

1. From the discovery cache: `/memories/session/youtrack-$PROJECT-config.md` includes a Tags section with recently used tags
2. From search: `mcp__youtrack__search_issues(query: "project: $PROJECT")` — scan results for tag usage patterns

## Tag Operations

### Adding Tags

```
mcp__youtrack__manage_issue_tags(
  issue_id: "PROJ-42",
  operation: "add",
  tags: ["needs-review", "backend"]
)
```

Can add multiple tags in one call.

### Removing Tags

```
mcp__youtrack__manage_issue_tags(
  issue_id: "PROJ-42",
  operation: "remove",
  tags: ["needs-review"]
)
```

### Checking Current Tags

Tags are returned in the `mcp__youtrack__get_issue` response. Always check current tags before adding (to avoid duplicates) or removing (to avoid errors).

## Personal vs Shared Tags

YouTrack distinguishes between:
- **Shared tags** — visible to the entire project team. Use for team-wide categorization.
- **Personal tags** — visible only to the tag creator. Use for individual workflow management.

When adding tags for team coordination, always use shared tags.

## Naming Conventions

Follow these conventions for tag names unless the project has established different patterns:

- **Lowercase, hyphen-separated:** `needs-review`, `blocked-by-external`, `high-priority`
- **Prefix categories:**
  - `needs-*` — action required: `needs-review`, `needs-testing`, `needs-deployment`
  - `blocked-*` — blocked state: `blocked-by-external`, `blocked-by-design`
  - `area-*` — component/area: `area-auth`, `area-api`, `area-frontend`
  - `type-*` — classification: `type-bugfix`, `type-refactor`, `type-docs`
  - `priority-*` — when the Priority field isn't sufficient: `priority-hotfix`

## Auto-Remove Behavior

Some workflows automatically remove tags when an issue is resolved. For example, `needs-review` might auto-remove when the state changes to Fixed. This is configured per-project in YouTrack workflows — the discovery cache doesn't capture this, so warn the user if a tag might be auto-managed.

## Common Tag Patterns

### Bug Triage Tags

| Tag | Purpose |
|-----|---------|
| `needs-triage` | Bug hasn't been reviewed yet |
| `needs-reproduction` | Can't reproduce yet |
| `known-issue` | Recognized but not yet prioritized |
| `regression` | Previously working, now broken |
| `customer-reported` | Reported by a customer, not internal |

### Development Workflow Tags

| Tag | Purpose |
|-----|---------|
| `needs-review` | Ready for code review |
| `needs-testing` | Ready for QA |
| `needs-docs` | Documentation needed before closing |
| `in-progress` | Work has started (redundant with State but sometimes used) |

### Blocking Tags

| Tag | Purpose |
|-----|---------|
| `blocked-by-external` | Waiting on external team/vendor |
| `blocked-by-design` | Needs design/UX input |
| `blocked-by-requirements` | Requirements unclear |

## Tag Search Patterns

Find issues by tag:
```
mcp__youtrack__search_issues(query: "project: $PROJECT tag: needs-review")
mcp__youtrack__search_issues(query: "project: $PROJECT tag: blocked-by-external #Unresolved")
```

Multiple tags:
```
mcp__youtrack__search_issues(query: "project: $PROJECT tag: area-auth tag: needs-review")
```

## Creating New Tags

When a tag doesn't exist yet:
1. Check the discovery cache for similar tags
2. Follow the project's naming conventions (if observable from existing tags)
3. Use lowercase, hyphen-separated format
4. Create the tag by simply using it in `manage_issue_tags` with operation `add` — YouTrack creates it automatically

## Tag Hygiene

- Avoid tags that duplicate field values (e.g., `tag: in-progress` when the State field already tracks that)
- Avoid tags that duplicate link semantics (e.g., `tag: blocked` when depends-on links exist)
- Remove stale tags during issue updates (e.g., `needs-review` when review is complete)
- Consolidate similar tags (e.g., `needs-review`, `review-needed`, `awaiting-review` — pick one)
