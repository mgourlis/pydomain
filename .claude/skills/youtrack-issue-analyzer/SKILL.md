---
name: youtrack-issue-analyzer
description: >-
  Deep-read a YouTrack issue and its full context — linked issues, KB references,
  external sources, codebase paths — and build rich session context for further work.
  Surfaces ambiguities when the issue is vague or underspecified.
when_to_use: >-
  When the user asks to analyze, understand, or work on a YouTrack issue.
  Triggers on: "analyze PROJ-42", "what does this issue mean", "explain PROJ-42",
  "I want to work on PROJ-42", "give me context on PROJ-42", "this issue is unclear",
  "break down this epic", "follow the prompt in PROJ-42", "implement PROJ-42",
  "start PROJ-42", "what's the full picture of PROJ-42", "show me everything about PROJ-42",
  "help me clarify PROJ-42", "what subtasks does PROJ-10 have".
argument-hint: [issue-id]
arguments: [issue_id]
allowed-tools:
  - mcp__youtrack__get_issue
  - mcp__youtrack__search_issues
  - mcp__youtrack__search_articles
  - mcp__youtrack__get_article
  - mcp__youtrack__get_project
  - mcp__youtrack__get_issue_fields_schema
effort: max
---

# YouTrack Issue Analyzer

> Deep-read an issue, follow links and references, build rich session context. Read-only — never modifies YouTrack. For _direct modification_, use `youtrack-issue-management`; for _dependencies/blockers_, use `youtrack-state-propagation`; for _documentation_, use `youtrack-knowledge-base`.

## Prerequisites

- YouTrack MCP server must be configured and running
- `$issue_id` — the issue ID (e.g., `PROJ-42`)
- `<prerequisite>` Load `youtrack-project-discovery` to discover project field configuration for issue classification. `</prerequisite>`

## Core Principle

This skill is a **context builder, not a modifier**. It reads deeply, follows links, fetches referenced content, and produces a rich analysis. It never creates, updates, or changes anything in YouTrack.

If the issue is vague or underspecified, this skill surfaces questions rather than guessing — applying the clarification principles from `general-guidelines` and `karpathy-coding-guidelines`.

## Analysis Protocol

### Phase 1: Fetch & Classify

#### 1.1 Fetch the Issue

```
mcp__youtrack__get_issue("PROJ-42")
```

Returns the issue with:
- `idReadable` — "PROJ-42"
- `summary` — title
- `description` — full Markdown body
- `fields` — standard fields (state, priority, assignee)
- `customFields` — project-specific custom fields
- `tags` — applied tags
- `links` — linked issues with link types and directions
- `comments` — discussion thread

#### 1.2 Classify by Type

Determine the issue type from custom fields (e.g., `Type` field value):
- **Epic** — container for stories
- **Story** — user-facing feature, may be parent of tasks
- **Task** — concrete work item, may be subtask of a story
- **Bug** — defect to fix
- **Feature** — standalone feature (not part of an epic)
- **Investigation/Spike** — research task
- **Documentation** — doc creation or update

The classification determines how we expand the context (e.g., epics need subtask expansion, bugs need reproduction context).

#### 1.3 Read the Description Structure

Parse the description for recognizable sections:

| Section Pattern | What It Contains |
|----------------|------------------|
| `## Context` / `## Background` | Why this issue exists |
| `## Requirements` / `## Spec` | What needs to be built |
| `## Acceptance Criteria` / `## AC` | How to verify completion |
| `## Definition of Done` / `## DoD` | Completion checklist |
| `## Prompt` / `## Instructions` | Direct agent instructions |
| `## References` / `## Sources` | External links and docs |
| `## Notes` / `## Additional Info` | Supplementary context |
| `## Out of Scope` | What's explicitly excluded |

### Phase 2: Content Analysis

Classify the issue's content to determine what kind of artifact it represents:

| Classification | Indicators | Action |
|---------------|------------|--------|
| **Actionable task** | Clear summary, defined scope, ready to work | Assemble context, hand off to coding |
| **Specification** | Detailed requirements with AC | Extract requirements as checklist |
| **Prompt-style** | Contains `prompt:` or `instructions:` section | Extract prompt verbatim for the session to execute |
| **Epic/Container** | Serves as parent, no actionable content | Fetch all children, summarize hierarchy |
| **User story** | "As a... I want... So that..." format | Extract persona, desire, benefit |
| **Vague/underspecified** | Insufficient detail to act | Apply clarification protocol (Phase 8) |
| **Documentation request** | Instructions to write/update docs | Flag as doc task, extract scope |
| **Investigation** | Open-ended research question | Note it requires discovery, not implementation |
| **Antipattern detected** | Solution story, compound story, missing benefit, oversized (>8 AC), technical jargon in user story, missing negative path | Flag antipattern type and suggest fix; see [`${CLAUDE_SKILL_DIR}/references/issue-patterns.md`] for detection signals and remedies |

