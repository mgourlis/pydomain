# Search Strategies — YouTrack Knowledge Base

Search query patterns, fallback strategies, and multi-project KB searching.

## Search Query Construction

### Basic Keyword Search

```
mcp__youtrack__search_articles(query: "$keywords")
```

The search is full-text across titles and article content. It supports natural language queries.

### Project-Scoped Search

```
mcp__youtrack__search_articles(query: "project: $PROJECT $keywords")
```

Always scope searches to a project unless the user asks to search across projects.

### Effective Query Patterns

| User Question | Effective Query |
|---------------|----------------|
| "How do we handle auth?" | `authentication authorization` |
| "What's our deployment process?" | `deployment release process` |
| "Coding standards for Python" | `coding standards python` |
| "Architecture decisions about databases" | `architecture decision database` |
| "Runbook for incident response" | `runbook incident response` |
| "How to set up a dev environment" | `setup development environment` |

### Query Construction Strategy

1. Extract key nouns and verbs from the user's question
2. Remove filler words ("how", "what", "is there", "do we")
3. Include synonyms for the key concepts
4. Keep the query concise — 3-5 terms is optimal
5. Don't use boolean operators unless the tool supports them

## Search Result Interpretation

### Assessing Relevance

When `search_articles` returns results:
1. Check the article title — does it suggest relevance?
2. Check the summary/teaser — does it match the query?
3. If titles and summaries look relevant, fetch the full content via `get_article`
4. If only tangentially related, note them as "See also" rather than primary answers

### Handling Many Results

If search returns more than 10 results:
1. Sort by relevance (usually the default)
2. Fetch the top 3-5 for full reading
3. List the rest as "Other potentially relevant articles" with titles only
4. Offer to read more if needed

### Handling No Results

If search returns zero results:
1. Try with fewer, broader terms: "deployment" instead of "Kubernetes deployment pipeline"
2. Try synonyms: "CI/CD" instead of "deployment pipeline"
3. Try without project scope (if the article might be in a different project)
4. If still nothing: "No KB articles found about X. You may want to create one."

### Handling Ambiguous Results

If search returns articles that could be relevant but it's unclear:
1. Fetch the top 2-3 for full reading
2. Present what each covers
3. Ask the user which is closest to their question
4. Read the chosen one in depth

## Fallback Strategies

### Strategy 1: Broader Search

If a specific search fails, broaden it:
```
"auth JWT token refresh" → "authentication tokens"
"React component lifecycle hooks" → "React patterns"
```

### Strategy 2: Cross-Project Search

If the project doesn't have relevant KB content:
```
mcp__youtrack__search_articles(query: "$keywords")  # without project scope
```

The user's organization may have KB articles in a shared/global project.

### Strategy 3: Issue-Based Discovery

If no KB articles are found:
```
mcp__youtrack__search_issues(query: "project: $PROJECT $keywords")
```

Issues may contain the information the user is looking for, even if it's not formalized in the KB.

### Strategy 4: Suggest Creation

If the information should exist in the KB but doesn't:
"The KB doesn't cover $topic. This would be a good article to create. I can help write it if you provide the details."

## Multi-Project KB Search

### When to Search Across Projects

- The user doesn't specify a project: search broadly first, then narrow
- The organization has shared/standards projects: include them
- The information might be in a platform/infrastructure project: check there

### How to Search Across Projects

1. First, list available projects via `mcp__youtrack__find_projects`
2. Search the primary project first
3. If not found, search without project scope
4. Report which project each result belongs to

### Shared/Global KB Pattern

Some organizations maintain a shared KB project (e.g., "ORG", "STANDARDS") with cross-cutting documentation. If the primary project search fails, check common shared project names.

## Article Content Search vs Title Search

`search_articles` searches both titles and content. When many results are returned:
- Articles where the query matches the title are more likely to be specifically about that topic
- Articles where the query matches only the body may only mention it in passing

Prioritize title matches for primary answers. Use body matches as supplementary sources.

## Pre-Fetch Decision Heuristic

Before fetching the full content of a search result, check:
1. **Title relevance:** Does the title clearly match the query?
2. **Summary relevance:** Does the snippet suggest it answers the question?
3. **Recency:** Is the article actively maintained? (Recent update = more reliable)
4. **Position in tree:** Is it a top-level overview or a deeply nested detail?

Only fetch if at least 2 of the 3 relevance indicators are positive. For marginal matches, list them as "See also" without fetching.

## Search Examples

### Example 1: Specific Question

```
User: "How do we handle database migrations?"

Search: mcp__youtrack__search_articles(query: "project: PROJ database migrations")
Returns: [Article 123: "Database Migration Guide", Article 456: "Deployment Process"]

Fetch: mcp__youtrack__get_article("123")
       mcp__youtrack__get_article("456")

Synthesize: "The [Database Migration Guide](url) describes the process:
  1. Create a migration script in /migrations
  2. Test on staging first
  3. Run during the weekly deployment window
  The [Deployment Process](url) also mentions migrations are part of the standard deploy checklist."
```

### Example 2: Broad Topic

```
User: "What coding standards do we follow?"

Search: mcp__youtrack__search_articles(query: "project: PROJ coding standards")
Returns: [Article 100: "Python Standards", Article 101: "JavaScript Standards",
          Article 102: "General Guidelines", Article 103: "Code Review Process"]

Fetch: All four (the question is broad, need full picture)

Synthesize: "The KB has standards for:
  - Python: PEP 8 with modifications (Article 100)
  - JavaScript: Airbnb style guide (Article 101)
  - General: naming, structure, patterns (Article 102)
  Code review process: every PR needs 1 approval (Article 103)"
```

### Example 3: No Results

```
User: "What's our disaster recovery plan?"

Search: mcp__youtrack__search_articles(query: "project: PROJ disaster recovery")
Returns: []

Fallback: mcp__youtrack__search_articles(query: "disaster recovery")  # cross-project
Returns: [Article 200 in ORG: "Business Continuity Plan"]

Fetch: mcp__youtrack__get_article("200")

Report: "No disaster recovery article in PROJ. However, ORG has a [Business Continuity Plan](url) that covers DR at the organization level."
```
