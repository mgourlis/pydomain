# Issue Patterns — Task Creation

Issue type taxonomy, description templates, section conventions, and quality guardrails for creating well-formed tasks.

## Issue Type Taxonomy

Each type has a different purpose, audience, and description structure. Select the type based on user intent, then apply the corresponding template.

| Type | When to Use | Title Pattern |
|------|-------------|---------------|
| **Epic** | Container for a larger goal spanning multiple stories/tasks | Overarching feature name, quarterly goal |
| **Story** | User-visible unit of value, typically part of an epic | Feature name, user-facing capability |
| **Task** | Concrete, assignable work item — smallest unit | Imperative action: "Build...", "Implement...", "Fix..." |
| **Bug** | Defect — something is broken | Problem statement: "X fails when Y" |
| **Feature** | Standalone new capability (not part of an epic) | "Add X", "Support for Y", "Integrate with Z" |
| **Investigation** | Research, spike, feasibility — outcome is understanding, not code | "Investigate X", "Research Y", "Explore Z" |
| **Documentation** | Create or update documentation | "Document X", "Write guide for Y", "Update docs for Z" |

The exact type name comes from the tracker's project configuration. Map user intent to the closest available type.

## Description Templates

### Template 1: Epic

Container for multiple stories or tasks. The reader needs to understand the goal, the scope, and how progress will be tracked.

```markdown
## Context
[Why this epic exists — business driver, strategic goal, or user need]

## Goal
[The outcome this epic delivers. What changes for users or the system when all children are done?]

## Scope
- [Included capability or area]
- [Included capability or area]

## Out of Scope
- [Explicitly excluded work]

## Success Criteria
- [ ] [Measurable condition confirming the epic is complete]
- [ ] [Measurable condition confirming the epic is complete]
```

Epic descriptions are *not* implementation plans — they define boundaries and success conditions. Children handle the "how."

### Template 2: Story

User-visible unit of value. May be a parent of tasks. The reader needs the user perspective, the value proposition, and the acceptance criteria.

#### Story Subtypes

Choose the narrative format based on the story's purpose:

| Subtype | When to Use | Narrative Format |
|---------|-------------|-----------------|
| **Feature** | New user-facing capability | As a [persona], I want [action], so that [benefit] |
| **Improvement** | Enhancing existing capability | As a [persona], I need [improvement], to [achieve goal more effectively] |
| **Bug Fix** | Restoring expected behavior | As a [persona], I expect [correct behavior], when [condition] |
| **Integration** | Connecting systems or services | As a [persona], I want to [integrate with system], so that [workflow improvement] |
| **Enabler** | Technical prerequisite for user value | As a developer, I need [technical requirement], to enable [user-facing capability] |

Feature subtype example:
> As a marketing manager, I want to export campaign reports to PDF, so that I can share results with stakeholders who don't have system access.

Improvement subtype example:
> As a sales rep, I need faster search results, to find customer records without interrupting calls.

Bug Fix subtype example:
> As a user, I expect my session to remain active, when navigating between dashboard tabs.

Enabler subtype example:
> As a developer, I need to implement a caching layer, to enable sub-second dashboard load times.

#### Story Template

```markdown
## Context
[Why this story exists, link to parent epic or design document]

## User Perspective
[Choose narrative format from subtypes above]

## What to Do
- [Specific user-facing changes]

## Acceptance Criteria
- [ ] [Measurable condition from the user's perspective]
- [ ] [Measurable condition from the user's perspective]

## References
- [Links to designs, mockups, specifications]
```

### Template 3: Task (Implementation)

Concrete, assignable work item. The standard template — most tasks follow this structure.

```markdown
## Context
[Why this task exists, link to parent story/epic or design document]

## What to Do
- [Specific work items — concrete but not prescriptive]

## Acceptance Criteria
- [ ] [Verifiable condition with measurable threshold]
- [ ] [Verifiable condition with measurable threshold]

## References
- [Links to existing design docs, specs, documentation URLs — never code paths or other tasks]
```

### Template 4: Bug Report

Defect report. The reader needs to understand what's wrong, how to reproduce it, and what should happen instead.

```markdown
## Context
[What feature is affected and how it relates to the system]

## Problem
[What's broken — observable symptoms, not root cause speculation]

## Steps to Reproduce
1. [Step one]
2. [Step two]
3. [Step three]

## Expected Behavior
[What should happen]

## Actual Behavior
[What happens instead]

## Acceptance Criteria
- [ ] [Verifiable fix condition]
- [ ] [Regression prevention]
```

Include error messages or log excerpts when they clarify the problem. Omit when the description is self-explanatory.

### Template 5: Feature

Standalone new capability. Similar to Story but self-contained — no parent epic. The reader needs the capability description and clear boundaries.