### Phase 3: Link Graph Expansion

#### 3.1 Parse All Links

From the issue response, extract links by type and direction:
- **subtask of / parent for** — hierarchy links
- **depends on / is required for** — dependency links
- **relates to** — informational links
- **duplicates / is duplicated by** — duplicate links

#### 3.2 Expand Based on Issue Type

**For Epics:**
```
Fetch all subtasks (child issues via "parent for" links)
  → For each subtask, fetch its subtasks (recursively, 1 level)
  → Build epic → story → task tree
  → Summarize each child: state, assignee, summary
```

**For Stories:**
```
Fetch parent epic (via "subtask of" link)
  → Fetch sibling stories (via parent's "parent for" links)
  → Fetch subtasks (child tasks via "parent for" links)
  → Build: epic context + sibling awareness + task breakdown
```

**For Tasks:**
```
Fetch parent story/epic (via "subtask of" link)
  → Fetch sibling tasks (via parent's "parent for" links)
  → Fetch dependencies (via "depends on" links)
  → Build: parent context + sibling state + blocker awareness
```

**For Bugs:**
```
Fetch duplicates (via "duplicates" links)
  → Fetch dependencies (via "depends on" links)
  → Check if related issues provide reproduction context
  → Build: duplicate awareness + blocker check
```

#### 3.3 Build the In-Memory Tree

Construct a structured representation:
```
PROJ-10 (Epic: Redesign Auth) [In Progress]
  assigned to: jdoe
  subtasks:
    ├── PROJ-42 (Story: Login Flow) [In Progress]
    │   assigned to: asmith
    │   links: depends on PROJ-88 (API: Token Endpoint)
    │   subtasks:
    │   ├── PROJ-100 (Task: Login UI) [Fixed]
    │   └── PROJ-101 (Task: Error Handling) [Open]
    └── PROJ-43 (Story: Password Reset) [Open]
        assigned to: unassigned
        links: depends on PROJ-88 (API: Token Endpoint)
```

### Phase 4: Reference Resolution

#### 4.1 YouTrack KB References

Detect patterns in the issue description:
- Explicit article IDs or URLs
- `article:` or `KB:` prefixes
- Phrases like "see the X article" or "documented in our KB"

For each detected reference:
```
mcp__youtrack__get_article("{article.id detected from description}")
```
Fetch the full content and link from the context.

#### 4.2 External URLs

Detect URLs in the description. Note them in the context. For publicly accessible URLs, optionally fetch summaries via WebFetch — but only do this if explicitly needed for understanding.

#### 4.3 Codebase References

Detect file paths, module names, or code patterns mentioned in the issue:
- `/path/to/file.ts` — explicit file paths
- `module:auth` — module references
- `class:UserService` — class/function references
- `#include <...>` — for non-web projects

Note these for the session. Do NOT read the files — the coding environment handles that when implementation begins.

#### 4.4 Other Issue IDs

Detect issue IDs mentioned in the description text (not formally linked):
- Pattern: `PROJ-\d+` or similar
- Fetch each via `mcp__youtrack__get_issue`
- Add to the link graph (marking as "mentioned in text, not formally linked")

### Phase 5: Special Section Handling

Parse the description for structured sections:

#### 5.1 Prompt/Instructions Section

Detect sections labeled `## Prompt`, `## Instructions`, `## What to Do`, or `prompt:`.
- Extract the full text verbatim
- Include in the context as "Direct Agent Instructions"
- Flag for execution: "This issue contains direct instructions — follow them"

#### 5.2 Acceptance Criteria

Detect sections labeled `## Acceptance Criteria`, `## AC`, or `ac:`.
- Extract as a checklist
- Each line starting with `-` or `*` is a criterion
- Format as: `- [ ] <criterion>` for verification tracking

#### 5.3 Context/Background

Detect sections labeled `## Context`, `## Background`, `## Why`.
- Extract as background knowledge
- Include verbatim in the context — this is the "why" behind the issue

#### 5.4 Definition of Done

Detect sections labeled `## Definition of Done`, `## DoD`, or `## Completion`.
- Extract as checklist
- Include alongside acceptance criteria

#### 5.5 References/Sources

