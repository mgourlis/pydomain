# Article Structure — YouTrack Knowledge Base

YouTrack KB article data model, visibility, nesting, and content conventions.

## Article Data Model

### Core Properties

Each article has:
- **ID** — unique identifier (e.g., `123-456`)
- **Title** — article title (plain text)
- **Content** — Markdown body
- **Project** — owning project short name
- **Parent Article** — optional parent for hierarchy
- **Child Articles** — articles nested under this one
- **Visibility** — who can see the article
- **Author** — creator
- **Created** — timestamp
- **Updated** — last modification timestamp

### Article Hierarchy

Articles can be nested to create a tree structure:
```
Root Level (no parent)
  ├── Child Article (parent = root)
  │   └── Grandchild Article (parent = child)
  └── Another Child
```

The hierarchy is discoverable from `search_articles` responses (which include `childArticles` arrays) and `get_article` responses (which include parent article references).

## Visibility Controls

### Visibility Levels

| Level | Visible To | Use Case |
|-------|-----------|----------|
| Public | Anyone with the YouTrack URL | Public documentation, API docs |
| Project | Project team members | Internal team docs |
| Private | Specific users/groups | WIP drafts, sensitive decisions |

### Setting Visibility

When creating an article, default visibility is project-level. If the article should be public or restricted, specify the visibility in the creation call.

### Visibility Discovery

Check existing articles' visibility to understand the project's conventions. Most project KBs use project-level visibility for all articles.

## Content Format

Articles use Markdown. Common patterns:

### Headers

```markdown
# Title (H1) — used as the article title, not repeated in body
## Section (H2) — major sections
### Subsection (H3) — details within sections
```

### Code Blocks

```markdown
```python
def example():
    pass
```
```

### Links

```markdown
[Link Text](https://example.com)
[Internal Article](article://123-456)
[Issue](issue://PROJ-42)
```

### Images

Images can be embedded if attached to the article. Attachment management is via the YouTrack UI (not currently available through MCP tools).

### Tables

```markdown
| Column 1 | Column 2 |
|----------|----------|
| Value    | Value    |
```

## Common Article Types

### Architecture Decision Record (ADR)

```markdown
# ADR-NNN: Decision Title

## Status
Proposed / Accepted / Deprecated / Superseded

## Context
What is the issue we're addressing?

## Decision
What did we decide?

## Consequences
What became easier/harder because of this?
```

### How-To Guide

```markdown
# How to: Task Description

## Prerequisites
- What you need before starting

## Steps
1. First step
2. Second step

## Troubleshooting
Common problems and solutions
```

### Coding Standards

```markdown
# Coding Standards: Language/Framework

## Naming Conventions
...

## File Organization
...

## Patterns
...

## Anti-Patterns
...
```

### Runbook

```markdown
# Runbook: Scenario

## Trigger
When does this runbook apply?

## Response
1. Check X
2. Verify Y
3. If A, do B

## Escalation
When to escalate and to whom
```

### Project Overview

```markdown
# Project Overview

## What We Build
...

## Key Repositories
...

## Team
...

## Getting Started
...
```

## Article Discovery Patterns

### Finding the Root/Index Article

Many projects have a root article that serves as the KB index. It typically:
- Has no parent article
- Has many child articles
- Title is something like "Knowledge Base", "Documentation", or "Project Docs"

To find it: look at all articles and identify the one with the most children and no parent.

### Navigation by Topic

Articles are typically organized by topic area:
- Architecture / Design
- Development / Coding
- Operations / Deployment
- Runbooks / Incidents
- Processes / Workflows

## Creating Articles

### Where to Place a New Article

1. Identify the relevant category in the existing article tree
2. Place at the appropriate depth:
   - Broad topic → top-level child under the root
   - Specific detail → under the relevant category article
   - Decision record → under the ADR/decisions section

### Naming Conventions

Match the project's existing conventions. From the article tree, observe:
- Title case vs sentence case
- Prefix conventions (e.g., "ADR:", "How to:")
- Length patterns (short vs descriptive)

### Content Structure

Match the structure of similar existing articles. If the project uses ADR templates, follow it. If they use how-to patterns, match that.

## Updating Articles

### What to Update

- **Content changes:** Update the body with new information
- **Title changes:** Only if the scope changed significantly
- **Structure changes:** Reorganize sections while preserving content

### Maintaining History

Articles don't have version history visible via MCP. When making significant updates:
- Add an update note at the bottom of the article
- Don't delete content unless it's definitively wrong — prefer adding corrections
- If an ADR is superseded, note which ADR superseded it (don't delete)

### Staleness Checks

When analyzing articles for staleness:
- Articles not updated in >6 months: potentially stale
- Articles not updated in >12 months: likely stale
- Articles referencing specific versions or dates: check if those are current
- Articles linked from recent issues: likely still relevant
