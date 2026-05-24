---
name: diataxis-writer
description: >
  Generate and maintain Diataxis-structured docs. Use this skill when the user wants
  to create, set up, update, audit, or lint documentation. Trigger on mentions of:
  "Diataxis", "docs folder", "write docs", "document this feature", "fix links",
  "convert links to Obsidian/Confluence/YouTrack", or "documentation structure".
---
# Diataxis Documentation Writer

## Core Framework & References (Cache)
Apply the [Diataxis](https://diataxis.fr/) framework: **Strictly ONE mode per page.**
* **Tutorial:** Learning ("Getting started with X")
* **How-To:** Task/goal ("How to [verb]")
* **Reference:** Information ("[Component] reference")
* **Explanation:** Understanding ("Understanding X")

**Mandatory Reference Files:**
Always load the relevant reference file into your context before writing or formatting:
* `@references/diataxis-principles.md` → Load for **The Compass** (mode selection) and strict titling/anti-pattern constraints.
* `@references/writing-conventions.md` → Load for **Markdown Templates**, adoption level formatting, and cross-reference syntax.
* `@references/link-types.md` → Load for **Conversion Algorithms** (GitHub ↔ Obsidian ↔ Confluence ↔ YouTrack).

## Diagramming Integration
Trigger the `diagramming` skill (≤20 elements per diagram) when beneficial:
* **Mermaid Flowchart/Class:** Architecture, sub-system overviews.
* **Mermaid Flowchart:** Process flow, decision points.
* **Mermaid Sequence:** Multi-step tutorial workflows.
* **Mermaid ER:** Reference data models.
* **Excalidraw/Mermaid Mind Map:** Complex explanations.
* *Embedding:* Put ```mermaid``` blocks directly in Markdown. Save Excalidraws to `docs/diagrams/` and link relative.

---

## Capabilities & Workflows

**1. Initialize Structure**
* Run: `python ${CLAUDE_SKILL_DIR}/scripts/init_structure.py --target-dir docs --project-dir .`
* Refine generated `introduction.md` using `pyproject.toml`, `package.json`, and `README.md`.
* Locate any project blueprint (e.g., `docs/others/diatiaxis-library-template.md`) for the next step.

**2. Fill In Documentation**
* **Discover:** Read blueprint file (authoritative) or parse source modules for public APIs.
* **Prioritize:** Low adoption level → dependency chain → user impact.
* **Write:** Select mode via the Compass (`@references/diataxis-principles.md`). Use exact templates (`@references/writing-conventions.md`). Add to `_index.md`.

**3. New Features**
* **Analyze:** Identify new public API surfaces.
* **Write:** 1 new concept requires 1 Explanation page. 1 new user action requires 1 How-To page. Update Tutorials if beginner flow changes.
* **Update:** Add ADR references, cross-links, and update "Next steps" in existing pages.

**4. Update Existing Docs**
* **Trace:** Find pages referencing changed modules/classes.
* **Assess:** Update API signatures, behavioral descriptions, and prerequisites. Add deprecation notices.
* **Surgical Updates:** Do not blend modes during updates. Re-verify cross-references.

**5. Lint & Maintain**
* Run: `python ${CLAUDE_SKILL_DIR}/scripts/lint_docs.py --docs-dir docs` (Options: `--strict`, `--skip-mode-check`).
* **Fix Priority:** Broken links → missing `_index.md` → empty sections → orphans → mode warnings.
* **Manual Checks:** Concept/How-to sync, functional tutorials, compiling code examples, valid ADR links.

**6. Convert Links**
* **Review:** Load `@references/link-types.md` for target format syntax and algorithms.
* Formats: `github` ↔ `obsidian`, `github` → `confluence` (needs `--base-url`), `github` → `youtrack` (needs `--youtrack-url`).
* Run: `python ${CLAUDE_SKILL_DIR}/scripts/convert_links.py --from <fmt> --to <fmt> --docs-dir docs/`
* *Always* use `--dry-run` first. Lint immediately after conversion.
