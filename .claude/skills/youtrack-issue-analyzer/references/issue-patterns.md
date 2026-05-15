# Issue Patterns — YouTrack Issue Analyzer

Issue type taxonomy, content structure patterns, and special section detection.

## Issue Type Taxonomy

### Epic

Container for multiple stories or tasks that together deliver a larger goal.

**Indicators:**
- Type field = "Epic"
- Title patterns: "Epic:", overarching feature names, quarterly goals
- Description describes a broad goal, not specific implementation
- Has many subtasks (5-15+)
- No direct actionable content — serves as parent/hub

**Analysis strategy:** Fetch all children, group by type/state, present dashboard.

### Story

User-visible unit of value, typically part of an epic.

**Indicators:**
- Type field = "Story" or "User Story"
- Title patterns: feature names, user-facing functionality
- Description often uses "As a... I want... So that..." format
- May have acceptance criteria
- May be a parent of tasks

**Analysis strategy:** Extract persona/desire/benefit, check AC, fetch parent epic and sibling stories.

### Task

Concrete, assignable work item. Smallest unit of work.

**Indicators:**
- Type field = "Task"
- Title is a specific action: "Build...", "Implement...", "Fix...", "Write tests for..."
- Description contains specific technical details
- Usually a subtask of a story or parent task
- Can be completed in a single work session

**Analysis strategy:** Fetch parent for context, check dependencies, extract specific technical requirements.

### Bug

Defect report describing unexpected behavior.

**Indicators:**
- Type field = "Bug"
- Title describes a problem: "X fails when Y", "Error on Z page"
- Description may include: steps to reproduce, expected vs actual behavior, environment details
- May have severity/priority fields
- Often linked to a duplicate or related issue

**Analysis strategy:** Extract repro steps, check duplicates, note environment details.

### Feature

Standalone feature request, may not be part of an epic.

**Indicators:**
- Type field = "Feature"
- Title: "Add X", "Support for Y", "Integrate with Z"
- Description describes desired capability
- May have acceptance criteria
- Similar to a story but self-contained

**Analysis strategy:** Similar to story — scope, AC, dependencies.

### Investigation / Spike

Research task to answer a question or explore a solution.

**Indicators:**
- Type field = "Investigation" or "Spike"
- Title: "Investigate X", "Research Y", "Explore Z option"
- Description contains open-ended questions
- No clear deliverable beyond findings/recommendations
- Often time-boxed

**Analysis strategy:** Extract the questions to answer. Note that implementation is NOT expected — only research.

### Documentation

Request or task to create/update documentation.

**Indicators:**
- Type field = "Documentation" or "Docs"
- Title: "Document X", "Write guide for Y", "Update README"
- Description specifies what to document and where
- Often references a specific doc file or KB article

**Analysis strategy:** Extract scope of documentation, target location, and audience.

## Content Structure Patterns

### Pattern 1: Structured Specification

```markdown
## Context
Why this exists...

## Requirements
- Requirement 1
- Requirement 2

## Acceptance Criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Out of Scope
- What's not included
```

**Indicators:** Clear section headers, checkbox lists in AC, explicit scope boundaries.

**Analysis:** Ready to implement. Extract AC as checklist, respect scope boundaries.

### Pattern 2: Prompt-Style

```markdown
## Prompt
1. Read the auth module
2. Add OAuth2 support
3. Update the login flow
4. Write tests
```

**Indicators:** Contains `prompt:` section, numbered instruction steps, uses imperative mood.

**Analysis:** Extract instructions verbatim. Execute in order. Each step is a verifiable task.

### Pattern 3: User Story

```markdown
As a [user type]
I want [capability]
So that [benefit]

## Acceptance Criteria
...
```

**Indicators:** "As a... I want... So that..." format, separates persona from capability from benefit.

**Analysis:** Extract the three components. Use benefit to guide implementation decisions.

### Pattern 4: Bug Report

```markdown
## Steps to Reproduce
1. Go to X
2. Click Y
3. See error Z

## Expected Behavior
Should show A

## Actual Behavior
Shows error B

## Environment
- Browser: Chrome 120
- Version: 2.3.0
```

**Indicators:** Steps to reproduce, expected vs actual, environment details.

**Analysis:** Extract repro steps for verification. Note environment specifics.

### Pattern 5: Minimal/Vague

```markdown
Make the login page faster. It's slow.
```

**Indicators:** One or two sentences, no structure, no measurable criteria.

**Analysis:** Apply clarification protocol. Ask for:
- Measurable performance target ("faster" → what metric, what target?)
- Scope (just the page load? the API call? the whole flow?)
- How to verify improvement

## Antipattern Detection

When analyzing an issue, check for these common structural problems. Each antipattern has specific signals and recommended actions.

