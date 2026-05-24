# Diataxis Writing Conventions

## General Conventions (Cache)

**Code Examples:**
* Use actual public API imports (NO pseudo-code).
* Show complete, runnable snippets (no fragments assuming invisible setup).
* Use the project's test framework (e.g., pytest) and target language version.
* NEVER show deprecated APIs.

**Cross-References:**
* Format: Relative Markdown links `[text](../path/file.md)`.
* Prerequisites go at the top; Next Steps go at the bottom.
* Inline link to ADRs and architecture docs where relevant.

**Adoption/Audience Levels:**
If the project uses tiered adoption (e.g., Level 1 → 5), place this exact quote block at the top of the page under the H1:
`> **[ADOPTION: Level N]** · Prerequisites: [page A](...), [page B](...)`

---

## 1. Tutorial Template & Rules
**Rules:** First-person plural ("we", "let's"). Every step produces visible, testable output. NO alternatives mid-stream. NO inline concept explanations (link instead). MUST work end-to-end.

```markdown
# [Topic] tutorial

> **Prerequisites:** [page A](...), [page B](...)
> **Time:** ~N minutes

In this tutorial, we'll build a [what] to learn [what].

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

## Step N: [Final step]
...

## What we accomplished
We built a [thing] that [does X]. You learned:
* [Key takeaway 1]

## Next steps
* [Related how-to guide](...)
* [Deeper concept explanation](...)

```

## 2. How-To Guide Template & Rules

**Rules:** Title must be "How to [verb]". User's problem perspective. NO teaching language ("you will learn"). NO concept explanation. Include judgment calls, not just mechanical actions. Use conditional imperatives ("If you want X, do Y").

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

## Expected outcome
[What the user should have after following this guide.]

## Next steps
* [Related how-to guide](...)
* [Concept page for deeper understanding](...)

## Cross-references
* **ADR-NNN**: [Brief description](link)
* **arch42 §N**: [Brief description](link)

```

## 3. Explanation (Concept) Template & Rules

**Rules:** Open with what the topic IS and WHY it exists. Admit alternatives and trade-offs (not just happy path). Make explicit connections to other concepts. Warn about pitfalls. Link design decisions to ADRs. NO step-by-step instructions.

```markdown
# [Topic]

> **[ADOPTION: Level N]** · Prerequisites: [page A](...), [page B](...)

[Opening paragraph: what this topic is, why it exists, what need it addresses.]

## How it works
[Explanation of the mechanism, design, or pattern. Include context/rationale.]

## Design decisions
[Why specific choices were made. Link to ADRs.]
> **📌 ADR-NNN**: [Brief description of the decision and consequences.](link)

## Relationship to other concepts
[How this connects to related topics. Dependencies.]

## Common pitfalls
> **⚠️** [Pitfall description and why it happens.]

## Next steps
* [Related concept page](...)
* [How-to guide for using this](...)

```

---

## Formatting Reference

**Cross-Reference Callouts:**

* **ADRs:** `> 📌 ADR-001: [Protocol over ABC...](../adr/ADR-001-slug.md)`
* **arch42:** `> 🔗 arch42 §5.2: Building block details`
* **External:** `[Pydantic v2 docs](https://docs.pydantic.dev/latest/)`

**Index Page Template (`_index.md`):**

```markdown
# [Section name]

[One-sentence description of what this section covers.]

* **[Page title](page-file.md)**: One-line summary of what the page covers.
* **[Page title](page-file.md)**: One-line summary of what the page covers.

```

*(Note: Use bullet points for index files instead of Markdown tables for better readability and token efficiency).*
