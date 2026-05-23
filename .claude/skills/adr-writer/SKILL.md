---
name: adr-writer
description: Write new Architecture Decision Records (ADRs) in docs/adr/ following the project template, then update the ADR index in README.md and append entries to docs/arch42/09-design-decisions.md. Trigger whenever the user wants to create/write/record/document an architecture decision, design decision, or ADR; mentions "record this decision", "write up the ADR", "new decision", "ADR-NNN", or "document the choice"; or when a design discussion needs to be captured as a formal decision record.
---

# ADR Writer Skill

This skill handles three artifacts in sequence: (1) creating a new ADR markdown file in `docs/adr/`, (2) updating the ADR index in `docs/adr/README.md` (including superseded/deprecated handling), and (3) appending entries to `docs/arch42/09-design-decisions.md`.

Use this skill whenever a decision needs to be formally recorded — even when the user just says "we decided X, make an ADR".

---

## Workflow Overview

```
determine next ADR number
  → gather inputs from user
  → create ADR-NNN.md from TEMPLATE.md
  → update README.md (index + superseded/deprecated)
  → update arch42/09-design-decisions.md (narrative + mapping + reference)
  → verify all links and statuses
```

---

## Step-by-step Instructions

### Step 1: Determine the next ADR number

```bash
ls docs/adr/ADR-*.md | grep -oP 'ADR-\K\d+' | sort -n | tail -1
```

Next number = `max + 1`, zero-padded to 3 digits (e.g., `054` → `055`).

If the ADR supersedes an existing one, keep track of the superseded number for later use.

### Step 2: Gather inputs from the user

Ask the user for:

| Input | Description | Required |
|-------|-------------|----------|
| **Title** | One-line title (used in filename, headings, tables) | Yes |
| **Category** | Module/area: Base/Foundational, DDD Module, CQRS Module, Saga Subsystem, Event Sourcing Module, Infrastructure, Cross-Cutting, or new topic group | Yes |
| **Content** | Context, Decision, Alternatives Considered, Consequences, References | Ideally yes; if minimal: create as `Proposed` with `TBD` placeholders |
| **Supersedes** | ADR-NN it replaces (if any) | No |
| **Deprecates** | ADR-NN it deprecates (if any) | No |
| **Narrative in arch42** | Whether a 9.x section is warranted | Default: yes (only skip for trivially minor items) |
| **Standalone section** | Whether to create a new README section vs. add to existing group | Recommend standalone when: (a) the ADR is a specialised topic within a module, (b) the number falls outside the group's range, (c) the user prefers a dedicated section |

**Why ask the user** instead of guessing: Category and standalone-vs-group affect file paths and table placement. The user knows their domain organisation; guessing risks misplacing the entry and requiring a manual fix.

### Step 3: Create the ADR file

#### Filename

```
ADR-{NNN}-{kebab-case-title}.md
```

Example: For "Redis Distributed Caching" with number 055 → `ADR-055-redis-distributed-caching.md`

#### Content structure

Write to `docs/adr/ADR-{NNN}-{kebab-case-title}.md` using the TEMPLATE.md at `docs/adr/TEMPLATE.md`:

```markdown
# ADR-{NNN}: {Title}

## Status

{Proposed | Accepted | Accepted — Supersedes ADR-NN}

## Date

{YYYY-MM-DD}

## Context

{What is the issue motivating this decision? Include concrete examples.}

## Decision

{What is the change being proposed or enacted? Use active voice — "We will..."}

## Alternatives Considered

| Alternative | Rejection Reason |
|-------------|-----------------|
| ... | ... |

## Consequences

### Positive

- ...

### Negative

- ...

### Neutral

- ...

## References

- Source files, related ADRs (as `[ADR-NN](ADR-NN-slug.md)`), arch42 sections.
```