```markdown
## Context
[Why this feature is needed, what problem it solves]

## What to Do
- [Capability to add]
- [Capability to add]

## Acceptance Criteria
- [ ] [Measurable condition]
- [ ] [Measurable condition]

## Out of Scope
- [Explicitly excluded work]

## References
- [Links to specifications, prior art, competitive analysis]
```

### Template 6: Investigation / Spike

Research task. The outcome is understanding or a decision, not code. The reader needs to know what questions to answer and what form the output takes.

```markdown
## Context
[What prompted the investigation]

## Questions to Answer
1. [Specific question]
2. [Specific question]

## Goal
[What decision or recommendation this investigation should produce]

## Constraints
- [Time-box, scope limits, tools or areas to investigate]

## Acceptance Criteria
- [ ] Root cause identified and documented
- [ ] Recommended fix or next steps proposed
- [ ] Findings written in [location — wiki, comment, document]
```

Do NOT include implementation work in an investigation. If the investigation reveals implementation is needed, create a separate task.

### Template 7: Documentation

Create or update documentation. The reader needs to know what to document, where, and for whom.

```markdown
## Context
[Why this documentation is needed — new feature, outdated docs, gap identified]

## What to Document
- [Topic or section to cover]

## Target Location
[Where the documentation lives — wiki page, README section, API docs, KB article]

## Audience
[Who will read this — developers, end users, admins, oncall]

## Acceptance Criteria
- [ ] [Topic covered with accurate information]
- [ ] [Code examples compile/run if applicable]
- [ ] [Reviewed by [person or role]]
```

### Template 8: Prompt-Style

Agent instruction task — the issue contains direct instructions for an agent or developer to execute verbatim.

```markdown
## Context
[Why this work is being done]

## Prompt
1. [Step one — execute verbatim]
2. [Step two — execute verbatim]
3. [Step three — execute verbatim]

## Acceptance Criteria
- [ ] [Verifiable output condition]
- [ ] [Verifiable output condition]
```

Use this template when the user provides explicit numbered instructions or when working with agent-driven workflows. The prompt section is extracted and executed as-is.

## Acceptance Criteria Patterns

Multiple AC formats serve different needs. Choose based on the issue type and complexity.

### Pattern 1: Checklist (Default)

Best for: Tasks, Documentation, Bugs — when a simple pass/fail list suffices.

```markdown
- [ ] [Verifiable condition with measurable threshold]
- [ ] [Verifiable condition with measurable threshold]
```

### Pattern 2: Given-When-Then (Behavioral)

Best for: Stories, Features — any issue where user behavior needs precise specification.

```markdown
Given [precondition/context],
When [action/trigger],
Then [expected outcome].
```

Example:
```markdown
Given the user is logged in with valid credentials,
When they click the "Export" button,
Then a PDF download starts within 2 seconds.
```

### Pattern 3: Should/Must/Can (Priority-Tagged)

Best for: Features, Tasks with mixed priority requirements — distinguishes hard requirements from nice-to-haves.

| Prefix | Meaning | Use When |
|--------|---------|----------|
| **Must** | Hard requirement — failure blocks release | Compliance, security, data integrity |
| **Should** | Expected behavior — failure is a defect | Standard functionality, normal use cases |
| **Can** | Optional capability — nice to have | Enhancements, convenience features |

```markdown
- Must encrypt all data at rest to meet compliance requirements
- Should display loading spinner when API call exceeds 500ms
- Can undo last action without losing other changes
```

### AC Coverage Checklist

Every set of AC should cover these categories where applicable. Not every issue needs all six — use judgment.

| Category | What to Cover | Example |
|----------|---------------|---------|
| Happy Path | Normal usage with valid input | Given valid credentials, user is authenticated and redirected |
| Validation | Invalid, missing, or boundary input | Should reject email without @ symbol with inline error |
| Error Handling | System failures, timeouts, unavailable services | Must show user-friendly message when API fails |
| Performance | Response time, throughput, resource limits | Should complete search within 2 seconds |
| Accessibility | Keyboard, screen reader, contrast | Must be navigable via keyboard only |
| Security | Auth, authorization, data exposure | Should not expose sensitive data in URL parameters |

### Minimum AC by Complexity

More complex issues need more acceptance criteria. Use these minimums as a guide:

| Complexity | Typical Types | Minimum AC |
|------------|--------------|------------|
| Small (1-2pt) | Bug fix, trivial task | 3-4 criteria |
| Medium (3-5pt) | Standard task, small feature | 4-6 criteria |
| Large (8pt) | Complex feature, multi-component | 5-8 criteria |
| Oversized (13+pt) | — | **Split the issue instead** |

## Section Header Conventions

Standard section names and their purpose. Use these names consistently so both humans and agents can parse descriptions.

