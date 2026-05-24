---
name: adr-writer
description: >
  Create and manage Architecture Decision Records (ADRs). Trigger when the user wants to
  create/write/record/document an architecture/design decision, mentions "ADR-NNN",
  "record this decision", "document the choice", or needs to formally capture a design discussion.
---
# ADR Writer Skill

**Workflow:** Determine Number → Gather Inputs → Create File → Update README → Update Arch42 → Verify.

## Core Templates & Link Rules (Cache)
* **ADR Template:** Use the base template at `@references/adr_template.md`.
* **README Links:** Relative to same directory (e.g., `[ADR-NNN](ADR-NNN-slug.md)`).
* **Arch42 Links:** Relative to parent directory (e.g., `[ADR-NNN](../adr/ADR-NNN-slug.md)`).
* **Code/Test References:** Path relative to project root (e.g., `src/<proj_name>/...`).

---

## 1. Determine Number & Gather Inputs
* **Find Next NNN:** Run `ls docs/adr/ADR-*.md | grep -oP 'ADR-\K\d+' | sort -n | tail -1`. Next = `Max + 1` (zero-padded to 3 digits). Track any superseded numbers.
* **Ask User For:**
  1. **Title:** One-line title.
  2. **Category:** Base/Foundational, DDD, CQRS, Saga, Event Sourcing, Infra, Cross-Cutting, or new.
  3. **Content:** Context, Decision, Alternatives, Consequences. (Fallback: use `Proposed` status with `TBD` placeholders).
  4. **Supersedes/Deprecates:** ADR-NN it replaces/deprecates (optional).
  5. **Arch42 Narrative:** Default Yes. Skip only for purely procedural/minor items.
  6. **Standalone vs. Group:** Recommend Standalone if: (a) specialized topic, (b) number falls outside a group's historical range, (c) user preference.

## 2. Create the ADR File
* **Filename:** `docs/adr/ADR-{NNN}-{kebab-case-title}.md`
* **Conventions:**
  * **Decision:** Active voice ("We will...").
  * **Alternatives:** Provide at least 3 alternatives (fewer only if very narrow).
  * **Status:** `{Proposed | Accepted | Accepted — Supersedes ADR-NN}`.

## 3. Update `docs/adr/README.md`
**A. Add Index Entry:**
* *Group Section:* Insert row numerically: `| [ADR-{NNN}](ADR-{NNN}-{slug}.md) | {Title} | {Status} | {Date} |`
* *Standalone Section:* Create header `## {Parent Module} — {Short Topic} (ADR-{NNN})` *before* `## Superseded`. Add table/row beneath it.
* **CRITICAL:** Do NOT modify historical parenthetical ranges in existing group headers (e.g., `(ADR-014 – ADR-026)`).

**B. Handle Superseded / Deprecated (3-Part Update):**
1. **README Original Table:** Change old ADR's status to `Superseded by ADR-{NNN}` or `Deprecated`.
2. **README Bottom Sections:** Append to `## Superseded` or `## Deprecated` tables: `| [ADR-NN](ADR-NN-slug.md) | {Original Section} | {Superseded by ADR-{NNN} | Deprecated} | {Date} |`
3. **Old ADR File:** Update the `# Status` line inside `ADR-NN-slug.md` to match.

## 4. Update `docs/arch42/09-design-decisions.md`
* **Count:** Find `## ADR Reference — All {N} Decisions` and increment `{N}`. Verify by counting files.
* **Narrative (9.x):** If warranted, insert *before* the Mapping heading. Start with `---` (newline) `## 9.{x} {Short Title}`, followed by Context, Decision, Rationale, Consequences.
* **Mapping Table:** Insert row numerically: `| 9.{x} {Short Title} | [ADR-{NNN}](../adr/ADR-{NNN}-{slug}.md) |`
* **Reference Table:** Insert row numerically into existing group OR create a new `### {Short Topic} ({NNN})` standalone header. Row: `| [ADR-{NNN}](../adr/ADR-{NNN}-{slug}.md) | {Title} |`

## 5. Edge Cases
* **Superseding an already-superseded ADR:** Update chain (old file says `Superseded by NNN`). Note history in new ADR Context.
* **Multiple ADRs in one session:** Process strictly one at a time. Re-determine next number for each.
* **Collision:** If ADR-NNN file already exists, STOP and alert user. Do not overwrite.
* **Deprecating AND Superseding:** Mutually exclusive. Superseding wins.

## 6. Verification Checklist
* File is valid markdown, correctly named. Links use correct relative paths (same dir vs `../adr/`).
* Superseding/Deprecating correctly applied across all 3 locations.
* Arch42 count matches file count. README group headers remain unmodified. New number has no collisions.