**Key conventions:**
- Use active voice in the Decision section — "We will..." not "It was decided to..."
- Provide at least 3 alternatives when possible (fewer are OK for very narrow decisions)
- Link related ADRs as `[ADR-042](ADR-042-event-upcaster-chain-cycle-detection.md)` (relative path within same directory)
- Reference source code paths relative to project root: `src/pydomain/es/snapshot.py`
- Include test file paths in References

### Step 4: Update `docs/adr/README.md`

The README is at `docs/adr/README.md`. It has:
- Group sections with tables (e.g., `## CQRS Module (ADR-014 – ADR-026)`)
- Standalone sections (e.g., `## Event Sourcing — Snapshot Schema (ADR-053)`)
- `## Superseded` and `## Deprecated` sections at the bottom

#### 4a. Add the index entry

**If adding to an existing group section:** Insert a new row in the existing table in numerical order. Row format:

```
| [ADR-{NNN}](ADR-{NNN}-{kebab-case-title}.md) | {Title} | {Status} | {Date} |
```

**If creating a new standalone section** (recommended when the ADR number is outside the group's range, or when it's a specialised sub-topic): Create a new `##` heading **after** all existing group sections but **before** `## Superseded`:

```
## {Parent Module} — {Short Topic} (ADR-{NNN})

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-{NNN}](ADR-{NNN}-{kebab-case-title}.md) | {Title} | {Status} | {Date} |
```

Example: `## Event Sourcing — Snapshot Schema (ADR-053)`

**Important:** Group range headers (e.g., `(ADR-014 – ADR-026)`) are historical snapshots. Do NOT modify the parenthetical range when adding a new ADR. The range reflects the original ADRs in that group, not a live boundary.

#### 4b. Handle Superseded

If the new ADR supersedes ADR-NN:

1. In the **superseded ADR's table row** in README, change status from `Accepted` to `Superseded by ADR-{NNN}`.
2. In the **Superseded section** of README:
   - If empty (`_None._`), replace with the table and header:
   ```markdown
   ## Superseded

   | ADR | Original Section | Superseded by | Date |
   |-----|-----------------|---------------|------|
   | [ADR-NN](ADR-NN-slug.md) | {Original Section name} | [ADR-{NNN}](ADR-{NNN}-{kebab-case-title}.md) | {Date} |
   ```
   - If already populated, append a row following the same format.
3. In the **superseded ADR's own file** (`ADR-NN-slug.md`), update its Status line from `Accepted` to `Superseded by ADR-{NNN}`.

**Why update the superseded ADR file?** The file should reflect its current status independently — someone reading only that file needs to know it's no longer current.

#### 4c. Handle Deprecated

Same pattern as Superseded, but with:
```markdown
| ADR | Original Section | Status | Date |
|-----|-----------------|--------|------|
| [ADR-NN](ADR-NN-slug.md) | {Original Section} | Deprecated | {Date} |
```

Update the deprecated ADR's own file Status line to `Deprecated`.

### Step 5: Update `docs/arch42/09-design-decisions.md`

The arch42 document (`docs/arch42/09-design-decisions.md`) has three update points:

#### 5a. Update the ADR count heading

Find `## ADR Reference — All {N} Decisions` and increment `{N}` by the number of new ADRs being added.

**Why:** The count must reflect the actual total. After updating, verify by counting `docs/adr/ADR-*.md` files.

#### 5b. Add a narrative section (9.x) [optional]

If the ADR warrants a narrative entry, append a new section **before the `## Section → ADR Mapping` heading**. Follow the existing narrative format:

```markdown
---

## 9.{x} {Short Title}

### Context

{Problem being addressed. What forces, constraints, or requirements motivated this decision?}

### Decision

{What was decided. Active voice.}

### Rationale

{Why this option over alternatives. Comparison tables work well here.}

### Consequences

{Impact — positive, negative, or trade-offs introduced.}

---
```

The `---` separator is required before the section to align with the existing document style.

**When to skip:** If the ADR is purely procedural, a narrow policy change, or the user explicitly says no narrative section is needed. Index-only ADRs (like listing third-party tools) also don't warrant a narrative section.

