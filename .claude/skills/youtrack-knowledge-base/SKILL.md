---
name: youtrack-knowledge-base
description: >-
  Analyze, search, and answer questions from YouTrack Knowledge Base articles.
  Discovers article structure, indexes content, provides grounded answers with citations.
when_to_use: >-
  When the user asks a question that might be answered by project documentation in YouTrack,
  wants to search the KB, or analyze/update KB articles. Triggers on:
  "search the KB for...", "is there documentation about...", "what does the KB say",
  "show me the knowledge base", "list all articles", "analyze our KB",
  "create a KB article about...", "how do we handle deployments",
  "is there a decision record for...", "what are our coding conventions",
  "look up the runbook", "find the architecture decision", "KB gap analysis".
argument-hint: [project]
arguments: [project]
allowed-tools:
  - mcp__youtrack__search_articles
  - mcp__youtrack__get_article
  - mcp__youtrack__create_article
  - mcp__youtrack__update_article
  - mcp__youtrack__get_project
---

# YouTrack Knowledge Base

> Search, analyze, and manage YouTrack Knowledge Base articles. Grounded answers with citations. For _issue_ status or work, use `youtrack-issue-management` or `youtrack-issue-analyzer`; this skill is for _documentation_ and _how things work_.

## Prerequisites

- YouTrack MCP server must be configured and running
- `$project` — the project short name (e.g., `PROJ`, `MYAPP`)
- No prerequisite skill required — KB discovery is self-contained (articles are project-level, no field schema needed)

## Article Data Model

Articles in YouTrack KB:
- Belong to a project
- Have a hierarchical structure (parent/child articles)
- Have visibility controls (public, project-level, private)
- Content is Markdown with optional attachments
- Can reference issues, other articles, and external URLs

## Discovery Protocol

### 1. List All Articles

```
mcp__youtrack__search_articles(query: "project: $project")
```

Returns all articles in the project with:
- `id` — article ID
- `title` — article title
- `summary` — short description/teaser
- `project` — project short name
- `childArticles` — nested child articles (if any)

### 2. Build Article Tree

Parse the response to build a navigable tree:
```
KB Root
  ├── Getting Started
  │   ├── Project Overview
  │   └── Setup Guide
  ├── Architecture
  │   ├── System Design
  │   ├── Decisions
  │   │   ├── ADR-001: Use Postgres
  │   │   └── ADR-002: Event-Driven
  │   └── Data Model
  ├── Development
  │   ├── Coding Standards
  │   ├── Branching Strategy
  │   └── Deployment Guide
  └── Runbooks
      ├── Incident Response
      └── Rollback Procedure
```

### 3. Cache Article Index

Write the article tree to `/memories/session/youtrack-{PROJECT}-kb-index.md` (where `{PROJECT}` is `$project` uppercased) for session reuse.

## Query Workflow

### 1. Receive Question

Parse the user's question. Identify key terms and concepts.

### 2. Search Articles

```
mcp__youtrack__search_articles(query: "{extract key terms from user's question}")
```

Use the user's question terms directly. The search is full-text across titles and content.

### 3. Fetch Matching Articles

For each promising search result:
```
mcp__youtrack__get_article("{article.id from search results}")
```

This returns the full Markdown content.

### 4. Synthesize Answer

- Read the full content of matching articles
- Extract the relevant sections for the user's question
- Synthesize into a clear, cited answer
- Link back to articles: "See [Article Title](https://<instance>.youtrack.cloud/article/{article.id})"
- If the answer is partial, say so: "The KB covers X but doesn't mention Y"
- If no answer is found, suggest related articles: "No direct answer, but these articles may be relevant: [list]"

### 5. Citation Format

Always cite the source article:
```
Based on the [Coding Standards](https://<instance>.youtrack.cloud/article/123-456) article:

> Direct quote from the article...

The [Branching Strategy](https://<instance>.youtrack.cloud/article/123-457) also mentions...
```

## Index/Summary Workflow

### Full KB Index

When the user asks to "show the knowledge base" or "list all articles":

1. Fetch all articles: `mcp__youtrack__search_articles(query: "project: $project")`
2. Build the article tree (see Discovery Protocol)
3. Present as a navigable structure
4. Note article count, last updated dates, and hierarchy depth
5. Highlight top-level categories and leaf articles

### Topic Summary

When the user asks about a specific topic area:
1. Search for articles matching the topic
2. Fetch the top matches
3. Summarize each with title, key points, and publication date

## Article Analysis

### Gap Analysis

When the user asks "what's missing from the KB":

1. Fetch the full article tree
2. Analyze topic coverage against common project documentation needs:
   - Architecture decisions
   - Coding standards
   - Deployment procedures
   - Runbooks / incident response
   - Onboarding guide
   - API documentation
   - Testing strategy
   - Security policies
3. Report covered topics and gaps
4. Suggest articles that would fill the gaps
5. Cross-reference with issues — are there decisions or patterns that should be documented?

### Staleness Check

When the user asks if the KB is up to date:

1. Fetch all articles
2. Check last update date for each
3. Flag articles not updated in >6 months as "potentially stale"
4. Flag articles not updated in >12 months as "likely stale"
5. Cross-reference with recent project activity (from issue searches)

### Cross-Reference with Issues

Check if important decisions or processes from issues are documented:
1. Search for issues with "decision" or "ADR" in title
2. Check if corresponding KB articles exist
3. Report decisions that should be documented

## Article Creation/Update

### Creating an Article

```
mcp__youtrack__create_article(
  project: "$project",
  title: "Article Title",
  content: "Markdown content...",
  parent_id: "optional-parent-article-id"
)
```

Before creating:
- Check if a similar article already exists via search
- Match the project's existing article structure (place in the right category)
- Follow naming conventions from existing articles
- Keep content focused — one topic per article

### Updating an Article

```
mcp__youtrack__update_article(
  article_id: "{article.id}",
  title: "Updated Title",    # optional
  content: "Updated content"  # optional
)
```

Before updating:
- Fetch the current article content: `mcp__youtrack__get_article("{article.id}")`
- Show the user what will change (diff-style)
- Preserve existing structure and section order unless specifically changing them
- Add update notes when major changes are made

### Article Template

When creating new articles, follow this structure:
```markdown
# Title

## Overview
Brief description of what this article covers.

## Details
Main content with sections as needed.

## Related
- Link to related articles
- Link to related issues
- Link to external resources

## Last Updated
YYYY-MM-DD — brief note on what changed
```

## References

- **[`${CLAUDE_SKILL_DIR}/references/article-structure.md`]** — Article data model: parent-child relationships, visibility controls, content format
- **[`${CLAUDE_SKILL_DIR}/references/search-strategies.md`]** — Search query patterns, fallback strategies, multi-project KB search