| Section Name | Aliases | Purpose |
|---|---|---|
| `## Context` | Background, Why | Why this issue exists — business driver, parent reference |
| `## What to Do` | Requirements, Spec | Specific work items — concrete but not prescriptive |
| `## Acceptance Criteria` | AC, Definition of Done, DoD | How to verify completion — testable, measurable |
| `## Out of Scope` | Non-Goals | Explicitly excluded work — constrains implementation |
| `## Problem` | — | What's broken (bugs only) |
| `## Steps to Reproduce` | — | How to trigger the defect (bugs only) |
| `## Expected Behavior` | — | What should happen (bugs only) |
| `## Actual Behavior` | — | What happens instead (bugs only) |
| `## Prompt` | Instructions | Direct agent instructions — execute verbatim |
| `## References` | Sources, See Also | Links to verifiable external resources |
| `## Constraints` | — | Restrictions on the implementation approach |
| `## Goal` | — | Desired outcome (investigations, epics) |
| `## Questions to Answer` | — | Specific research questions (investigations only) |

## Quality Guardrails

### Vague Indicators — Catch These Before Presenting the Draft

When drafting a task, check for these vague indicators and fix them:

| Vague Pattern | Fix |
|---|---|
| "Make it work" with no definition of "work" | Add measurable acceptance criteria |
| "Improve performance" — which aspect? by how much? | Specify metric and target: "Page loads in under 2s" |
| "Clean up the code" | Name the specific structural improvement and how to verify it |
| No scope boundaries — what's in vs out? | Add explicit Out of Scope section |
| No context — why does this exist? | Add Context section linking to the larger effort |
| Title is past-tense: "Fixed X" | Rewrite in imperative: "Fix X" |
| Title is vague: "Bug fix" | Be specific: "Fix 500 error on special characters in search" |
| Subjective criteria: "better UX", "more robust" | Replace with measurable thresholds |
| Missing negative cases | Add: error conditions, empty states, invalid input, permission boundaries |
| Solution story — prescribes implementation ("Implement React component") | Rewrite focusing on outcome: "Display user profile information" |
| Compound story — multiple capabilities in one ("Create, edit, and delete users") | Split into separate issues, one per capability |
| Missing benefit — no "so that" clause in story | Add who benefits and why |
| Technical jargon in user-facing story ("Implement Redis caching") | Rewrite in user language: "Enable instant search results" |
| Too many AC (15+) — story is oversized | Split into smaller issues; each should have ≤8 AC |

### Specificity Checklist

Every draft should pass these checks before presentation:

- **Title:** imperative mood, 5-10 words, specific
- **Context:** one or two sentences connecting to the larger effort
- **Work items:** concrete enough to act on, abstract enough to leave implementation judgment
- **Acceptance criteria:** each has a clear pass/fail outcome and measurable threshold
- **Negative cases:** error conditions, empty states, invalid input covered where relevant
- **Scope boundaries:** Out of Scope section present when the task could be interpreted broadly
- **References:** only verifiable existing resources — no code paths, no future files, no other tasks

### INVEST Validation

Before finalizing a Story or Feature, validate against INVEST criteria. Issues that fail multiple criteria should be rewritten or split.

| Criterion | Question | Pass If |
|-----------|----------|--------
| **I**ndependent | Can this be developed without blocking on another uncommitted issue? | No hard dependencies on unfinished work |
| **N**egotiable | Is the implementation approach flexible? | Multiple valid approaches exist |
| **V**aluable | Does this deliver value to users or business? | Clear benefit statement present |
| **E**stimable | Can the team estimate the effort? | Well enough understood to size |
| **S**mall | Can this be completed in one sprint/iteration? | Fits within typical work session cadence |
| **T**estable | Can we verify this is done? | Clear, measurable acceptance criteria |

#### INVEST Failure Patterns

| Fails Criterion | Red Flag in Description | Fix |
|-----------------|------------------------|-----|
| Independent | "After issue X is done..." or "Blocked by Y" | Combine with dependency or resequence |
| Negotiable | Specific implementation prescribed in description | Refocus on outcome, not solution |
| Valuable | No "so that" / benefit statement | Add who benefits and why |
| Estimable | Team cannot describe the approach | Create an Investigation first, then re-estimate |
| Small | Description covers multiple distinct capabilities | Split into separate issues |
| Testable | "System should be better" or subjective criteria | Add measurable thresholds |

### Clarification Thresholds

When drafting from incomplete user input:

- **1-2 vague indicators:** Note them but proceed if the core task is clear
- **3+ vague indicators:** Stop and ask for clarification before presenting
- **Missing AC + Missing scope:** Always ask — these are the two most critical sections
- **Missing context only:** Proceed but flag in the draft: "Context assumed — please verify"