| Antipattern | Detection Signals | Impact | Recommended Action |
|-------------|-------------------|--------|-------------------|
| **Solution Story** | Description prescribes specific implementation (technology, library, architecture) rather than user need | Limits implementation options; may not address real need | Reframe as user outcome; move implementation details to technical notes |
| **Compound Story** | Title contains "and" or commas joining capabilities; multiple distinct user actions in one story | Cannot estimate accurately; partial completion unclear | Split into separate issues, one per capability |
| **Missing Benefit** | "So that" clause absent; no clear value statement; describes *what* but not *why* | Team cannot make informed tradeoff decisions | Ask: who benefits and what changes for them when this is done? |
| **Oversized Story** | >8 acceptance criteria; description spans multiple unrelated areas; team hesitates to estimate | High risk of incomplete delivery | Apply splitting techniques (see Story Splitting Suggestions) |
| **Technical Jargon** | User-facing story uses implementation terms (database, API, framework names) | Non-technical stakeholders cannot validate | Rewrite in user language; keep technical details in implementation notes |
| **Missing Negative Path** | Only happy-path AC; no error conditions, validation rules, or edge cases | Incomplete implementation, production bugs | Add AC for error handling, validation, and boundary conditions |

## Special Section Detection

### Section Header Detection

Detect sections by matching headers (H2, H3, and bold text labels):

```markdown
## Prompt → instruction section
## Instructions → instruction section
## Acceptance Criteria → AC checklist
## AC → AC checklist
## Definition of Done → DoD checklist
## DoD → DoD checklist
## Context → background
## Background → background
## Why → background
## Problem → background
## Requirements → specification
## Spec → specification
## References → reference list
## Sources → reference list
## See Also → reference list
## Out of Scope → scope boundaries
## Non-Goals → scope boundaries
## Notes → supplementary
## Additional Info → supplementary
```

Also detect inline labels:
```markdown
prompt: ... → instruction section
AC: ... → AC checklist
```

### Content Extraction Rules

**For checklists (AC, DoD):**
- Each `-` or `*` bullet is one item
- Numbered items are sequential steps
- Preserve the checkbox format for tracking

**For instructions (Prompt):**
- Extract the full section verbatim — don't summarize
- Preserve numbering and nesting
- Note if instructions reference code paths, files, or external resources

**For background (Context):**
- Extract verbatim
- Include in the context document for reference during implementation
- Don't summarize unless the background is very long (>500 words)

**For scope boundaries (Out of Scope):**
- Extract as a "do NOT do" list
- Important: these constrain implementation — flag if the user later asks for out-of-scope work

## Vague vs Specific Indicators

### Vague Issue Indicators

- **No acceptance criteria:** "Make it work" with no definition of "work"
- **No scope boundaries:** What's included vs excluded isn't clear
- **Ambiguous requirements:** "Improve performance" — which aspect? by how much?
- **Missing context:** No explanation of WHY this is needed
- **Assumptions implicit:** References to domain knowledge not explained
- **Single sentence:** The entire description is one line
- **Subjective criteria:** "Make it better", "Clean up the code"

### Specific Issue Indicators

- **Measurable AC:** "Page loads in under 2 seconds", "Zero console errors"
- **Clear scope:** "Add X to the Y page. Do NOT change Z."
- **Context provided:** "Because of the recent auth change, we need to..."
- **References included:** Links to designs, docs, related issues
- **Structured description:** Clear sections with headers
- **Verifiable DoD:** Each item is a boolean yes/no question

### Threshold for Clarification

- **1-2 vague indicators:** Note them but proceed if the core task is clear
- **3+ vague indicators:** Pause and ask for clarification before proceeding
- **Missing AC + Missing scope:** Always ask — these are the two most critical
- **Missing context only:** Proceed but flag: "Proceeding without full context. I'm assuming this is a standalone change."

## Story Splitting Suggestions

When an issue is oversized or compound, suggest one of these splitting techniques based on the issue's characteristics.

| Technique | When to Suggest | How to Split | Example |
|-----------|-----------------|--------------|---------|
| **By Workflow Step** | Issue describes a linear process with multiple stages | One story per workflow step | "Checkout" → "Add to cart" + "Enter payment" + "Confirm order" |
| **By Persona** | Issue serves multiple user types with different needs | One story per persona | "Dashboard" → "Admin dashboard" + "User dashboard" |
| **By Data Type** | Issue handles multiple input/output formats or types | One story per data type | "Import data" → "Import CSV" + "Import Excel" |
| **By Operation** | Issue covers full CRUD or multiple operations on one entity | One story per operation | "Manage users" → "Create user" + "Edit user" + "Delete user" |
| **Happy Path First** | Issue is complex with many error scenarios | Basic flow first, then error handling, then edge cases | "Full feature" → "Basic happy path" + "Error handling" + "Edge cases" |
| **By Platform/Interface** | Issue targets multiple platforms or interfaces | One story per platform | "Mobile support" → "iOS support" + "Android support" |

### Splitting Decision Guide

1. If the issue describes a **sequence of steps** → split by workflow step
2. If it mentions **multiple user types** → split by persona
3. If it handles **multiple formats** → split by data type
4. If it covers **CRUD operations** → split by operation
5. If it has **many error scenarios** → happy path first
6. If it targets **multiple platforms** → split by platform
