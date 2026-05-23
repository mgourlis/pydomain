# Diataxis Writing Conventions

## General Conventions (All Modes)

### Code Examples
- Use the project's actual public API imports -- never simplified or pseudo-code
- Show complete, runnable snippets -- not fragments that assume invisible setup
- Use the project's test framework and conventions (e.g., pytest, pytest-anyio)
- All code targets the project's language version and dependencies
- Never show deprecated APIs or patterns from older versions

### Cross-References
- Link to prerequisite pages at the top ("Prerequisites: ...")
- Link to related pages at the bottom ("Next steps: ...")
- Use inline cross-references to ADRs, architecture docs, and related concept pages
- Format: relative Markdown links (default) -- `[text](../path/file.md)`

### Adoption / Audience Levels
If the project has tiered adoption levels (e.g., Level 1 basic → Level 5 advanced), indicate the target level at the top of each page:
```
> **[ADOPTION: Level N]** · Prerequisites: [page A](...), [page B](...)
```

---

## Tutorial Template

```markdown
# [Topic] tutorial

> **Prerequisites:** [page A](...), [page B](...)
> **Time:** ~N minutes

In this tutorial, we'll build a [what you'll build] to learn [what you'll learn].

## Step 1: [First concrete action]

First, [do this specific thing]:

```python
# Complete, runnable code
```

Run it and you should see:

```
expected output
```

Notice that [something worth observing].

## Step 2: [Next concrete action]

Now that we have [result from step 1], let's [next thing]:

```python
# Complete, runnable code
```

## Step N: [Final step]

...

## What we accomplished

We built a [thing] that [does X]. You learned:
- [Key takeaway 1]
- [Key takeaway 2]

## Next steps
- [Related how-to guide](...)
- [Deeper concept explanation](...)
```

### Tutorial Writing Rules
- Every step produces visible, testable output
- No alternatives or options presented mid-stream
- No inline concept explanations -- link to concept pages
- First-person plural throughout ("we", "let's")
- The tutorial MUST work end-to-end for every reader

---

## How-To Guide Template

```markdown
# How to [accomplish specific goal]

> **[ADOPTION: Level N]** · Prerequisites: [page A](...), [page B](...)

This guide shows you how to [solve a specific, real-world problem].

## 1. [First step -- setup or prerequisite action]

[Brief context if essential. No teaching.]

```python
# Code for this step
```

## 2. [Second step]

[What to do and why this step matters -- one sentence max.]

```python
# Code for this step
```

## N. [Final step -- verification or result]

...

## Expected outcome

[What the user should have after following this guide.]

## Next steps
- [Related how-to guide](...)
- [Concept page for deeper understanding](...)

## Cross-references
- **ADR-NNN**: [Brief description of relevant ADR](link)
- **arch42 §N**: [Brief description of relevant section](link)
```

### How-To Writing Rules
- Title starts with "How to" + verb
- Written from the user's problem perspective ("How to dispatch a command" not "Using the CommandBus")
- No "you will learn" or teaching language
- No explanation of concepts -- link to concept pages
- Steps include judgment calls, not just mechanical actions
- Conditional imperatives: "If you want X, do Y"

---

## Explanation (Concept) Template

```markdown
# [Topic]

> **[ADOPTION: Level N]** · Prerequisites: [page A](...), [page B](...)

[Opening paragraph: what this topic is, why it exists, what need it addresses.]

## How it works

[Explanation of the mechanism, design, or pattern. Include context and rationale.]

## Design decisions

[Why specific choices were made. Link to ADRs.]

> **📌 ADR-NNN**: [Brief description of the relevant decision and its consequences.]

## Relationship to other concepts

[How this connects to related topics. What depends on it. What it depends on.]

## Common pitfalls

> **⚠️** [Pitfall description and why it happens.]

## Next steps
- [Related concept page](...)
- [How-to guide for using this](...)

## Cross-references
- **ADR-NNN**: [Description](link)
- **arch42 §N**: [Description](link)
```

### Explanation Writing Rules
- Opens with what the topic IS and WHY it exists
- Admits alternatives and trade-offs -- not just the happy path
- Makes connections to other concepts explicitly
- Warns about common misunderstandings and pitfalls
- Links design decisions back to ADRs
- No step-by-step instructions (those belong in how-tos)

---

## Cross-Reference Formatting

### Within Diataxis docs (default: GitHub Markdown relative links)
```markdown
[Entities](../concepts/ddd/entities.md)
[How to define an aggregate](../how-to/ddd/define-aggregate.md)
```

### To ADRs
```markdown
> **📌 ADR-001**: [Protocol over ABC for interfaces](../adr/ADR-001-protocol-over-abc-for-interfaces.md)
```

### To arch42
```markdown
> **🔗 arch42 §5.2**: Building block details
```

### To External Sources
```markdown
[Python documentation](https://docs.python.org/3/library/...)
[Pydantic v2 docs](https://docs.pydantic.dev/latest/)
```

---

## Index Page Template (_index.md)

```markdown
# [Section name]

[One-sentence description of what this section covers.]

| Page | Description |
|------|-------------|
| [Page title](page-file.md) | One-line summary of what the page covers |
| ... | ... |
```