#### 5c. Add to Section → ADR Mapping

Insert a row in the mapping table, keeping numerical order:

```
| 9.{x} {Short Title} | [ADR-{NNN}](../adr/ADR-{NNN}-{kebab-case-title}.md) |
```

Note the `../adr/` prefix — this file is in `docs/arch42/`, one level above `docs/adr/`.

#### 5d. Add to ADR Reference

**If adding to an existing group** (e.g., `### Infrastructure (044–049)`): Insert a row in numerical order. The table has two columns: `ADR` and `Title`.

```
| [ADR-{NNN}](../adr/ADR-{NNN}-{kebab-case-title}.md) | {Title} |
```

**If creating a new standalone section:** Add a new `###` heading before `## Section → ADR Mapping`:

```
### {Short Topic} ({NNN})

| ADR | Title |
|-----|-------|
| [ADR-{NNN}](../adr/ADR-{NNN}-{kebab-case-title}.md) | {Title} |
```

Example: `### Snapshot Schema (053)`

### Step 6: Verify

Run through this checklist after all changes:

- [ ] ADR file is valid markdown, follows TEMPLATE.md structure
- [ ] Filename matches `ADR-{NNN}-{kebab-case-title}.md`
- [ ] README links are relative (no `../adr/` — same directory)
- [ ] arch42 links use `../adr/` prefix (one level up)
- [ ] If superseding: status updated in (a) README original table, (b) README Superseded section, (c) superseded ADR's own file
- [ ] If deprecating: status updated in (a) README Deprecated section, (b) deprecated ADR's own file
- [ ] arch42 section count heading reflects total ADR count
- [ ] Group range headers in README are NOT modified (they're historical)
- [ ] New ADR number doesn't appear anywhere else in the codebase by accident

---

## Edge Cases

| Situation | Handling |
|-----------|----------|
| **Superseding an already-superseded ADR** | Update the chain: the old ADR's file now shows `Superseded by ADR-{NNN}` (chain of supersession). In the new ADR's Context, note the history. |
| **No user-provided content** | Create with `Proposed` status and `TBD — to be filled` placeholders. Still create all structural artifacts (file, README entry, arch42 header). |
| **Multiple ADRs in one session** | Create one at a time, re-determining the next number each time. |
| **ADR number already exists** | Alert the user — a file collision means either the numbers are out of sync or this decision was already documented. Do not overwrite. |
| **Deprecating AND superseding** | They are mutually exclusive in practice: deprecation means "don't use this" and superseding means "use this instead." If both apply, use supersession only. |

---

## Templates Reference

### README table row (in existing group)

```
| [ADR-{NNN}](ADR-{NNN}-kebab-title.md) | {Title} | {Status} | {Date} |
```

### README standalone section

```
## {Parent Module} — {Topic} (ADR-{NNN})

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [ADR-{NNN}](ADR-{NNN}-kebab-title.md) | {Title} | {Status} | {Date} |
```

### README Superseded row

```
| [ADR-NN](ADR-NN-slug.md) | {Original Section} | [ADR-{NNN}](ADR-{NNN}-kebab-title.md) | {Date} |
```

### README Deprecated row

```
| [ADR-NN](ADR-NN-slug.md) | {Original Section} | Deprecated | {Date} |
```

### arch42 narrative section

```
---

## 9.{x} {Short Title}

### Context

...

### Decision

...

### Rationale

...

### Consequences

...

---
```

### arch42 Section → ADR Mapping row

```
| 9.{x} {Short Title} | [ADR-{NNN}](../adr/ADR-{NNN}-kebab-title.md) |
```

### arch42 ADR Reference row (existing group)

```
| [ADR-{NNN}](../adr/ADR-{NNN}-kebab-title.md) | {Title} |
```

### arch42 ADR Reference standalone section

```
### {Topic} ({NNN})

| ADR | Title |
|-----|-------|
| [ADR-{NNN}](../adr/ADR-{NNN}-kebab-title.md) | {Title} |
```
