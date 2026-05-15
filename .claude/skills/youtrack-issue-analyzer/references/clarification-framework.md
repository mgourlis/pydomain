# Clarification Framework — YouTrack Issue Analyzer

Decision framework for when to ask questions vs proceed with assumptions.

## Core Principle

**Surfacing ambiguity is more valuable than guessing correctly.** A well-framed question saves rework. A wrong assumption wastes time. When in doubt, ask.

## Clarification Decision Tree

```
Is the issue actionable without clarification?
  ├── YES → Proceed. Assemble context, hand off to implementation.
  └── NO → What's missing?
      ├── Acceptance Criteria → ASK: "What does 'done' look like?"
      ├── Scope boundaries → ASK: "What's included vs excluded?"
      ├── Implementation hints → ASK: "Where should I look in the codebase?"
      ├── Measurable targets → ASK: "What specific metric defines success?"
      ├── Dependencies unclear → ASK: "Does this depend on anything else?"
      ├── Background/context → MAY PROCEED (flag: "Working without full context")
      └── Subjective criteria → ASK for objective measures
```

## Question Templates

### Missing Acceptance Criteria

```
The issue describes what to build but not how to verify it's correct.

Could you add acceptance criteria? For example:
- What should happen in the success case?
- What should happen in error/edge cases?
- What specific behavior confirms this is done?
```

### Unclear Scope

```
The issue doesn't define what's in scope vs out of scope.

To avoid scope creep, could you clarify:
- Does this include [likely-in-scope thing]?
- Does this include [likely-out-of-scope thing]?
- Are there existing features/modules I should NOT touch?
```

### Ambiguous Requirements

```
The requirement "[ambiguous phrase]" could mean:
1. [Interpretation A] — [implication]
2. [Interpretation B] — [implication]

Which interpretation is correct? Or is it something else?
```

### Missing Context

```
The issue doesn't explain why this work is needed.

Context helps me make better implementation decisions. Is this:
- A bug fix for a reported issue?
- A feature requested by a specific user/team?
- Technical debt cleanup?
- A dependency for other work?
```

### Vague Success Criteria

```
"[vague criterion like 'make it fast']" is subjective.

Could you define a measurable target? For example:
- Page load time under X ms (currently Y ms)
- Reduce API response from X to Y
- Handle Z concurrent users without errors
```

### Missing Implementation Hints

```
The issue describes what to do but not where. In a codebase of this size, that matters.

Do you know which files/modules are involved? For example:
- Which component/page needs to change?
- Which API/service handles this?
- Is there a similar feature I can reference for patterns?
```

## When to Ask vs Proceed

| Situation | Decision | Rationale |
|-----------|----------|-----------|
| Missing AC | ASK | Without AC, can't verify completion |
| Missing scope | ASK | Risk of scope creep or missing requirements |
| Missing context | PROCEED (flag) | Context helps but isn't always blocking |
| Ambiguous requirement | ASK | Two implementations = one is wrong |
| Subjective criterion | ASK | Can't objectively verify completion |
| Missing code pointers | PROCEED | Will search codebase; may ask if search fails |
| One vague sentence | ASK | Too little to act on |
| Structured but thin | PROCEED (flag) | Enough to start; flag uncertainties |
| Contradictory requirements | ASK | Must resolve contradiction first |
| Outdated issue (old, many comments) | PROCEED (summarize) | May still be valid; summarize current understanding |
| Issue references external doc | FETCH then PROCEED | Read the doc first, then assess |

## Framing Clarifications Constructively

### Good Framing

```
"To implement this, I need to understand:
1. [Specific question]
2. [Specific question]

What I can determine from the issue:
- [Clear fact 1]
- [Clear fact 2]

If you don't have answers to these, I'd assume:
- [Reasonable default assumption] — is that OK?"
```

### Bad Framing

```
"This issue is unclear. What do you want me to do?"

"It doesn't explain anything. Please add more detail."
```

Good framing is specific about what's missing, what IS clear, and what the default assumption would be. It shows you've engaged with the issue rather than rejecting it.

## Integration with Behavioral Guidelines

### From general-guidelines

**Think Before Acting:** Apply this to every issue. Before writing code:
- State assumptions explicitly
- If multiple interpretations exist, present them
- If something is unclear, name what's confusing

**Simplicity First:** When requirements are vague, don't build the most elaborate interpretation. Propose the simplest reasonable interpretation and ask if that's sufficient.

### From karpathy-coding-guidelines

**Surgical Changes:** When the issue is vague about scope, default to the smallest change that could satisfy the requirements. Don't expand scope to "improve things while I'm there."

**Goal-Driven Execution:** Transform vague AC into verifiable goals. "Make it fast" → "Reduce page load time by 50% (from X to Y ms)".

## Escalation: When the Issue Needs Updating

If the issue is fundamentally underspecified (missing requirements, no AC, contradictory information), suggest the issue itself should be updated:

```
"This issue could benefit from more detail before implementation.

Suggested additions:
- Acceptance Criteria section with 2-3 verifiable criteria
- Scope section clarifying what's included/excluded
- [Other missing section]

I can update PROJ-42 with these if you tell me what to add."
```

## When to NOT Ask

Don't ask for clarification when:
- The answer is obvious from context (the issue is part of an epic that provides the context)
- The answer is discoverable (can be found by reading the codebase or linked issues)
- The ambiguity doesn't affect implementation (minor wording ambiguity with one clear interpretation)
- The user has signaled urgency ("just do it", "make a judgment call", "you decide")

## Example: Clarification in Practice

### Issue: "Add dark mode to settings"

**Analysis:**
- Type: Feature (clear)
- Description: One sentence (vague)
- Missing: AC, scope, implementation hints
- Indicators: 4 vague indicators → CLARIFY

**Response:**
```
To implement this, I need to understand:

1. Scope: Should dark mode apply to the entire app, or just the settings page?
2. Behavior: Should it follow the system preference by default, or be manual-only?
3. Persistence: Should the preference be saved per-user (backend) or per-device (localStorage)?
4. Acceptance criteria: How do we verify it's correct? Every page? Specific components?

What I can determine:
- This is a new feature for the settings area
- Likely involves a toggle component and a theme context/provider

If I had to assume:
- System preference detection with manual override
- Local storage persistence
- Global application (all pages)

Does that match your expectations?
```