Detect sections labeled `## References`, `## Sources`, `## See Also`.
- Parse each reference
- Resolve YouTrack articles and issue IDs
- Note external URLs

### Phase 6: Context Assembly

Write the assembled context to `/memories/session/issue-$issue_id-context.md`:

```markdown
# Issue Context: $issue_id

## Summary
- **Title:** {issue.summary}
- **Type:** {issue.type}
- **State:** {issue.state}
- **Priority:** {issue.priority}
- **Assignee:** {issue.assignee}
- **Tags:** {issue.tags}

## Classification
**Artifact type:** Actionable task / Specification / Epic / Vague / etc.
**Clarity:** Clear — ready to act / Needs clarification — see questions below

## Description (verbatim)
{issue.description}

## Extracted Sections
### Prompt/Instructions
{extracted prompt if present}

### Acceptance Criteria
- [ ] {criterion}
- [ ] {criterion}

### Context/Background
{extracted context}

### Definition of Done
- [ ] {criterion}

## Link Graph
### Hierarchy
- Parent: {parent issue}
- Children: {children list}
- Siblings: {siblings list}

### Dependencies
- Depends on: {blockers} (all must be resolved first)
- Required for: {dependents} (blocked by this issue)

### Related
- Relates to: {related}
- Duplicates: {duplicates}

## Resolved References
### KB Articles
- [{title}]({url}) — {summary}

### External URLs
- {url} — {description}

### Codebase Paths
- {path} — {context}

## Ambiguity Assessment
**Is this issue actionable without clarification?** {yes or no}

**Unresolved questions:**
1. {question}
2. {question}
```

### Phase 7: Output

Based on the analysis, produce the appropriate output:

**If the issue is clear and actionable:**
- Present the assembled context (condensed form)
- Note: "Ready to proceed. Context is in `/memories/session/issue-$issue_id-context.md`"
- If the user asked to "work on" or "implement", hand off to the appropriate coding skill

**If the issue is an epic/container:**
- Present the full hierarchy
- Summary dashboard: counts by state, assignee, and type
- Note: "This is a container. Pick a child to work on, or analyze one with `/youtrack-issue-analyzer PROJ-XXX`"

**If the issue has prompt instructions:**
- Present the context
- State: "This issue contains direct instructions. Extracted prompt is in the context."
- Offer to execute the prompt

**If the issue is vague:**
- Present the partial context
- List clarification questions (from Phase 8)
- Do NOT proceed with assumptions

### Phase 8: Clarification Protocol

When the issue is vague or underspecified, surface questions rather than guessing.

#### 8.1 When to Ask vs Proceed

| Situation | Action |
|-----------|--------|
| Missing acceptance criteria | Ask for AC before implementing |
| Unclear scope (what's in/out) | Ask for scope boundaries |
| Ambiguous requirements (multiple interpretations) | Present interpretations, ask which is correct |
| Missing context (why this exists) | Note missing context, proceed if not blocking |
| Unresolved dependencies (blockers not done) | Flag blockers, ask if they're actually needed |
| Vague success criteria ("make it fast") | Ask for measurable targets |
| No implementation hints in a large codebase | Ask where to look, or propose based on description |

#### 8.2 Question Format

Frame clarifications constructively:
```
To implement this issue, I need to understand:
1. [Specific question about requirement X]
2. [Specific question about scope Y]

What I can determine so far:
- [Fact already clear from the issue]
- [Inference that seems reasonable]

Assumptions I would make if you don't have answers:
- [Proposed assumption with reasoning]
```

#### 8.3 When the Issue Should Be Updated

If the issue is fundamentally underspecified (missing requirements, unclear scope, conflicting information), suggest:
"This issue would benefit from more detail. Consider updating PROJ-42 with:
- [Missing section]
- [Missing section]

I can update the issue if you tell me what to add."

For structural problems (compound stories, missing benefit, solution stories), validate against INVEST criteria and antipatterns in [`${CLAUDE_SKILL_DIR}/references/issue-patterns.md`]. For oversized stories, suggest splitting using the techniques there (by workflow step, persona, data type, operation, or happy path first).

## After Analysis

- The context file at `/memories/session/issue-$issue_id-context.md` is available for the rest of the session
- If the user proceeds to implementation, the coding skill reads this context
- If the user switches to a different issue, this skill can be re-invoked

## References

- **[`${CLAUDE_SKILL_DIR}/references/issue-patterns.md`]** — Issue type taxonomy, content patterns, special section detection, vague vs specific indicators
- **[`${CLAUDE_SKILL_DIR}/references/clarification-framework.md`]** — When to ask vs proceed, question templates, escalation guidance
